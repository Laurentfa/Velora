"""Une IA qui refuse (clé refusée, crédits épuisés…) ne doit pas être réinterrogée sur le même mail toutes les quelques secondes."""
import pytest
from conftest import add_message

from mailia import ai, aipolicy, config, sync


@pytest.fixture
def pending(monkeypatch):
    monkeypatch.setattr(config, "ai_enabled", lambda: True)
    monkeypatch.setattr(sync, "ensure_details", lambda row: None)
    monkeypatch.setattr(sync, "needs_details", lambda row: False)
    sync._ai_pause.update(until=0.0, fp="")
    calls = []

    def fail(mid):
        calls.append(mid)
        raise aipolicy.AIError("Clé 1min.AI refusée ou invalide.", fallback=True, alert="key")

    monkeypatch.setattr(ai, "analyze_and_store", fail)
    add_message("testbox|INBOX|1", is_read=0)
    acc = sync.ACCOUNTS.get("testbox")
    if acc is not None:
        monkeypatch.setattr(acc, "ai_enabled", True, raising=False)
    return calls


def test_a_refused_key_pauses_the_analysis_instead_of_retrying_every_cycle(pending) -> None:
    for _ in range(5):
        sync.analyze_pending()
    assert len(pending) == 1  # une seule tentative, puis la pause


def test_changing_the_keys_lifts_the_pause_at_once(pending, monkeypatch) -> None:
    sync.analyze_pending()
    sync.analyze_pending()
    assert len(pending) == 1
    real = config.env
    monkeypatch.setattr(config, "env", lambda k, d="": "nouvelle-cle" if k == "ONEMIN_API_KEY" else real(k, d))
    sync.analyze_pending()
    assert len(pending) == 2  # la clé a changé : on réessaie tout de suite


def test_the_pause_ends_after_its_delay(pending, monkeypatch) -> None:
    sync.analyze_pending()
    sync._ai_pause["until"] = 0.0
    sync.analyze_pending()
    assert len(pending) == 2
