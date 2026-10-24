"""Boîte de réception : pas de bouton « Afficher plus », la liste se prolonge en descendant ; le bouton reste dans les dossiers."""
import types
from datetime import datetime, timedelta, timezone

import pytest
from nicegui import ui
from nicegui.testing import User

import main
from mailia import db, sync
from tests.conftest import add_message, wait_until


@pytest.fixture
def box(monkeypatch):
    monkeypatch.setattr(sync, "run_cycle", lambda: None)
    ui.page("/")(main.index_page)
    db.kv_set("brief_day", datetime.now().strftime("%Y-%m-%d"))
    base = datetime(2026, 10, 1, tzinfo=timezone.utc)
    for i in range(130):  # plus que la première page de 80
        add_message(mid=f"testbox|INBOX|{i}", subject=f"Sujet numéro {i}", body="x", ckey=f"testbox|c{i}", date=(base - timedelta(hours=i)).isoformat())
    seen = []
    real = main.Mailbox.__init__
    monkeypatch.setattr(main.Mailbox, "__init__", lambda self, *a, **k: (real(self, *a, **k), seen.append(self))[0])
    return seen


def scroll(near_bottom: bool):
    return types.SimpleNamespace(args={"verticalPosition": 5000 if near_bottom else 0, "verticalContainerSize": 800, "verticalSize": 5600})


async def test_inbox_has_no_show_more_button_and_loads_the_rest_when_scrolling(user: User, box) -> None:
    await user.open("/")
    await user.should_see("Sujet numéro 0")
    await user.should_not_see("Afficher plus")
    b = box[-1]
    await wait_until(lambda: len(b.rows) == 80 and len(b._row_els) == 80)
    assert len(b.rows) == 80 and len(b._row_els) == 80
    first = b._row_els["testbox|INBOX|0"]
    await b.rows_scrolled(scroll(near_bottom=False))
    assert len(b.rows) == 80  # loin du bas : rien ne se charge
    await b.rows_scrolled(scroll(near_bottom=True))
    await wait_until(lambda: len(b.rows) == 130 and len(b._row_els) == 130)
    assert len(b.rows) == 130 and len(b._row_els) == 130
    assert b._row_els["testbox|INBOX|0"] is first  # les lignes déjà affichées n'ont pas été redessinées : les nouvelles sont ajoutées à la suite
    await user.should_see("Sujet numéro 129")
    ids = [e.props["data-id"] for e in b._row_els["testbox|INBOX|0"].parent_slot.parent.default_slot.children if e.props.get("data-id")]
    assert ids == [f"testbox|INBOX|{i}" for i in range(130)]  # même ordre que la liste


async def test_nothing_more_to_load_when_the_whole_list_is_shown(user: User, box) -> None:
    await user.open("/")
    b = box[-1]
    await wait_until(lambda: len(b.rows) == 80)  # la première page est chargée en arrière-plan : on attend qu'elle soit là avant de défiler
    await b.rows_scrolled(scroll(True))
    await wait_until(lambda: len(b.rows) == 130)
    assert len(b.rows) == 130
    limit = b.limit
    await b.rows_scrolled(scroll(True))  # déjà tout chargé : pas de nouvelle demande
    assert b.limit == limit


async def test_a_folder_keeps_the_button(user: User, box) -> None:
    with db.conn() as c:
        c.execute("INSERT OR REPLACE INTO folders(account,key,name,path,role,sync) VALUES('testbox','Archive','Archive','Archive','archive',1)")
    base = datetime(2026, 9, 1, tzinfo=timezone.utc)
    for i in range(90):
        add_message(mid=f"testbox|Archive|{i}", subject=f"Archive {i}", body="x", folder="Archive", ckey=f"testbox|a{i}", date=(base - timedelta(hours=i)).isoformat())
    await user.open("/")
    b = box[-1]
    await b.go("folder", "testbox", "Archive")
    await wait_until(lambda: b.view == "folder" and len(b.rows) == 80)
    assert not b.auto_more()
    await user.should_see("Afficher plus")  # hors boîte de réception, le bouton reste
    await b.rows_scrolled(scroll(True))
    assert len(b.rows) == 80  # et le défilement ne charge rien tout seul


async def test_only_inbox_and_unread_views_load_automatically(user: User, box) -> None:
    await user.open("/")
    b = box[-1]
    assert b.auto_more()
    b.view = "unread"
    assert b.auto_more()
    b.view = "todo"
    assert not b.auto_more()
