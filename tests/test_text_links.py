"""Texte brut d'un mail reçu : les adresses web sont cliquables."""
import pytest
from conftest import add_message
from nicegui import ui

import main
from mailia import config, queries, sync
from mailia.textutil import split_links


def test_links_are_found_and_trailing_punctuation_stays_outside() -> None:
    t = "Bonjour https://admin.alwaysdata.com/user/validate/?user_id=a&token=1-b, puis (voir www.exemple.fr/page). Fin."
    assert split_links(t) == [("text", "Bonjour "), ("link", "https://admin.alwaysdata.com/user/validate/?user_id=a&token=1-b"),
                              ("text", ", puis (voir "), ("link", "www.exemple.fr/page"), ("text", "). Fin.")]
    assert split_links("rien ici") == [("text", "rien ici")]
    assert split_links("https://fr.wikipedia.org/wiki/Chat_(animal)") == [("link", "https://fr.wikipedia.org/wiki/Chat_(animal)")]  # parenthèse de l'adresse conservée


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


async def test_a_plain_text_mail_shows_a_clickable_link_that_asks_before_opening(user, box) -> None:
    add_message("testbox|INBOX|1", subject="Bienvenue", body="Validez ici : https://admin.exemple.com/validate?x=1\nMerci")
    await user.open("/")
    b = box[-1]
    await b.open("testbox|INBOX|1")
    await user.should_see("https://admin.exemple.com/validate?x=1")
    link = user.find(kind=ui.html, content="maillink").elements.pop()
    assert 'class="maillink"' in link.content
    assert any(l.type.startswith("click") for l in link._event_listeners.values())
