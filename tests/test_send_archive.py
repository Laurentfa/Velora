"""Envoi d'une réponse : la case « Archiver la conversation après l'envoi »."""
import asyncio

import pytest
from conftest import add_message
from nicegui import ui

import main
from mailia import config, db, queries, sync


@pytest.fixture
def box(monkeypatch):
    monkeypatch.setattr(sync, "run_cycle", lambda: None)
    monkeypatch.setattr(config, "ai_enabled", lambda: True)
    monkeypatch.setattr(queries, "_refresh_headline", lambda sig, items: None)
    prov = type("P", (), {"calendar_events": lambda self, a, b: [], "tasks": lambda self: []})()
    real_provider = sync.provider
    monkeypatch.setattr(sync, "provider", lambda key: prov if key == "testbox" else real_provider(key))
    ui.page("/")(main.index_page)
    seen = []
    real = main.Mailbox.__init__
    monkeypatch.setattr(main.Mailbox, "__init__", lambda self, *a, **k: (real(self, *a, **k), seen.append(self))[0])
    return type("Box", (), {"boxes": seen})()


async def _reply_dialog(box, monkeypatch, tick: bool):
    calls = []
    monkeypatch.setattr(sync, "perform_many", lambda msgs, kind, dest=None: calls.append((kind, [x["id"] for x in msgs])))
    add_message("testbox|INBOX|1", sender="alice@example.com", subject="Question")
    b = box.boxes[-1]
    await b.open("testbox|INBOX|1")
    b.draft.update(to="alice@example.com", html="<p>Merci, c'est fait.</p>")
    task = asyncio.create_task(b.send())
    return b, task, calls


async def test_the_reply_dialog_offers_to_archive_and_does_it(user, box, monkeypatch) -> None:
    await user.open("/")
    b, task, calls = await _reply_dialog(box, monkeypatch, True)
    await user.should_see("Archiver la conversation après l'envoi")
    user.find(marker="send-archive").click()
    user.find(marker="send-go").click()
    await asyncio.wait_for(task, 10)
    assert calls == [("archive", ["testbox|INBOX|1"])]
    assert db.kv_get("send_archive") == "1"  # le choix est repris la fois suivante


async def test_without_the_box_ticked_the_conversation_stays(user, box, monkeypatch) -> None:
    await user.open("/")
    db.kv_set("send_archive", "0")
    b, task, calls = await _reply_dialog(box, monkeypatch, False)
    await user.should_see("Archiver la conversation après l'envoi")
    user.find(marker="send-go").click()
    await asyncio.wait_for(task, 10)
    assert calls == []
