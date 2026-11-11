"""Pièce jointe : après un clic sur « Joindre un fichier », le focus retourne à l'éditeur (sinon Entrée, tapée ensuite, rouvrait le sélecteur de fichiers)."""
import pytest
from conftest import add_message, wait_until
from nicegui import ui

import main
from mailia import config, sync, ui_drafts


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


async def test_attach_button_gives_the_focus_back_to_the_editor(user, box) -> None:
    add_message("testbox|INBOX|1", sender="alice@example.com", subject="Dossier X", ckey="testbox|c1")
    await user.open("/")
    b = box[-1]
    await b.open("testbox|INBOX|1")
    await user.should_see("Réponse rapide")
    b.open_compose()
    await wait_until(lambda: b.compose)
    await user.should_see("Joindre un fichier")
    attach = [e for e in user.find(kind=ui.button).elements if "Joindre un fichier" in (getattr(e, "text", "") or "")][-1]
    handlers = [getattr(lst, "js_handler", "") or "" for lst in attach._event_listeners.values()]
    assert any("pickFiles()" in h and ui_drafts.FOCUS_EDITOR_JS in h for h in handlers), handlers
