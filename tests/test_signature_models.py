"""Modèles de signature (Noël) : choix à la rédaction, bascule automatique en décembre."""
from datetime import datetime

import pytest
from conftest import add_message
from nicegui import ui

import main
from mailia import compose, config, db, queries, sync


def test_every_model_survives_the_outgoing_cleaning() -> None:
    from mailia import htmlmail
    for key in compose.SIGNATURE_MODELS:
        out = htmlmail.sanitize_outgoing(compose.model_html(key))
        assert "<table" in out and "border" in out and "color" in out and "{{" not in out


def test_the_name_is_escaped_into_the_model(monkeypatch) -> None:
    monkeypatch.setattr(config, "signature", lambda: "Marie <b>Dupont</b>")
    assert "<b>Dupont</b>" not in compose.model_html("noel_classique") and "Marie &lt;b&gt;" in compose.model_html("noel_classique")


def test_december_default_is_switchable(monkeypatch) -> None:
    class Dec(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 12, 10)
    monkeypatch.setattr(compose, "datetime", Dec)
    assert compose.default_model() == "noel_classique"
    assert "Joyeux Noël" in compose.sig_block("")
    db.kv_set("sig_xmas_auto", "0")
    assert compose.default_model() is None and "Joyeux Noël" not in compose.sig_block("")
    db.kv_set("sig_xmas_auto", "1")
    db.kv_set("sig_xmas_model", "noel_dore")
    assert "Joyeuses fêtes" in compose.sig_block("")


def test_no_christmas_signature_outside_december(monkeypatch) -> None:
    class Oct(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 10, 10)
    monkeypatch.setattr(compose, "datetime", Oct)
    assert compose.default_model() is None and "Joyeux Noël" not in compose.sig_block("")


def test_swapping_models_keeps_text_and_quote() -> None:
    html = compose.initial_html(account="") .replace("<p><br></p>", "<p>Bonjour</p>") + '<hr><blockquote>cité</blockquote>'
    out = compose.swap_signature(html, "", "noel_dore")
    assert "Joyeuses fêtes" in out and "<p>Bonjour</p>" in out and out.endswith("<blockquote>cité</blockquote>")
    back = compose.swap_signature(out, "", compose.NO_SIGNATURE)
    assert "msig" not in back and "<blockquote>cité</blockquote>" in back
    again = compose.swap_signature(back, "", "voeux_sobres")
    assert again.count('class="msig"') == 1 and again.index("msig") < again.index("<hr")


@pytest.fixture
def box(monkeypatch):
    monkeypatch.setattr(sync, "run_cycle", lambda: None)
    monkeypatch.setattr(config, "ai_enabled", lambda: True)
    monkeypatch.setattr(queries, "_refresh_headline", lambda sig, items: None)
    ui.page("/")(main.index_page)
    seen = []
    real = main.Mailbox.__init__
    monkeypatch.setattr(main.Mailbox, "__init__", lambda self, *a, **k: (real(self, *a, **k), seen.append(self))[0])
    return seen


async def test_the_model_can_be_chosen_when_replying_and_forwarding(user, box) -> None:
    add_message("testbox|INBOX|1", subject="Devis", sender="alice@example.com", to_addrs="moi@example.org")
    await user.open("/")
    b = box[-1]
    await b.open("testbox|INBOX|1")
    await b.forward(queries.get_message("testbox|INBOX|1"))
    await user.should_see("Modèle de signature")
    b.choose_signature("noel_dore")
    assert "Joyeuses fêtes" in b.draft["html"] and b.draft["html"].count('class="msig"') == 1 and "TR:" in b.draft["subject"]
    b.choose_signature(compose.NO_SIGNATURE)
    assert "msig" not in b.draft["html"]
