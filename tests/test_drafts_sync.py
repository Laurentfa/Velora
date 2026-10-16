"""Brouillons copiés sur le serveur (dossier Brouillons) : copie, remplacement, suppression, hors ligne, nettoyage, ouverture dans l'éditeur."""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from nicegui import ui
from nicegui.testing import User

import main
from mailia import db, outgoing, sync
from tests.conftest import add_message


class FakeServer:
    """Joue le serveur : garde les brouillons copiés, comme le dossier Brouillons d'une boîte."""
    def __init__(self):
        self.items, self.n, self.calls, self.fail = {}, 0, [], False

    def save_draft(self, d, files, ref, folder_key):
        self.calls.append(("save", ref, d["subject"], [f[0] for f in files], folder_key))
        if self.fail:
            raise OSError("pas de réseau")
        self.n += 1
        new = f"srv{self.n}"
        self.items[new] = {**d, "files": list(files)}
        self.items.pop(ref, None)
        return new

    def delete_draft(self, ref, folder_key):
        self.calls.append(("delete", ref))
        if self.fail:
            raise OSError("pas de réseau")
        self.items.pop(ref, None)

    def load_draft(self, ref, folder_key):
        d = self.items[ref]
        return {"to": d["to"], "cc": d["cc"], "bcc": d["bcc"], "subject": d["subject"], "html": d["html"], "files": d["files"]}


@pytest.fixture
def server(monkeypatch):
    srv = FakeServer()
    monkeypatch.setattr(sync, "provider", lambda key: srv)
    with db.conn() as c:
        c.execute("INSERT OR REPLACE INTO folders(account,key,name,path,role,sync) VALUES('testbox','Drafts','Brouillons','Brouillons','drafts',0)")
    return srv


def new_draft(did="d1", subject="Devis", html="<p>Bonjour</p>", to="client@exemple.fr", files=None, api=False):
    db.draft_save({"id": did, "account": "testbox", "is_new": True, "reply_to": None, "to": to, "cc": "", "bcc": "", "subject": subject, "html": html}, files or [])
    if api:
        db.draft_mark_api(did, "cle", "Créé par un assistant")
    return did


def row(did="d1"):
    return db.draft_load(did)[0]


def test_a_new_draft_is_copied_to_the_server_and_the_drafts_folder_becomes_visible(server):
    new_draft(files=[("a.txt", b"x", "text/plain")])
    assert db.drafts_count() == 1  # pas encore copié : visible dans la vue « Brouillons à synchroniser »
    assert sync.mirror_drafts() == 1
    r = row()
    assert r["server_ref"] == "srv1" and r["server_at"] == r["updated"] and r["server_folder"] == "Drafts"
    assert server.calls == [("save", None, "Devis", ["a.txt"], "Drafts")]
    assert db.folders("testbox")[0]["sync"] == 1 and db.kv_get("drafts_auto:testbox") == "1"  # le dossier Brouillons est suivi : on y retrouve le brouillon
    assert db.drafts_count() == 0  # il a quitté la vue : il est dans le dossier Brouillons de sa boîte
    assert sync.mirror_drafts() == 0 and len(server.calls) == 1  # rien n'a changé : aucune nouvelle copie


def test_an_edited_draft_replaces_its_copy_instead_of_piling_up(server):
    new_draft()
    sync.mirror_drafts()
    db.draft_save({"id": "d1", "account": "testbox", "is_new": True, "reply_to": None, "to": "client@exemple.fr", "cc": "", "bcc": "", "subject": "Devis v2", "html": "<p>Mis à jour</p>"})
    assert sync.mirror_drafts() == 1
    assert server.calls[-1][:3] == ("save", "srv1", "Devis v2") and list(server.items) == ["srv2"] and row()["server_ref"] == "srv2"


def test_assistant_empty_and_folderless_drafts_are_not_copied(server):
    new_draft("api1", api=True)
    new_draft("vide", subject="", html="", to="")
    assert sync.mirror_drafts() == 0 and not server.calls  # les brouillons des assistants restent à relire ici ; un brouillon vide n'est pas copié
    assert db.drafts_count() == 2
    with db.conn() as c:
        c.execute("DELETE FROM folders")
    new_draft("d2")
    assert sync.mirror_drafts() == 0 and not server.calls  # dossier Brouillons pas encore connu : on réessaiera


def test_without_network_nothing_is_lost_and_it_resumes_later(server):
    new_draft()
    server.fail = True
    assert sync.mirror_drafts() == 0 and row()["server_ref"] in (None, "") and db.drafts_count() == 1
    server.fail = False
    assert sync.mirror_drafts() == 1 and row()["server_ref"] == "srv1"


def test_deleting_or_sending_a_draft_removes_its_copy_later(server):
    new_draft("d1")
    new_draft("d2", subject="À envoyer")
    sync.mirror_drafts()
    db.draft_delete("d1")  # supprimé par l'utilisateur
    outgoing.queue("testbox", "client@exemple.fr", "", "", "À envoyer", "<p>x</p>", [], datetime.now(timezone.utc) + timedelta(seconds=10), draft_id="d2")  # envoyé
    assert {g["ref"] for g in db.drafts_gone()} == {"srv1", "srv2"} and set(server.items) == {"srv1", "srv2"}
    server.fail = True
    sync.mirror_drafts()
    assert len(db.drafts_gone()) == 2  # hors ligne : la suppression reste à faire
    server.fail = False
    sync.mirror_drafts()
    assert db.drafts_gone() == [] and server.items == {}


def test_an_unfollowed_drafts_folder_keeps_the_drafts_visible_here(server):
    new_draft()
    sync.mirror_drafts()
    assert db.drafts_count() == 0
    db.set_folder_sync("testbox", set())  # l'utilisateur décoche le dossier Brouillons : on ne le verrait plus nulle part
    assert db.drafts_count() == 1
    sync.mirror_drafts()
    assert db.folders("testbox")[0]["sync"] == 0  # et on ne le recoche pas d'office (choix de l'utilisateur)


def set_sync_time(minutes_after_copy):
    r = row()
    t = datetime.fromisoformat(r["server_at"]) + timedelta(minutes=minutes_after_copy)
    db.kv_set("last_sync", t.astimezone(timezone.utc).isoformat())


def test_a_draft_deleted_elsewhere_is_forgotten_but_only_when_it_is_safe(server):
    new_draft()
    sync.mirror_drafts()
    set_sync_time(1)
    assert sync.forget_vanished_drafts() == 0 and row()  # synchro trop récente : la copie n'a peut-être pas encore été vue
    set_sync_time(10)
    add_message(mid="testbox|Drafts|srv1", folder="Drafts")  # la copie est dans le dossier
    with db.conn() as c:
        c.execute("UPDATE messages SET uid='srv1' WHERE id='testbox|Drafts|srv1'")
    assert sync.forget_vanished_drafts() == 0 and row()
    with db.conn() as c:
        c.execute("DELETE FROM messages")  # supprimée dans Outlook
    db.draft_save({"id": "d1", "account": "testbox", "is_new": True, "reply_to": None, "to": "x@y.fr", "cc": "", "bcc": "", "subject": "Modifié ici", "html": "<p>x</p>"})
    assert sync.forget_vanished_drafts() == 0 and row()  # modifié depuis : on garde le travail
    db.kv_set("last_sync", datetime.now(timezone.utc).isoformat())  # (la synchro tourne en permanence : elle est toujours « maintenant »)
    sync.mirror_drafts()
    assert row() and row()["server_ref"] == "srv2"
    set_sync_time(10)
    assert sync.forget_vanished_drafts() == 1 and row() is None and db.drafts_gone() == []  # oubliée sans rien redemander au serveur


def test_a_draft_deleted_while_being_copied_does_not_leave_an_orphan(server, monkeypatch):
    new_draft()
    real = server.save_draft

    def save_then_vanish(d, files, ref, folder_key):
        out = real(d, files, ref, folder_key)
        db.draft_delete("d1")  # envoyé ou supprimé pendant la copie
        return out
    monkeypatch.setattr(server, "save_draft", save_then_vanish)
    sync.mirror_drafts()
    assert [g["ref"] for g in db.drafts_gone()] == ["srv1"]  # la copie, devenue orpheline, sera supprimée
    monkeypatch.setattr(server, "save_draft", real)
    sync.mirror_drafts()
    assert server.items == {}


def test_a_server_draft_is_loaded_for_editing_without_being_copied_again(server):
    server.items["abc"] = {"to": "a@b.fr", "cc": "", "bcc": "", "subject": "Commencé dans Outlook", "html": "<p>texte</p>", "files": [("p.pdf", b"%PDF", "application/pdf")]}
    m = {"account": "testbox", "uid": "abc", "folder": "Drafts"}
    did = sync.load_server_draft(m)
    r, files = db.draft_load(did)
    assert r["subject"] == "Commencé dans Outlook" and r["server_ref"] == "abc" and files[0][0] == "p.pdf"
    assert sync.mirror_drafts() == 0 and not [c for c in server.calls if c[0] == "save"]  # déjà à jour sur le serveur
    assert sync.load_server_draft(m) == did  # un second clic rouvre le même brouillon


# ---------- interface ----------
@pytest.fixture
def box(monkeypatch, server):
    monkeypatch.setattr(sync, "run_cycle", lambda: None)
    ui.page("/")(main.index_page)
    db.kv_set("brief_day", datetime.now().strftime("%Y-%m-%d"))
    db.set_folder_sync("testbox", {"INBOX", "Drafts"})
    seen = []
    real = main.Mailbox.__init__
    monkeypatch.setattr(main.Mailbox, "__init__", lambda self, *a, **k: (real(self, *a, **k), seen.append(self))[0])
    return seen


async def test_the_drafts_view_only_exists_while_something_is_waiting_to_be_copied(user: User, box) -> None:
    await user.open("/")
    await user.should_not_see("Brouillons à synchroniser")
    new_draft()
    await box[-1].reload(force=True)
    await asyncio.sleep(0.4)
    await user.should_see("Brouillons à synchroniser")
    sync.mirror_drafts()
    await box[-1].reload(force=True)
    await asyncio.sleep(0.4)
    await user.should_not_see("Brouillons à synchroniser")  # copié : il est dans le dossier Brouillons de la boîte


async def test_clicking_a_message_of_the_drafts_folder_opens_it_in_the_editor(user: User, box, server) -> None:
    server.items["abc"] = {"to": "a@b.fr", "cc": "c@d.fr", "bcc": "", "subject": "Commencé dans Outlook", "html": "<p>texte du brouillon</p>", "files": []}
    add_message(mid="testbox|Drafts|abc", subject="Commencé dans Outlook", folder="Drafts", body="texte du brouillon")
    with db.conn() as c:
        c.execute("UPDATE messages SET uid='abc' WHERE id='testbox|Drafts|abc'")
    await user.open("/")
    b = box[-1]
    await b.open("testbox|Drafts|abc")
    await asyncio.sleep(0.4)
    assert b.new and b.draft["subject"] == "Commencé dans Outlook" and b.draft["to"] == "a@b.fr" and b.draft["cc"] == "c@d.fr"
    await user.should_see("Envoyer")  # c'est l'éditeur, pas la lecture
    assert await b._save_draft_now() is False and sync.mirror_drafts() == 0  # ouvert sans y toucher : rien n'est réenregistré ni recopié (la mise en forme d'origine reste intacte)
    assert not [c for c in server.calls if c[0] == "save"]
