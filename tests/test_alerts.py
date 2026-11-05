"""1min.AI refuse la connexion : une alerte reste affichée jusqu'au clic sur OK, une seule fois."""
import httpx
import pytest
from conftest import wait_until
from nicegui import ui

import main
from mailia import ai, alerts, config, db, onemin, queries, sync


class Resp:
    def __init__(self, status=200, data=None):
        self.status_code, self._d, self.text = status, data or {}, str(data)

    def json(self):
        return self._d


def ok_chat():
    return Resp(200, {"choices": [{"message": {"content": "réponse"}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 12, "completion_tokens": 3}})


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    for k in ("ONEMIN_API_KEY", "ANTHROPIC_API_KEY", "AI_PROVIDER", "PROVIDER_ASK", "MODEL1_ASK"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("ONEMIN_API_KEY", "k")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "a")
    monkeypatch.setattr(onemin, "_calls", onemin._calls.__class__())
    import json
    import time
    db.kv_set("onemin_models", json.dumps({"ids": ["gpt-x"], "openai": ["gpt-x"], "at": time.time()}))
    alerts._pending.clear()
    alerts._acked.clear()
    yield
    alerts._pending.clear()
    alerts._acked.clear()


def ask(monkeypatch, status):
    """Un appel IA par 1min.AI qui échoue avec ce code, puis se replie sur Claude (simulé)."""
    monkeypatch.setattr(httpx, "post", lambda *a, **k: Resp(status, {"error": {"message": "API Key is not active"}}) if status != 200 else ok_chat())
    real = ai._run
    monkeypatch.setattr(ai, "_run", lambda model, *a: "via claude" if not model.startswith("1min:") else real(model, *a))
    return ai._ask("s", "u", model="1min:gpt-x", feature="ask")


def test_a_refused_key_raises_one_alert_that_is_not_repeated_once_acknowledged(monkeypatch) -> None:
    assert ask(monkeypatch, 401) == "via claude"  # l'IA continue de répondre par le repli
    (a,) = alerts.pending()
    assert a["key"] == "onemin:key" and "refuse la connexion" in a["title"]
    assert "refusée" in a["text"] and "API Key is not active" in a["text"] and "Claude" in a["text"]
    ask(monkeypatch, 401)
    assert len(alerts.pending()) == 1  # pas de doublon tant qu'elle est là
    alerts.acknowledge("onemin:key")
    ask(monkeypatch, 401)
    assert alerts.pending() == []  # acquittée : elle ne revient pas à chaque appel
    ask(monkeypatch, 200)  # la connexion refonctionne
    assert ask(monkeypatch, 401) == "via claude"
    assert [x["key"] for x in alerts.pending()] == ["onemin:key"]  # le problème revient : l'alerte aussi


def test_exhausted_credits_alert_but_a_passing_outage_does_not(monkeypatch) -> None:
    ask(monkeypatch, 429)
    ask(monkeypatch, 503)
    assert alerts.pending() == []
    ask(monkeypatch, 402)
    assert [x["key"] for x in alerts.pending()] == ["onemin:credits"]


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


async def test_the_alert_window_stays_until_ok_is_clicked(user, box) -> None:
    await user.open("/")
    b = box[-1]
    await b.tick()
    alerts.raise_alert("onemin:key", "1min.AI refuse la connexion", "Clé 1min.AI refusée ou invalide.")
    await b.tick()
    await user.should_see("1min.AI refuse la connexion")
    await user.should_see("Clé 1min.AI refusée ou invalide.")
    for _ in range(3):
        await b.tick()
    assert len(user.find(content="1min.AI refuse la connexion").elements) == 1  # une seule fenêtre, même si l'alerte est toujours là
    user.find(marker="alert-ok").click()
    await wait_until(lambda: alerts.pending() == [])
    assert b._alert_open == ""
    await b.tick()
    assert alerts.pending() == []  # acquittée : elle ne se rouvre pas
