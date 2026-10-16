"""Fournisseur 1min.AI : aiguillage, repli, images, coût. Le réseau est simulé (aucun appel réel)."""
import json
import time

import httpx
import pytest
from mailia import ai, aipolicy, config, db, onemin


class FakeResp:
    def __init__(self, status=200, data=None):
        self.status_code, self._d, self.text = status, data or {}, str(data)

    def json(self):
        return self._d


@pytest.fixture
def env(monkeypatch):
    for k in ("ONEMIN_API_KEY", "ANTHROPIC_API_KEY", "AI_PROVIDER", "PROVIDER_ASK", "MODEL1_ASK"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(onemin, "_calls", onemin._calls.__class__())
    db.kv_set("onemin_models", json.dumps({"ids": ["gpt-x", "m"], "openai": ["gpt-x", "m"], "at": time.time()}))
    return monkeypatch


def ok_chat(text="réponse"):
    return FakeResp(200, {"choices": [{"message": {"content": text}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 12, "completion_tokens": 3}})


def test_provider_and_model_selection(env):
    assert config.ai_provider("ASK") == "anthropic" and config.model("ASK") == "claude-haiku-4-5"
    env.setenv("AI_PROVIDER", "onemin")
    assert config.model("ASK") == "1min:" + config.ONEMIN_DEFAULT_MODEL
    env.setenv("PROVIDER_ASK", "anthropic")
    assert config.model("ASK") == "claude-haiku-4-5" and config.ai_provider("BRIEF") == "onemin"
    env.setenv("PROVIDER_ASK", "default")
    env.setenv("MODEL1_ASK", "mon-modele")
    assert config.model("ASK") == "1min:mon-modele"


def test_onemin_call_records_tokens_without_cost(env):
    env.setenv("ONEMIN_API_KEY", "k")
    sent = {}

    def post(url, headers, json, timeout):
        sent.update(url=url, auth=headers["Authorization"], body=json)
        return ok_chat()
    env.setattr(httpx, "post", post)
    assert ai._ask("sys", "bonjour", model="1min:gpt-x", feature="ask") == "réponse"
    assert sent["url"].endswith("/chat/completions") and sent["auth"] == "Bearer k" and sent["body"]["model"] == "gpt-x"
    assert aipolicy.cost_usd("1min:gpt-x", 10**6, 10**6) == 0.0


def test_fallback_and_no_fallback(env):
    env.setenv("ONEMIN_API_KEY", "k")
    env.setenv("ANTHROPIC_API_KEY", "a")
    env.setattr(httpx, "post", lambda *a, **k: FakeResp(402, {"error": {"message": "insufficient credits"}}))
    used = []
    real = ai._run

    def run(model, *a):
        used.append(model)
        return "via claude" if not model.startswith("1min:") else real(model, *a)
    env.setattr(ai, "_run", run)
    assert ai._ask("s", "u", model="1min:gpt-x", feature="ask") == "via claude"
    assert used == ["1min:gpt-x", "claude-haiku-4-5"]
    db.kv_set("ai_fallback", "0")
    with pytest.raises(ai.AIError, match="Crédits"):
        ai._ask("s", "u", model="1min:gpt-x", feature="ask")


def native_resp(text="lu", credit=500):
    return FakeResp(200, {"aiRecord": {"aiRecordDetail": {"resultObject": [text]},
                                       "metadata": {"credit": credit, "inputToken": 120, "totalToken": 130, "finishReason": "stop"},
                                       "teamUser": {"creditLimit": 1000000, "usedCredit": 4000}}})


def test_native_for_claude_model_and_credits(env):
    env.setenv("ONEMIN_API_KEY", "k")
    sent = {}
    env.setattr(httpx, "post", lambda url, headers, json, timeout: sent.update(url=url, h=headers, b=json) or native_resp("Bonjour."))
    out = ai._ask("consigne", "message", model="1min:us.anthropic.claude-haiku-4-5-20251001-v1:0", feature="ask")
    assert out == "Bonjour." and sent["url"].endswith("/api/chat-with-ai") and sent["h"]["API-KEY"] == "k"
    assert sent["b"]["model"] == "us.anthropic.claude-haiku-4-5-20251001-v1:0"
    assert sent["b"]["promptObject"]["prompt"].startswith("consigne") and sent["b"]["promptObject"]["prompt"].endswith("message")
    c = onemin.credits_summary()
    assert c["credits"] == 500 and c["calls"] == 1


def test_images_use_native_and_vision_model(env):
    env.setenv("ONEMIN_API_KEY", "k")
    calls = []

    def post(url, headers=None, json=None, timeout=None, files=None):
        calls.append(url)
        if url.endswith("/assets"):
            assert files["asset"][2] == "image/jpeg"
            return FakeResp(200, {"asset": {"key": "images/x.jpg"}})
        calls.append(json)
        return native_resp("scan lu")
    env.setattr(httpx, "post", post)
    assert ai._ask("s", "u", model="1min:mistral-small-latest", images=[b"jpg"], feature="ask") == "scan lu"
    assert calls[0].endswith("/assets") and calls[2]["model"] == onemin.IMAGE_FALLBACK
    assert calls[2]["promptObject"]["attachments"] == {"images": ["images/x.jpg"]}
    calls.clear()
    ai._ask("s", "u", model="1min:gpt-4o-mini", images=[b"jpg"], feature="ask")
    assert calls[2]["model"] == "gpt-4o-mini"  # modèle qui lit les images : conservé


def test_native_error_in_body_and_text_only_models_use_openai(env):
    env.setenv("ONEMIN_API_KEY", "k")
    env.setattr(httpx, "post", lambda *a, **k: FakeResp(200, {"aiRecord": {"aiRecordDetail": {"resultObject": {"code": "UNKNOWN_ERROR", "message": "file type not supported"}}, "metadata": {}}}))
    with pytest.raises(ai.AIError, match="not supported"):
        onemin.chat("us.anthropic.claude-haiku-5-5", "s", "u")
    urls = []
    env.setattr(httpx, "post", lambda url, headers, json, timeout: urls.append(url) or ok_chat())
    onemin.chat("gpt-x", "s", "u")
    assert urls[0].endswith("/openai/v1/chat/completions")


def test_key_missing_and_rate_errors(env):
    with pytest.raises(ai.AIError) as e:  # aucune clé nulle part : les fonctions IA sont inactives, sans repli possible
        ai._ask("s", "u", model="1min:gpt-x", feature="ask")
    assert not e.value.fallback and "désactivées" in str(e.value)
    env.setenv("ANTHROPIC_API_KEY", "a")
    with pytest.raises(ai.AIError) as e:  # clé Claude seulement : l'appel 1min.AI échoue faute de clé, et on peut se replier sur Claude
        ai._ask("s", "u", model="1min:gpt-x", feature="ask")
    assert e.value.fallback
    env.setenv("ONEMIN_API_KEY", "k")
    env.setattr(httpx, "post", lambda *a, **k: FakeResp(401, {}))
    with pytest.raises(ai.AIError, match="refusée"):
        onemin.chat("m", "s", "u")
    env.setattr(httpx, "post", lambda *a, **k: FakeResp(400, {"error": {"message": "bad"}}))
    with pytest.raises(ai.AIError) as e:
        onemin.chat("m", "s", "u")
    assert not e.value.fallback


def test_long_input_truncated_and_models_list(env):
    env.setenv("ONEMIN_API_KEY", "k")
    body = {}
    env.setattr(httpx, "post", lambda url, headers, json, timeout: body.update(json) or ok_chat())
    onemin.chat("m", "s" * 100, "u" * 200_000, max_tokens=999999)
    assert len(body["messages"][1]["content"]) < 96_000 and body["max_tokens"] == onemin.MAX_OUT
    env.setattr(httpx, "get", lambda url, headers, timeout: FakeResp(200, {"data": [{"id": "b"}, {"id": "a"}]}))
    ok, msg = onemin.test("k")
    assert ok and f"{len(onemin.NATIVE_MODELS) + 2} modèles" in msg
    ids = onemin.list_models()
    assert "a" in ids and "us.anthropic.claude-haiku-5-5" in ids
