"""Arbre des dossiers de la colonne de gauche : hiérarchie du serveur, parents fantômes, repli, ordre choisi par glisser-déposer, affichage."""
import asyncio
from datetime import datetime

import pytest
from nicegui import events, ui
from nicegui.testing import User

import main
from mailia import db, folder_order, folder_tree, sync


def rows(*specs, account="testbox"):
    """(clé, chemin, rôle, suivi) -> lignes comme db.folders()."""
    return [{"key": k, "path": p, "account": account, "sync": s, "name": p.rsplit("/", 1)[-1], "role": r} for k, p, r, s in specs]


# la boîte Exchange réelle : la boîte de réception et ses sous-dossiers, les dossiers système, Archive et ses années
EXCHANGE = rows(("INBOX", "Boîte de réception", "inbox", 1), ("adm", "Boîte de réception/Administratif", "", 1), ("jur", "Boîte de réception/Juridique", "", 1),
                ("dr", "Brouillons", "drafts", 1), ("sent", "Éléments envoyés", "sent", 1), ("arch", "Archive", "archive", 1),
                ("a22", "Archive/2022", "", 1), ("a23", "Archive/2023", "", 1), ("a24", "Archive/2024", "", 1), ("a24s", "Archive/2024/Eléments Envoyés", "", 0),
                ("junk", "Courrier indésirable", "junk", 1), ("trash", "Éléments supprimés", "trash", 1), ("tr", "A traiter", "", 1), ("blk", "Blocked", "", 0))


def names(nodes):
    return [(n["key"], n["depth"]) for n in nodes]


def test_tree_mirrors_the_server_with_the_inbox_children_under_the_inbox():
    out = names(folder_tree.build(EXCHANGE, [], set()))
    assert out == [("INBOX", 0), ("adm", 1), ("jur", 1),  # la boîte de réception et SES sous-dossiers
                   ("dr", 0), ("sent", 0), ("arch", 0), ("a22", 1), ("a23", 1), ("a24", 1),  # puis les dossiers système, Archive et ses années
                   ("junk", 0), ("trash", 0), ("tr", 0)]  # puis la corbeille, et les autres dossiers par ordre alphabétique ; les non suivis sont absents


def test_unfollowed_parent_becomes_a_ghost_row_so_the_hierarchy_stays_readable():
    fl = rows(("INBOX", "Boîte de réception", "inbox", 1), ("sync", "Problèmes de synchronisation", "", 0), ("conf", "Problèmes de synchronisation/Conflits", "", 1))
    nodes = folder_tree.build(fl, [], set())
    assert [(n["key"], n["ghost"], n["depth"]) for n in nodes] == [("INBOX", False, 0), ("ghost:Problèmes de synchronisation", True, 0), ("conf", False, 1)]
    assert nodes[1]["name"] == "Problèmes de synchronisation" and nodes[1]["kids"]
    deep = rows(("INBOX", "Boîte de réception", "inbox", 1), ("x", "A/B/C", "", 1))  # parents qui n'existent pas du tout : fantômes aussi
    assert [(n["name"], n["ghost"]) for n in folder_tree.build(deep, [], set())][1:] == [("A", True), ("B", True), ("C", False)]


def test_imap_subfolders_of_the_inbox_go_under_the_inbox():
    fl = rows(("INBOX", "Boîte de réception", "inbox", 1), ("INBOX.2015", "INBOX/2015", "", 1), ("INBOX.2016", "INBOX/2016", "", 1), ("Sent", "Objets envoyés", "sent", 1))
    assert names(folder_tree.build(fl, [], set())) == [("INBOX", 0), ("INBOX.2015", 1), ("INBOX.2016", 1), ("Sent", 0)]


def test_folding_hides_descendants_but_keeps_the_open_folder_and_sums_the_unread():
    out = folder_tree.build(EXCHANGE, [], {"testbox|arch"}, account="testbox")
    assert [n["key"] for n in out if n["key"] in ("arch", "a22", "a23", "a24")] == ["arch"]  # années repliées
    arch = next(n for n in out if n["key"] == "arch")
    assert not arch["open"] and arch["kids"] and set(arch["below"]) == {"a22", "a23", "a24"}  # non lus de dessous à additionner
    seen = folder_tree.build(EXCHANGE, [], {"testbox|arch"}, selected="a23", account="testbox")
    assert [n["key"] for n in seen if n["key"].startswith("a2")] == ["a23"]  # le dossier qu'on regarde reste visible
    assert [n["key"] for n in folder_tree.build(EXCHANGE, [], {"testbox|INBOX"}, account="testbox")][:2] == ["INBOX", "dr"]  # la boîte de réception se replie aussi
    assert all(n["open"] for n in folder_tree.build(EXCHANGE, [], set(), account="testbox"))


def test_chosen_order_applies_among_siblings_and_unranked_come_after():
    order = ["tr", "jur", "adm", "a24", "a22"]
    out = [n["key"] for n in folder_tree.build(EXCHANGE, order, set())]
    assert out[:3] == ["INBOX", "jur", "adm"]  # la boîte de réception reste toujours la première ; ses enfants dans l'ordre choisi
    assert out.index("tr") < out.index("dr")  # « A traiter » remonté devant les dossiers système
    assert out.index("arch") < out.index("a24") < out.index("a22") < out.index("a23")  # les années réordonnées, la non classée à la fin


def test_move_before_after_and_refusals():
    base = folder_order.move(EXCHANGE, [], "jur", "adm", after=False)
    assert [n["key"] for n in folder_tree.build(EXCHANGE, base, set())][:3] == ["INBOX", "jur", "adm"]
    nxt = folder_order.move(EXCHANGE, base, "a24", "a22", after=False)
    out = [n["key"] for n in folder_tree.build(EXCHANGE, nxt, set())]
    assert out.index("a24") < out.index("a22") < out.index("a23") and out.index("jur") < out.index("adm")  # le premier réordonnancement est conservé
    assert folder_order.move(EXCHANGE, [], "a22", "adm", False) is None  # un sous-dossier ne quitte pas son parent : le serveur n'est pas touché
    assert folder_order.move(EXCHANGE, [], "INBOX", "dr", False) is None and folder_order.move(EXCHANGE, [], "dr", "INBOX", True) is None
    assert folder_order.move(EXCHANGE, [], "jur", "jur", True) is None and folder_order.move(EXCHANGE, [], "inconnu", "adm", True) is None
    assert folder_order.move(EXCHANGE, [], "blk", "dr", True) is None  # un dossier non suivi n'est pas affiché : rien à déplacer


def test_save_load_reset_and_backup():
    from mailia import backup
    folder_order.save("testbox", ["jur", "adm"])
    folder_order.save("autre", ["x"])
    assert folder_order.load("testbox") == ["jur", "adm"] and folder_order.load("vide") == []
    assert backup.export_data()["kv"]["folder_order:testbox"]
    folder_order.reset_all()
    assert folder_order.load("testbox") == [] and folder_order.load("autre") == []
    db.kv_set("folder_order:testbox", "pas du json")
    assert folder_order.load("testbox") == []


# ---------- interface ----------
@pytest.fixture
def box(monkeypatch):
    monkeypatch.setattr(sync, "run_cycle", lambda: None)
    ui.page("/")(main.index_page)
    db.kv_set("brief_day", datetime.now().strftime("%Y-%m-%d"))
    with db.conn() as c:
        for f in EXCHANGE:
            c.execute("INSERT OR REPLACE INTO folders(account,key,name,path,role,sync) VALUES('testbox',?,?,?,?,?)", (f["key"], f["name"], f["path"], f["role"], f["sync"]))
    seen = []
    real = main.Mailbox.__init__
    monkeypatch.setattr(main.Mailbox, "__init__", lambda self, *a, **k: (real(self, *a, **k), seen.append(self))[0])
    return seen


def folder_rows(user):
    """(clé, retrait) des dossiers dans l'ordre réellement affiché."""
    first = next(e for e in user.find(kind=ui.element).elements if e.props.get("data-fkey") and e.props.get("data-acct") == "testbox")
    return [(e.props["data-fkey"], e._style.get("padding-left")) for e in first.parent_slot.parent.default_slot.children if e.props.get("data-fkey")]


def fire(user, element, event="click.stop"):
    with user.client:
        for listener in element._event_listeners.values():
            if listener.type == event:
                events.handle_event(listener.handler, events.GenericEventArguments(sender=element, client=user.client, args=None))


async def test_sidebar_is_a_tree_with_the_inbox_children_indented_under_the_inbox(user: User, box) -> None:
    await user.open("/")
    await user.should_see("Administratif")
    keys = [k for k, _ in folder_rows(user)]
    assert keys == ["adm", "jur", "dr", "sent", "arch", "a22", "a23", "a24", "junk", "trash", "tr"]  # ni « A traiter » avant Archive, ni sous-dossiers de la boîte à la fin
    pad = dict(folder_rows(user))
    assert pad["adm"] == "28px" and pad["jur"] == "28px"  # sous la boîte de réception
    assert pad["arch"] is None and pad["dr"] is None  # dossiers du serveur au premier niveau : alignés à gauche
    assert pad["a22"] == "28px"  # les années, sous Archive
    await user.should_see(marker="ffold-testbox-INBOX")  # la boîte de réception a sa flèche, comme Archive
    await user.should_see(marker="ffold-testbox-arch")
    await user.should_not_see(marker="ffold-testbox-dr")


async def test_account_with_only_an_inbox_stays_a_single_row(user: User, box, monkeypatch) -> None:
    with db.conn() as c:
        c.execute("DELETE FROM folders")
        c.execute("INSERT INTO folders(account,key,name,path,role,sync) VALUES('testbox','INBOX','Boîte de réception','Boîte de réception','inbox',1)")
    await user.open("/")
    await user.should_see("Boîte de test")
    assert not folder_rows_exist(user)
    await user.should_not_see(marker="fold-testbox")  # ni en-tête ni flèche : la ligne de la boîte est sa boîte de réception, sans ligne en double


def folder_rows_exist(user):
    return [e for e in user.find(kind=ui.element).elements if e.props.get("data-fkey")]


async def test_folding_the_inbox_hides_its_children_and_adds_up_their_unread(user: User, box) -> None:
    from tests.conftest import add_message
    add_message(mid="testbox|adm|1", subject="Pour l'admin", folder="adm", is_read=0)
    add_message(mid="testbox|adm|2", subject="Autre", folder="adm", is_read=0)
    b = box[-1] if box else None
    await user.open("/")
    b = box[-1]
    (arrow,) = user.find(marker="ffold-testbox-INBOX").elements
    fire(user, arrow)
    await asyncio.sleep(0.4)
    assert [k for k, _ in folder_rows(user)][:3] == ["dr", "sent", "arch"]  # Administratif et Juridique ont disparu
    assert "testbox|INBOX" in b.folders_folded()
    assert any("2 non lu(s) dans les sous-dossiers" in str(getattr(e, "_text", "")) for e in user.find(kind=ui.tooltip).elements)  # les non lus cachés sont additionnés
    assert any(str(getattr(e, "text", "")) == "+2" for e in user.find(kind=ui.label).elements)  # et affichés « +2 » sur la ligne repliée, à côté de son propre décompte


async def test_dropping_a_folder_reorders_it_among_its_siblings(user: User, box) -> None:
    await user.open("/")
    await user.should_see("Juridique")
    box[-1].folder_dropped({"detail": {"from": "jur", "after": False}}, "testbox", "adm")
    await asyncio.sleep(0.4)
    assert [k for k, _ in folder_rows(user)][:2] == ["jur", "adm"] and folder_order.load("testbox")[:2] == ["jur", "adm"]
    box[-1].folder_dropped({"detail": {"from": "a22", "after": True}}, "testbox", "adm")  # d'un autre niveau : refusé
    assert "a22" not in folder_order.load("testbox")
    el = next(e for e in user.find(kind=ui.element).elements if e.props.get("data-fkey") == "jur")
    assert el.props["draggable"] == "true" and el.props["data-parent"] == "Boîte de réception"


async def test_a_ghost_parent_is_shown_greyed_and_only_folds(user: User, box) -> None:
    with db.conn() as c:
        c.execute("INSERT OR REPLACE INTO folders(account,key,name,path,role,sync) VALUES('testbox','conf','Conflits','Problèmes de synchronisation/Conflits','',1)")
        c.execute("INSERT OR REPLACE INTO folders(account,key,name,path,role,sync) VALUES('testbox','sy','Problèmes de synchronisation','Problèmes de synchronisation','',0)")
    await user.open("/")
    await user.should_see("Problèmes de synchronisation")
    await user.should_see("Conflits")
    (arrow,) = user.find(marker="ffold-testbox-ghost:Problèmes de synchronisation").elements  # le parent non suivi se replie comme les autres
    fire(user, arrow)
    await asyncio.sleep(0.4)
    await user.should_not_see("Conflits")
