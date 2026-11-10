"""Réponses rapides : chaque bouton ouvre une réponse avec son texte, et le réglage ouvre sa fenêtre."""
import asyncio

import pytest
from conftest import add_message, wait_until
from nicegui import ui

import main
from mailia import ai, config, sync


@pytest.fixture
def box(monkeypatch):
    monkeypatch.setattr(sync, "run_cycle", lambda: None)
    monkeypatch.setattr(config, "ai_enabled", lambda: False)
    prov = type("P", (), {"calendar_events": lambda self, a, b: [], "tasks": lambda self: []})()
    real_provider = sync.provider
    monkeypatch.setattr(sync, "provider", lambda key: prov if key == "testbox" else real_provider(key))
    ui.page("/")(main.index_page)
    seen = []
    real = main.Mailbox.__init__
    monkeypatch.setattr(main.Mailbox, "__init__", lambda self, *a, **k: (real(self, *a, **k), seen.append(self))[0])
    return seen


def _click(user, label):
    el = [e for e in user.find(kind=ui.button).elements if getattr(e, "text", "") == label and e._event_listeners].pop()
    with user.client:
        for lid in list(el._event_listeners):
            el._handle_event({"listener_id": lid, "args": {}})


@pytest.mark.parametrize("label,text", [("Oui", "Oui."), ("Non", "Non."), ("Reçu, merci", "Bien reçu, merci."), ("Reprise sur décharge", "Reprise sur décharge.")])
async def test_each_quick_reply_button_opens_its_text(user, box, label, text) -> None:
    add_message("testbox|INBOX|1", sender="alice@example.com", subject="Dossier X", ckey="testbox|c1")
    await user.open("/")
    b = box[-1]
    await b.open("testbox|INBOX|1")
    await user.should_see("Réponse rapide")
    _click(user, label)
    await wait_until(lambda: b.compose)
    assert text in b.draft["html"]


async def test_quick_reply_settings_button_opens_its_window(user, box) -> None:
    add_message("testbox|INBOX|1", sender="alice@example.com", subject="Dossier X", ckey="testbox|c1")
    await user.open("/")
    await box[-1].open("testbox|INBOX|1")
    await user.should_see("Réponse rapide")
    with user.client:
        el = [e for e in user.find(kind=ui.button).elements if e._props.get("icon") == "tune" and e._event_listeners].pop()
        for lid in list(el._event_listeners):
            el._handle_event({"listener_id": lid, "args": {}})
    await user.should_see("Mes réponses rapides")


async def test_second_quick_reply_asks_then_replaces_or_keeps(user, box) -> None:
    add_message("testbox|INBOX|1", sender="alice@example.com", subject="Dossier X", ckey="testbox|c1")
    await user.open("/")
    b = box[-1]
    await b.open("testbox|INBOX|1")
    await user.should_see("Réponse rapide")
    _click(user, "Non")
    await wait_until(lambda: b.compose)
    assert "Non." in b.draft["html"]
    _click(user, "Reçu, merci")
    await user.should_see("Remplacer le texte déjà écrit")
    with user.client:
        ok = [e for e in user.find(kind=ui.button).elements if getattr(e, "text", "") == "Remplacer"].pop()
        for lid in list(ok._event_listeners):
            ok._handle_event({"listener_id": lid, "args": {}})
    await wait_until(lambda: "Bien reçu, merci." in b.draft["html"])
    assert "Non." not in b.draft["html"]


async def test_settings_button_works_while_a_reply_is_open(user, box) -> None:
    add_message("testbox|INBOX|1", sender="alice@example.com", subject="Dossier X", ckey="testbox|c1")
    await user.open("/")
    b = box[-1]
    await b.open("testbox|INBOX|1")
    await user.should_see("Réponse rapide")
    _click(user, "Oui")
    await wait_until(lambda: b.compose)
    await asyncio.sleep(1)  # l'éditeur redessine la zone : on prend le bouton à jour
    with user.client:
        el = [e for e in user.find(kind=ui.button).elements if e._props.get("icon") == "tune" and e._event_listeners].pop()
        for lid in list(el._event_listeners):
            el._handle_event({"listener_id": lid, "args": {}})
    await user.should_see("Mes réponses rapides")
