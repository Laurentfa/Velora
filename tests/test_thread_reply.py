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


async def test_a_draft_in_the_thread_is_labelled_not_shown_as_an_unknown_sender(user, box) -> None:
    from mailia import db
    with db.conn() as c:
        c.execute("INSERT OR REPLACE INTO folders(account,key,name,path,role,sync) VALUES('testbox','Drafts','Brouillons','Brouillons','drafts',0)")
    add_message("testbox|INBOX|0", sender="alice@example.com", subject="Dossier X", ckey="testbox|conv", date="2026-10-01T10:00:00+00:00")
    add_message("testbox|Drafts|1", sender="", subject="Dossier X", ckey="testbox|conv", date="2026-10-02T10:00:00+00:00", folder="Drafts")
    await user.open("/")
    await box[-1].open("testbox|INBOX|0")
    await user.should_see("Brouillon")
    await user.should_see("Moi")


async def test_module_tools_stay_visible_when_forwarding(user, box, monkeypatch) -> None:
    from mailia import modules
    monkeypatch.setitem(modules.slots, "reader", [{"module": "essai", "order": 1, "build": lambda api, mb, msg: ui.label(f"CARTE {msg['id']}")}])
    add_message("testbox|INBOX|0", sender="alice@example.com", subject="Dossier X", ckey="testbox|conv")
    await user.open("/")
    b = box[-1]
    await b.open("testbox|INBOX|0")
    await user.should_see("CARTE testbox|INBOX|0")
    await b.forward()
    assert b.new
    await user.should_see("CARTE testbox|INBOX|0")


async def test_each_message_of_a_conversation_can_be_printed_on_its_own(user, box, monkeypatch) -> None:
    import shutil
    from pathlib import Path
    opened = []
    monkeypatch.setattr(main, "open_file", lambda p: opened.append(Path(p)) or True)
    add_message("testbox|INBOX|0", sender="alice@example.com", subject="Dossier X", ckey="testbox|conv", date="2026-10-01T10:00:00+00:00", body="Premier message")
    add_message("testbox|INBOX|1", sender="bob@example.com", subject="Re: Dossier X", ckey="testbox|conv", date="2026-10-02T10:00:00+00:00", body="Deuxième message")
    await user.open("/")
    await box[-1].open("testbox|INBOX|1")
    await user.should_see("Dossier X")
    el = user.find(marker="tc-print-testbox|INBOX|0").elements.pop()
    with user.client:  # le bouton écoute « click.stop » : on envoie l'événement tel quel
        for lid in list(el._event_listeners):
            el._handle_event({"listener_id": lid, "args": {}})
    await user.should_see("Aperçu avant impression")
    frames = [e for e in user.find(kind=ui.element).elements if e.tag == "iframe"]
    assert frames and "Premier message" in frames[-1]._props["srcdoc"] and "Deuxième message" not in frames[-1]._props["srcdoc"]
    assert "window.print()" in frames[-1]._props["srcdoc"] and 'load", () =>' not in frames[-1]._props["srcdoc"]  # l'aperçu n'imprime pas tout seul
    user.find(marker="print-browser").click()
    assert len(opened) == 1
    page = opened[0].read_text(encoding="utf-8")
    shutil.rmtree(opened[0].parent, ignore_errors=True)
    assert "Premier message" in page and "Deuxième message" not in page and "alice@example.com" in page and "window.print()" in page
