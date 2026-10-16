"""Recherche sur le serveur en plus de la copie locale : liste, import des résultats, périmètre, panne, interface."""
import asyncio
from datetime import datetime

import pytest
from nicegui import ui
from nicegui.testing import User

import main
from mailia import db, queries, sync
from mailia.providers.base import Msg
from tests.conftest import add_message


def msg(uid, subject="Sujet", date="2025-02-01T10:00:00+00:00", **kw):
    return Msg(uid=uid, subject=subject, sender="dr@example.org", sender_name="Dr", to="moi@example.org", date=date, body="", is_read=True,
               has_att=False, attachments=None, conv=f"c{uid}", html=None, **kw)


class FakeProvider:
    def __init__(self, found, fail=False):
        self.found, self.fail, self.calls = found, fail, []

    def search(self, text, folder_keys, per_folder=100):
        self.calls.append((text, list(folder_keys), per_folder))
        if self.fail:
            raise OSError("pas de réseau")
        return list(self.found)


@pytest.fixture
def fake(monkeypatch):
    def install(found, fail=False):
        prov = FakeProvider(found, fail)
        monkeypatch.setattr(sync, "provider", lambda key: prov)
        return prov
    return install


def folders(*rows):
    with db.conn() as c:
        for key, path, role in rows:
            c.execute("INSERT OR REPLACE INTO folders(account,key,name,path,role,sync) VALUES('testbox',?,?,?,?,1)", (key, path.split("/")[-1], path, role))


# ---------- liste locale ----------
def test_extra_ids_are_shown_even_outside_the_folder_and_the_text(monkeypatch):
    add_message(mid="testbox|INBOX|1", subject="Devis chaudière")
    add_message(mid="testbox|Archive|9", subject="Sans rapport", folder="Archive", date="2025-02-01T10:00:00+00:00")
    got = lambda **k: [r["id"] for r in queries.list_messages("", "chaudière", group=False, **k)]  # noqa: E731
    assert got() == ["testbox|INBOX|1"]
    assert set(got(extra_ids={"testbox|Archive|9"})) == {"testbox|INBOX|1", "testbox|Archive|9"}


def test_extra_ids_still_obey_the_other_filters():
    add_message(mid="testbox|Archive|9", subject="Ancien", folder="Archive", is_read=1)
    add_message(mid="testbox|Archive|8", subject="Ancien non lu", folder="Archive", is_read=0)
    ids = {"testbox|Archive|9", "testbox|Archive|8"}
    got = [r["id"] for r in queries.list_messages("", "zzz", unread=True, group=False, extra_ids=ids)]
    assert got == ["testbox|Archive|8"]  # « non lus » reste appliqué aux mails venus du serveur


def test_search_from_the_inbox_covers_all_folders_but_trash_junk_and_drafts():
    folders(("INBOX", "Boîte de réception", "inbox"), ("Archive", "Archive", "archive"), ("T", "Corbeille", "trash"), ("J", "Spam", "junk"), ("D", "Brouillons", "drafts"))
    for k in ("INBOX", "Archive", "T", "J", "D"):
        add_message(mid=f"testbox|{k}|1", subject=f"Rapport Lemasson {k}", folder=k)
    got = lambda **kw: sorted(r["folder"] for r in queries.list_messages("", "lemasson", group=False, **kw))  # noqa: E731
    assert got() == ["INBOX"]  # sans demande : le dossier courant seulement (comportement de l'API inchangé)
    assert got(everywhere=True) == ["Archive", "INBOX"]
    assert got(folder="Archive", everywhere=True) == ["Archive"]  # dans un dossier précis, on reste dans ce dossier
    assert got(view="todo", everywhere=True) == []  # les vues « À traiter », « Newsletters »… restent dans la boîte de réception


# ---------- import ----------
def test_search_remote_imports_only_unknown_mails_and_keeps_known_ones(fake):
    add_message(mid="testbox|INBOX|1", body="texte complet déjà chargé", flagged=1)
    prov = fake([("INBOX", msg("1", "Déjà là")), ("INBOX", msg("2", "Nouveau", "2025-03-01T10:00:00+00:00"))])
    ids = sync.search_remote("lemasson", ["testbox"], "INBOX")
    assert ids == {"testbox|INBOX|1", "testbox|INBOX|2"}
    assert prov.calls[0][0] == "lemasson"
    old = queries.get_message("testbox|INBOX|1")
    assert old["body"] == "texte complet déjà chargé" and old["flagged"] == 1  # le mail connu n'est pas écrasé par la version sans texte
    new = queries.get_message("testbox|INBOX|2")
    assert new["subject"] == "Nouveau" and new["body"] == "" and new["att_json"] is None  # texte et pièces jointes : chargés à l'ouverture


def test_search_remote_cap_keeps_the_most_recent(fake):
    fake([("INBOX", msg(str(i), date=f"2025-01-{i + 1:02d}T10:00:00+00:00")) for i in range(10)])
    ids = sync.search_remote("lemasson", ["testbox"], "INBOX", cap=3)
    assert ids == {"testbox|INBOX|9", "testbox|INBOX|8", "testbox|INBOX|7"}


def test_search_remote_survives_a_server_failure_and_short_queries(fake):
    prov = fake([], fail=True)
    assert sync.search_remote("lemasson", ["testbox"], "INBOX") == set()  # pas de réseau : on garde la recherche locale
    assert sync.search_remote("ab", ["testbox"], "INBOX") == set() and len(prov.calls) == 1  # trop court : aucune requête


def test_search_remote_skips_accounts_without_password(fake, monkeypatch):
    prov = fake([("INBOX", msg("1"))])
    monkeypatch.setattr(sync.ACCOUNTS["testbox"], "password", "")
    assert sync.search_remote("lemasson", ["testbox"], "INBOX") == set() and not prov.calls


def test_clean_query_removes_search_operators():
    assert sync.clean_query('lemasson "dominique": (test) a-b o\'neil') == "lemasson dominique test a-b o'neil"


def test_scope_current_folder_with_subfolders_like_apple_mail():
    folders(("INBOX", "Boîte de réception", "inbox"), ("c1", "Boîte de réception/Clients", ""), ("A", "Archive", "archive"),
            ("a22", "Archive/2022", ""), ("a23", "Archive/2023", ""), ("T", "Corbeille", "trash"), ("Archivage", "Archivage", ""))
    assert sync.search_scope("testbox", "A") == ["A", "a22", "a23"]  # le dossier et ses sous-dossiers, pas « Archivage »
    assert sync.search_scope("testbox", "INBOX") == ["INBOX", "c1"]
    assert "T" not in sync.search_scope("testbox", "", everywhere=True) and "a22" in sync.search_scope("testbox", "", everywhere=True)


def test_scope_imap_subfolders_by_key():
    folders(("INBOX", "Boîte de réception", "inbox"), ("INBOX.Clients", "INBOX/Clients", ""), ("INBOXX", "INBOXX", ""))
    assert sync.search_scope("testbox", "INBOX") == ["INBOX", "INBOX.Clients"]


# ---------- interface ----------
@pytest.fixture
def mailbox(monkeypatch):
    monkeypatch.setattr(sync, "run_cycle", lambda: None)
    ui.page("/")(main.index_page)
    db.kv_set("brief_day", datetime.now().strftime("%Y-%m-%d"))
    add_message(mid="testbox|INBOX|1", subject="Devis chaudière")
    seen = []
    real = main.Mailbox.__init__
    monkeypatch.setattr(main.Mailbox, "__init__", lambda self, *a, **k: (real(self, *a, **k), seen.append(self))[0])
    return seen


async def test_typing_a_search_adds_the_server_results_to_the_list(user: User, mailbox, monkeypatch) -> None:
    calls = []

    def fake_search(text, accounts, folder, everywhere):
        calls.append((text, accounts, folder, everywhere))
        add_message(mid="testbox|Archive|9", subject="Rapport ancien Lemasson", folder="Archive", date="2025-02-01T10:00:00+00:00", body="")
        return {"testbox|Archive|9"}
    monkeypatch.setattr(sync, "search_remote", fake_search)
    await user.open("/")
    await user.should_see("Devis chaudière")
    box = mailbox[-1]
    await box.set_query("lemasson")
    await user.should_not_see("Rapport ancien Lemasson")  # la recherche locale vient d'abord, sans attendre le serveur
    await asyncio.sleep(1.3)
    await user.should_see("Rapport ancien Lemasson")  # puis la liste est complétée par le serveur
    assert calls and calls[0][0] == "lemasson" and calls[0][2] == "INBOX" and calls[0][3] is True  # depuis la boîte de réception : tous les dossiers
    await box.set_query("lemasson")  # même recherche : pas de deuxième requête (mémoire de 10 minutes)
    await box.set_query("")
    await box.set_query("lemasson")
    await asyncio.sleep(0.2)
    assert len(calls) == 1


async def test_server_search_can_be_switched_off(user: User, mailbox, monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(sync, "search_remote", lambda *a: calls.append(a) or set())
    db.kv_set("server_search", "0")
    await user.open("/")
    await mailbox[-1].set_query("lemasson")
    await asyncio.sleep(1.2)
    assert not calls


async def test_a_changed_search_does_not_show_old_server_results(user: User, mailbox, monkeypatch) -> None:
    def slow(text, accounts, folder, everywhere):
        import time
        time.sleep(0.8)
        if text != "lemasson":  # le serveur ne connaît que la première recherche
            return set()
        add_message(mid="testbox|Archive|9", subject="Résultat de la première recherche", folder="Archive", body="")
        return {"testbox|Archive|9"}
    monkeypatch.setattr(sync, "search_remote", slow)
    await user.open("/")
    box = mailbox[-1]
    await box.set_query("lemasson")
    await asyncio.sleep(0.9)
    await box.set_query("autre chose")  # l'utilisateur a changé de recherche pendant l'attente du serveur
    await asyncio.sleep(2.2)
    await user.should_not_see("Résultat de la première recherche")


async def test_search_inside_a_folder_stays_in_that_folder_and_the_server_gets_only_it(user: User, mailbox, monkeypatch) -> None:
    folders(("INBOX", "Boîte de réception", "inbox"), ("Archive", "Archive", "archive"))
    calls = []
    monkeypatch.setattr(sync, "search_remote", lambda *a: calls.append(a) or set())
    await user.open("/")
    box = mailbox[-1]
    box.view, box.account, box.folder = "folder", "testbox", "Archive"
    await box.set_query("lemasson")
    await asyncio.sleep(1.3)
    assert calls and calls[0][2] == "Archive" and calls[0][3] is False
    assert not box.search_everywhere()


async def test_results_from_other_folders_show_their_folder(user: User, mailbox, monkeypatch) -> None:
    folders(("INBOX", "Boîte de réception", "inbox"), ("Archive", "Archive", "archive"), ("a25", "Archive/2025", ""))
    add_message(mid="testbox|a25|7", subject="Rapport Lemasson ancien", folder="a25", date="2025-02-01T10:00:00+00:00")
    monkeypatch.setattr(sync, "search_remote", lambda *a: set())
    await user.open("/")
    box = mailbox[-1]
    await box.set_query("lemasson")
    await user.should_see("Rapport Lemasson ancien")
    await user.should_see("2025")  # le dossier d'origine est indiqué sur la ligne
    assert "tous les dossiers" in box.title.text
