"""Importance des messages : lue à la réception, affichée en liste, filtre « Importance haute », choisie à la rédaction et gardée jusqu'à l'envoi."""
import asyncio
from datetime import datetime

import pytest
from nicegui import events, ui
from nicegui.testing import User

import main
from mailia import db, outgoing, sync


@pytest.fixture
def box(monkeypatch):
    monkeypatch.setattr(sync, "run_cycle", lambda: None)
    ui.page("/")(main.index_page)
    db.kv_set("brief_day", datetime.now().strftime("%Y-%m-%d"))
    seen = []
    real = main.Mailbox.__init__
    monkeypatch.setattr(main.Mailbox, "__init__", lambda self, *a, **k: (real(self, *a, **k), seen.append(self))[0])
    return seen


def click(user, marker):
    (el,) = user.find(marker=marker).elements
    with user.client:
        for listener in el._event_listeners.values():
            if listener.type == "click":
                events.handle_event(listener.handler, events.ClickEventArguments(sender=el, client=user.client))


def test_a_draft_keeps_its_importance():
    db.draft_save({"id": "imp-1", "account": "a", "is_new": True, "to": "x@exemple.fr", "cc": "", "subject": "s", "html": "<p>x</p>", "importance": 2})
    (row,) = [r for r in db.drafts_list() if r["id"] == "imp-1"]
    assert row["importance"] == 2
    db.draft_delete("imp-1")


def test_the_outbox_keeps_its_importance():
    oid = outgoing.queue("a", "x@exemple.fr", "", "", "s", "<p>x</p>", [], outgoing.earliest(), importance=2)
    assert db.outbox_get(oid)["importance"] == 2
    default = outgoing.queue("a", "x@exemple.fr", "", "", "s", "<p>x</p>", [], outgoing.earliest())
    assert db.outbox_get(default)["importance"] == 1


async def test_the_composer_sets_the_importance(user: User, box) -> None:
    await user.open("/")
    b = box[-1]
    b.new_message()
    await asyncio.sleep(0.3)
    assert b.draft["importance"] == 1
    click(user, "importance-2")
    await asyncio.sleep(0.2)
    assert b.draft["importance"] == 2
    click(user, "importance-0")
    await asyncio.sleep(0.2)
    assert b.draft["importance"] == 0
