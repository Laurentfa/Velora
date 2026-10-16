"""Ordre des dossiers de la colonne de gauche : tri hiérarchique, déplacement au même niveau, mémorisation, interface."""
import asyncio
from datetime import datetime

import pytest
from nicegui import ui
from nicegui.testing import User

import main
from mailia import db, folder_order, sync


def rows(*specs):
    return [{"key": k, "path": p, "account": "testbox", "sync": 1, "name": p.rsplit("/", 1)[-1], "role": ""} for k, p in specs]


FOLDERS = rows(("a", "Archive"), ("a22", "Archive/2022"), ("a23", "Archive/2023"), ("a24", "Archive/2024"), ("adm", "Administratif"), ("jur", "Juridique"),
               ("t", "À traiter"))


def keys(sorted_rows):
    return [f["key"] for f in sorted_rows]


def test_default_order_is_alphabetical_with_children_under_their_parent():
    out = keys(folder_order.sort_folders(FOLDERS, []))
    assert out == ["adm", "a", "a22", "a23", "a24", "jur", "t"]  # Administratif, Archive et ses années, Juridique, puis « À traiter » (le À accentué vient après)
    assert out.index("a") + 1 == out.index("a22") and out.index("a24") + 1 == out.index("jur")  # rien ne s'intercale entre un parent et ses enfants


def test_chosen_order_applies_among_siblings_only():
    order = ["jur", "adm", "a", "a24", "a23", "a22"]
    out = keys(folder_order.sort_folders(FOLDERS, order))
    assert out[:3] == ["jur", "adm", "a"] and out[3:6] == ["a24", "a23", "a22"]  # Juridique, Administratif, Archive puis ses enfants dans l'ordre choisi
    assert out[-1] == "t"  # sans position choisie : après les autres de son niveau


def test_new_folders_go_after_the_ones_already_ranked():
    order = ["jur", "adm"]
    out = keys(folder_order.sort_folders(FOLDERS, order))
    assert out[:2] == ["jur", "adm"] and set(out[2:]) == {"a", "a22", "a23", "a24", "t"}


def test_move_before_and_after_a_sibling():
    base = folder_order.move(FOLDERS, [], "jur", "adm", after=False)
    assert keys(folder_order.sort_folders(FOLDERS, base))[:2] == ["jur", "adm"]
    order = folder_order.move(FOLDERS, base, "t", "adm", after=True)
    out = keys(folder_order.sort_folders(FOLDERS, order))
    assert out.index("jur") < out.index("adm") < out.index("t") < out.index("a")
    kids = keys(folder_order.sort_folders(FOLDERS, folder_order.move(FOLDERS, [], "a24", "a22", after=False)))
    assert kids[kids.index("a"):kids.index("a") + 4] == ["a", "a24", "a22", "a23"]  # les années se réordonnent sous leur parent


def test_move_refuses_other_levels_unknown_and_same_folder():
    assert folder_order.move(FOLDERS, [], "a22", "adm", False) is None  # un sous-dossier ne quitte pas son parent : le serveur n'est pas touché
    assert folder_order.move(FOLDERS, [], "adm", "a22", True) is None
    assert folder_order.move(FOLDERS, [], "jur", "jur", True) is None
    assert folder_order.move(FOLDERS, [], "inconnu", "adm", True) is None


def test_save_load_reset_and_backup():
    from mailia import backup
    folder_order.save("testbox", ["jur", "adm"])
    folder_order.save("autre", ["x"])
    assert folder_order.load("testbox") == ["jur", "adm"] and folder_order.load("vide") == []
    assert backup.export_data()["kv"]["folder_order:testbox"]  # l'ordre est dans la sauvegarde
    folder_order.reset_all()
    assert folder_order.load("testbox") == [] and folder_order.load("autre") == []
    db.kv_set("folder_order:testbox", "pas du json")
    assert folder_order.load("testbox") == []  # réglage illisible : ordre alphabétique


@pytest.fixture
def box(monkeypatch):
    monkeypatch.setattr(sync, "run_cycle", lambda: None)
    ui.page("/")(main.index_page)
    db.kv_set("brief_day", datetime.now().strftime("%Y-%m-%d"))
    with db.conn() as c:
        for k, p in (("INBOX", "Boîte de réception"), ("adm", "Administratif"), ("jur", "Juridique"), ("arch", "Archive")):
            c.execute("INSERT OR REPLACE INTO folders(account,key,name,path,role,sync) VALUES('testbox',?,?,?,?,1)", (k, p, p, "inbox" if k == "INBOX" else ""))
    seen = []
    real = main.Mailbox.__init__
    monkeypatch.setattr(main.Mailbox, "__init__", lambda self, *a, **k: (real(self, *a, **k), seen.append(self))[0])
    return seen


def displayed(user):
    return [e.props["data-fkey"] for e in user.find(kind=ui.element).elements if e.props.get("data-fkey") and e.props.get("data-acct") == "testbox"]


async def test_sidebar_shows_draggable_folders_and_a_drop_reorders_them(user: User, box) -> None:
    await user.open("/")
    await user.should_see("Juridique")
    assert displayed(user) == ["adm", "arch", "jur"]  # alphabétique au départ
    el = next(e for e in user.find(kind=ui.element).elements if e.props.get("data-fkey") == "jur")
    assert el.props["draggable"] == "true" and el.props["data-parent"] == "" and "navfolder" in el.classes
    box[-1].folder_dropped({"detail": {"from": "jur", "after": False}}, "testbox", "adm")  # « Juridique » déposé avant « Administratif »
    await asyncio.sleep(0.4)
    assert displayed(user)[:2] == ["jur", "adm"] and folder_order.load("testbox")[:2] == ["jur", "adm"]
    box[-1].folder_dropped({"detail": {"from": "INBOX", "after": True}}, "testbox", "adm")  # la boîte de réception n'est pas déplaçable
    assert folder_order.load("testbox")[:2] == ["jur", "adm"]
