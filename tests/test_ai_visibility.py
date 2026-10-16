"""Sans clé d'IA, aucune fonction IA n'est affichée ; elles apparaissent dès qu'une clé est enregistrée. Aperçus de pièces jointes : thème de l'appli."""
from datetime import datetime

import pytest
from nicegui import ui
from nicegui.testing import User

import main
from mailia import config, db, sync
from mailia.ui_settings import _tab_ia, _tab_prm, _tab_rules
from tests.conftest import add_message


def test_ai_needs_both_the_switch_and_a_key(monkeypatch):
    for k in ("ANTHROPIC_API_KEY", "ONEMIN_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    assert config.ai_switch() and not config.ai_has_key() and not config.ai_enabled()  # activé par défaut, mais aucune clé : inactif
    monkeypatch.setenv("ONEMIN_API_KEY", "k")
    assert config.ai_enabled()  # la clé de n'importe quel fournisseur suffit
    monkeypatch.setenv("AI_ENABLED", "0")
    assert config.ai_has_key() and config.ai_switch() is False and not config.ai_enabled()  # interrupteur coupé : inactif malgré la clé


@pytest.fixture
def mailbox(monkeypatch):
    monkeypatch.setattr(sync, "run_cycle", lambda: None)
    ui.page("/")(main.index_page)
    db.kv_set("brief_day", datetime.now().strftime("%Y-%m-%d"))
    add_message(mid="testbox|INBOX|1", subject="Devis chaudière", summary="Marie demande la validation du devis.", category="action", priority=3,
                needs_reply=1, ai_at="2026-10-01T10:05:00+00:00")


async def test_without_a_key_no_ai_element_is_shown(user: User, mailbox, monkeypatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    await user.open("/")
    await user.should_see("Devis chaudière")
    for hidden in ("Brief du jour", "Demander à ma boîte", "À traiter", "Marie demande la validation du devis.", "Urgent", "Réponse attendue"):
        await user.should_not_see(hidden)
    user.find(marker="row-testbox|INBOX|1").click()
    await user.should_see("Répondre")
    for hidden in ("Réponse IA", "Résumé IA", "Analyser ce message"):
        await user.should_not_see(hidden)


async def test_with_a_key_the_ai_elements_are_back(user: User, mailbox) -> None:
    await user.open("/")
    await user.should_see("Devis chaudière")
    await user.should_see("Brief du jour")
    await user.should_see("Demander à ma boîte")
    await user.should_see("Marie demande la validation du devis.")


async def test_settings_explain_why_ai_is_hidden(user: User, monkeypatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY")

    @ui.page("/ia")
    def page():
        f = {"AI_ENABLED": True, "AI_PROVIDER": "anthropic", "ANTHROPIC_API_KEY": "", "ONEMIN_API_KEY": "", "AI_FALLBACK": True, "AI_UNREAD_ONLY": True,
             "AI_MAX_AGE_DAYS": 30, **{f"MODEL_{t}": "claude-haiku-4-5" for t in config.TASKS}, **{f"MODEL1_{t}": "" for t in config.TASKS},
             **{f"PROVIDER_{t}": "default" for t in config.TASKS}}
        _tab_ia(f)
        _tab_prm(f, ui.row())
        _tab_rules(sync.ACCOUNTS, ui.row())

    await user.open("/ia")
    await user.should_see("Aucune clé d'API n'est enregistrée")
    await user.should_see("Ces consignes règlent le travail de l'IA")
    await user.should_see("Créer une règle passe par l'IA")
    await user.should_not_see("Interpréter la règle")


def test_attachment_preview_frame_follows_the_app_theme():
    from mailia.ui_assets import CSS
    assert "body.body--dark .pvframe{filter:invert(1) hue-rotate(180deg)" in CSS  # en thème sombre, comme les mails
    import inspect
    from mailia import ui_files
    src = inspect.getsource(ui_files)
    assert 'classes("pvframe")' in src and "background:#fff" not in src.split("pvframe")[1].split("\n")[0]  # plus de fond blanc imposé
