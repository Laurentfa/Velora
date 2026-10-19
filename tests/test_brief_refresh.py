"""Brief du jour : un point de la synthèse disparaît dès que son mail est géré (lu, archivé, supprimé, reporté), sans attendre l'IA."""
import asyncio
import json
from datetime import datetime

import pytest
from nicegui import ui

import main
from tests.conftest import add_message
from mailia import config, db, queries, sync


@pytest.fixture
def box(monkeypatch):
    monkeypatch.setattr(sync, "run_cycle", lambda: None)
    monkeypatch.setattr(config, "ai_enabled", lambda: True)
    monkeypatch.setattr(queries, "_refresh_headline", lambda sig, items: None)  # pas de nouvelle synthèse de l'IA pendant le test : on regarde ce qui est conservé
    ui.page("/")(main.index_page)
    db.kv_set("brief_day", datetime.now().strftime("%Y-%m-%d"))
    seen = []
    real = main.Mailbox.__init__
    monkeypatch.setattr(main.Mailbox, "__init__", lambda self, *a, **k: (real(self, *a, **k), seen.append(self))[0])
    return seen


def make_brief(ids):
    for i, mid in enumerate(ids):
        add_message(mid, subject=f"Sujet {i}", ai_at="2026-10-19T08:00:00+00:00", category="action", priority=3, needs_reply=1, summary=f"Résumé {i}",
                    date=f"2026-10-19T0{i + 1}:00:00+00:00")
    points = [{"id": mid, "text": f"Point numéro {i}"} for i, mid in enumerate(ids)]
    db.kv_set("brief:headline", json.dumps({"intro": "Intro du jour", "urgent": points, "waiting": [], "info": ""}, ensure_ascii=False))
    rows = queries.brief()["todo"]
    db.kv_set("brief:sig", f"{len(rows)}:{rows[0]['id']}")  # synthèse à jour : aucun appel à l'IA


async def test_a_point_disappears_when_its_message_is_read(user, box) -> None:
    ids = ["testbox|INBOX|1", "testbox|INBOX|2"]
    make_brief(ids)
    await user.open("/")
    b = box[-1]
    await b.go("brief", "", "")
    await asyncio.sleep(0.6)
    await user.should_see("Point numéro 0")
    await user.should_see("Point numéro 1")
    with db.conn() as c:
        c.execute("UPDATE messages SET is_read=1 WHERE id=?", (ids[0],))  # géré : lu
    await b.reload(force=True)
    await asyncio.sleep(0.5)
    await user.should_not_see("Point numéro 0")
    await user.should_see("Point numéro 1")


async def test_a_point_disappears_when_its_message_is_archived_or_deleted(user, box) -> None:
    ids = ["testbox|INBOX|1", "testbox|INBOX|2", "testbox|INBOX|3"]
    make_brief(ids)
    await user.open("/")
    b = box[-1]
    await b.go("brief", "", "")
    await asyncio.sleep(0.6)
    with db.conn() as c:
        c.execute("UPDATE messages SET folder='Archive' WHERE id=?", (ids[0],))
        c.execute("DELETE FROM messages WHERE id=?", (ids[1],))
    await b.reload(force=True)
    await asyncio.sleep(0.5)
    await user.should_not_see("Point numéro 0")
    await user.should_not_see("Point numéro 1")
    await user.should_see("Point numéro 2")


async def test_points_of_messages_still_to_handle_stay(user, box) -> None:
    ids = ["testbox|INBOX|1", "testbox|INBOX|2"]
    make_brief(ids)
    await user.open("/")
    b = box[-1]
    await b.go("brief", "", "")
    await b.reload(force=True)
    await asyncio.sleep(0.5)
    await user.should_see("Point numéro 0")
    await user.should_see("Point numéro 1")


async def test_the_brief_title_is_shown_once_with_its_icon_and_a_subtitle(user, box) -> None:
    make_brief(["testbox|INBOX|1", "testbox|INBOX|2"])
    await user.open("/")
    b = box[-1]
    await b.go("brief", "", "")
    await asyncio.sleep(0.6)
    assert b.title.text == "Brief du jour" and "sparkle" not in b.title_icon.content and "<svg" in b.title_icon.content  # l'icône est devant le titre du haut
    assert b.subtitle.visible and "non lus" not in b.subtitle.text and str(datetime.now().day) in b.subtitle.text
    labels = [el for el in user.client.elements.values() if isinstance(el, ui.label)]
    assert not [el for el in labels if "tag" in el.classes and "brief du jour" in el.text.lower()]  # plus de second titre dans la carte du brief
    assert not [el for el in labels if "brief-date" in el.classes]  # la date est dans le sous-titre du haut


async def test_the_icon_and_subtitle_disappear_in_other_views(user, box) -> None:
    make_brief(["testbox|INBOX|1"])
    await user.open("/")
    b = box[-1]
    await b.go("brief", "", "")
    await b.go("inbox", "", "")
    await asyncio.sleep(0.5)
    assert not b.subtitle.visible and b.title_icon.content == ""
