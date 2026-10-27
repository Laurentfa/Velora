"""Conversation : répondre ou transférer n'importe quel message, pas seulement le dernier."""
import pytest
from conftest import add_message
from nicegui import ui

import main
from mailia import config, queries, sync


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
    return seen


def _thread() -> None:
    for i, who in enumerate(["alice@example.com", "bob@example.com", "carol@example.com"]):
        add_message(f"testbox|INBOX|{i}", sender=who, subject="Dossier X", ckey="testbox|conv", date=f"2026-10-0{i + 1}T10:00:00+00:00",
                    to_addrs="moi@example.org, dave@example.com")


async def test_each_message_has_reply_and_forward_and_replying_targets_that_message(user, box) -> None:
    _thread()
    await user.open("/")
    b = box[-1]
    await b.open("testbox|INBOX|2")
    await user.should_see("Dossier X")
    for i in range(3):
        assert user.find(marker=f"tc-reply-testbox|INBOX|{i}").elements
        assert user.find(marker=f"tc-forward-testbox|INBOX|{i}").elements
    el = user.find(marker="tc-reply-testbox|INBOX|0").elements.pop()
    with user.client:  # le simulateur ne déclenche que « click » : le bouton écoute « click.stop » (pour ne pas replier la carte), on envoie l'événement tel quel
        for lid in list(el._event_listeners):
            el._handle_event({"listener_id": lid, "args": {}})
    from tests.conftest import wait_until
    await wait_until(lambda: b.compose)
    assert b.cur["id"] == "testbox|INBOX|0" and b.draft["reply_to"] == "testbox|INBOX|0" and "alice@example.com" in b.draft["to"]
