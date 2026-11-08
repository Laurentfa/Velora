"""Fournisseur compatible OpenAI (OpenAI, Mistral, Azure, Ollama…) : réglages, aiguillage, requête, erreurs, images, coût, repli. Le réseau est simulé."""
import json

import httpx
import pytest

from mailia import ai, aipolicy, config, db, oaicompat

ENV_KEYS = ("OAI_API_KEY", "OAI_BASE_URL", "OAI_VISION", "OAI_PRICE_IN", "OAI_PRICE_OUT", "ONEMIN_API_KEY", "ANTHROPIC_API_KEY", "AI_PROVIDER", "PROVIDER_ASK", "MODEL2_ASK")


class FakeResp:
    def __init__(self, status=200, data=None, text=None):
        self.status_code, self._d = status, data if data is not None else {}
        self.text = text if text is not None else json.dumps(self._d)

    def json(self):
        return self._d


def ok_chat(text="réponse", finish="stop", pin=12, pout=3):
    return FakeResp(200, {"choices": [{"message": {"content": text}, "finish_reason": finish}], "usage": {"prompt_tokens": pin, "completion_tokens": pout}})


@pytest.fixture
def env(monkeypatch):
    for k in ENV_KEYS:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("AI_ENABLED", "1")  # un test d'une autre fenêtre a pu l'éteindre dans l'environnement
    return monkeypatch


def test_address_rules_protect_the_key_and_the_mails(env):
    ok = config.oai_url_ok
    assert ok("https://api.openai.com/v1") and ok("https://api.mistral.ai/v1") and ok("https://x.openai.azure.com/openai/deployments/d")
    assert ok("http://localhost:11434/v1") and ok("http://192.168.1.20:8000/v1") and ok("http://127.0.0.1:1234/v1") and ok("http://serveur.local:8080/v1")
    assert not ok("http://api.openai.com/v1") and not ok("http://example.com/v1")  # jamais en clair vers Internet
    assert not ok("ftp://x") and not ok("https://user:mdp@x.fr/v1") and not ok("") and not ok("pas une adresse")
    assert config.oai_base_url() == config.OAI_DEFAULT_URL  # rien de réglé : OpenAI
    env.setenv("OAI_BASE_URL", "https://api.mistral.ai/v1/")
    assert config.oai_base_url() == "https://api.mistral.ai/v1"
    env.setenv("OAI_BASE_URL", "http://example.com/v1")
    assert config.oai_base_url() == "" and not config.provider_ready("openai")  # adresse refusée : pas de repli vers OpenAI, la clé ne part nulle part
    env.setenv("OAI_API_KEY", "sk-x")
    assert not config.provider_ready("openai")


def test_provider_readiness_and_model_selection(env):
    assert not config.provider_ready("openai") and not config.ai_has_key()
    env.setenv("OAI_API_KEY", "sk-x")
    assert config.provider_ready("openai") and config.ai_has_key()
    env.setenv("AI_PROVIDER", "openai")
    assert config.model("ASK") == "oai:" + config.OAI_DEFAULT_MODEL
    env.setenv("MODEL2_ASK", "gpt-4.1")
    assert config.model("ASK") == "oai:gpt-4.1" and config.model("ASK", "anthropic") == "claude-haiku-4-5"
    env.setenv("PROVIDER_ASK", "anthropic")
    assert config.model("ASK") == "claude-haiku-4-5" and config.model("BRIEF") == "oai:" + config.OAI_DEFAULT_MODEL


def test_a_local_server_needs_no_key_but_must_be_set_by_you(env):
    assert not config.provider_ready("openai")
    env.setenv("OAI_BASE_URL", "http://localhost:11434/v1")
    assert config.provider_ready("openai") and config.oai_is_local()  # Ollama : pas de clé
    env.setenv("OAI_BASE_URL", "https://api.mistral.ai/v1")
    assert not config.provider_ready("openai")  # un service sur Internet exige une clé


def test_request_shape_for_openai_and_for_other_services(env):
    env.setenv("OAI_API_KEY", "sk-x")
    sent = {}

    def post(url, headers=None, json=None, timeout=None, **kw):
        sent.update(url=url, auth=headers.get("Authorization"), body=json, kw=kw)
        return ok_chat("bonjour")
    env.setattr(httpx, "post", post)
    assert ai._ask("consigne", "message", model="oai:gpt-4o-mini", feature="ask", max_tokens=500) == "bonjour"
    assert sent["url"] == "https://api.openai.com/v1/chat/completions" and sent["auth"] == "Bearer sk-x"
    assert sent["body"]["model"] == "gpt-4o-mini" and sent["body"]["max_completion_tokens"] == 500 and "max_tokens" not in sent["body"]
    assert sent["body"]["messages"][0] == {"role": "system", "content": sent["body"]["messages"][0]["content"]} and sent["body"]["messages"][1]["content"].endswith("message")
    assert sent["kw"].get("follow_redirects") is False and sent["kw"].get("verify")  # certificat vérifié, aucune redirection suivie
    env.setenv("OAI_BASE_URL", "https://api.mistral.ai/v1")
    ai._ask("s", "u", model="oai:mistral-small-latest", feature="ask", max_tokens=300)
    assert sent["url"] == "https://api.mistral.ai/v1/chat/completions" and sent["body"]["max_tokens"] == 300 and "max_completion_tokens" not in sent["body"]
    env.setenv("OAI_BASE_URL", "http://localhost:11434/v1")
    env.delenv("OAI_API_KEY")
    ai._ask("s", "u", model="oai:llama3", feature="ask")
    assert sent["url"] == "http://localhost:11434/v1/chat/completions" and sent["auth"] is None  # sans clé : pas d'en-tête d'autorisation


def test_reasoning_models_get_room_to_think(env):
    env.setenv("OAI_API_KEY", "sk-x")
    sent = {}
    env.setattr(httpx, "post", lambda url, headers=None, json=None, timeout=None, **kw: sent.update(b=json) or ok_chat())
    ai._ask("s", "u", model="oai:gpt-5-mini", feature="ask", max_tokens=300)
    assert sent["b"]["max_completion_tokens"] == 4000  # la réflexion consomme la limite : sans marge, la réponse arrive vide
    ai._ask("s", "u", model="oai:gpt-4o-mini", feature="ask", max_tokens=300)
    assert sent["b"]["max_completion_tokens"] == 300


def test_other_name_of_the_token_limit_is_retried_once(env):
    env.setenv("OAI_API_KEY", "sk-x")
    calls = []

    def post(url, headers=None, json=None, timeout=None, **kw):
        calls.append(sorted(k for k in json if k.startswith("max")))
        if "max_completion_tokens" in json:
            return FakeResp(400, {"error": {"message": "Unsupported parameter: 'max_completion_tokens'."}})
        return ok_chat("ok")
    env.setattr(httpx, "post", post)
    assert ai._ask("s", "u", model="oai:gpt-4o-mini", feature="ask") == "ok"
    assert calls == [["max_completion_tokens"], ["max_tokens"]]


def test_usage_is_recorded_and_costed_when_the_price_is_known(env):
    env.setenv("OAI_API_KEY", "sk-x")
    env.setattr(httpx, "post", lambda *a, **k: ok_chat("ok", pin=1000, pout=500))
    ai._ask("s", "u", model="oai:gpt-4o-mini", feature="ask")
    with db.conn() as c:
        r = c.execute("SELECT model, calls, tokens_in, tokens_out, credits FROM usage_log").fetchone()
    assert (r["model"], r["calls"], r["tokens_in"], r["tokens_out"], r["credits"]) == ("oai:gpt-4o-mini", 1, 1000, 500, 0)
    assert aipolicy.cost_usd("oai:gpt-4o-mini", 10**6, 10**6) == pytest.approx(0.75)  # tarif public d'OpenAI
    assert aipolicy.cost_usd("oai:gpt-4o-mini-2024-07-18", 10**6, 0) == pytest.approx(0.15)  # nom daté : le modèle de base
    assert aipolicy.cost_usd("oai:gpt-4o", 10**6, 0) == pytest.approx(2.5)  # « gpt-4o » n'est pas pris pour « gpt-4o-mini »
    assert aipolicy.cost_usd("oai:inconnu-9", 10**6, 10**6) == 0.0  # prix inconnu : pas de chiffre inventé
    env.setenv("OAI_BASE_URL", "https://api.mistral.ai/v1")
    assert aipolicy.cost_usd("oai:gpt-4o-mini", 10**6, 10**6) == 0.0  # un autre service : les tarifs d'OpenAI ne s'appliquent pas
    env.setenv("OAI_PRICE_IN", "1")
    env.setenv("OAI_PRICE_OUT", "3")
    assert aipolicy.cost_usd("oai:mistral-small", 10**6, 10**6) == pytest.approx(4.0)  # vos tarifs l'emportent


def test_errors_are_explained_and_flag_the_fallback(env):
    env.setenv("OAI_API_KEY", "sk-x")
    cases = [(401, {"error": {"message": "Incorrect API key"}}, "Clé API refusée", True), (429, {"error": {"message": "slow down", "code": "rate_limit"}}, "débit", True),
             (429, {"error": {"message": "You exceeded your current quota", "code": "insufficient_quota"}}, "quota", True), (503, {}, "indisponible", True),
             (404, {"error": {"message": "The model `x` does not exist"}}, "Modèle ou adresse introuvable", False), (400, {"error": {"message": "bad"}}, "refusé la demande", False)]
    for status, data, text, fallback in cases:
        env.setattr(httpx, "post", lambda *a, _s=status, _d=data, **k: FakeResp(_s, _d))
        with pytest.raises(ai.AIError, match=text) as e:
            oaicompat.chat_full("gpt-4o-mini", "s", "u")
        assert e.value.fallback is fallback, (status, text)
    env.setattr(httpx, "post", lambda *a, **k: (_ for _ in ()).throw(httpx.ConnectError("x")))
    with pytest.raises(ai.AIError, match="injoignable") as e:
        oaicompat.chat_full("gpt-4o-mini", "s", "u")
    assert e.value.fallback
    env.setattr(httpx, "post", lambda *a, **k: ok_chat("", finish="length"))
    with pytest.raises(ai.AIError, match="limite de jetons"):
        oaicompat.chat_full("gpt-5", "s", "u")
    env.setattr(httpx, "post", lambda *a, **k: ok_chat("x", finish="content_filter"))
    with pytest.raises(ai.AIError, match="modération"):
        oaicompat.chat_full("gpt-4o", "s", "u")
    env.setattr(httpx, "post", lambda *a, **k: FakeResp(200, {"oops": 1}))
    with pytest.raises(ai.AIError, match="illisible"):
        oaicompat.chat_full("gpt-4o", "s", "u")


def test_images_are_sent_as_data_urls_only_to_vision_models(env):
    env.setenv("OAI_API_KEY", "sk-x")
    sent = {}
    env.setattr(httpx, "post", lambda url, headers=None, json=None, timeout=None, **kw: sent.update(b=json) or ok_chat("scan lu"))
    assert ai._ask("s", "u", model="oai:gpt-4o", images=[b"jpg"], feature="ask") == "scan lu"
    content = sent["b"]["messages"][1]["content"]
    assert content[0]["type"] == "image_url" and content[0]["image_url"]["url"].startswith("data:image/jpeg;base64,") and content[-1] == {"type": "text", "text": content[-1]["text"]}
    assert oaicompat.can_see("gpt-4o") and not oaicompat.can_see("llama3") and not oaicompat.can_see("mistral-7b")
    with pytest.raises(ai.AIError, match="ne lit pas les images"):
        ai._ask("s", "u", model="oai:llama3", images=[b"jpg"], feature="ask")  # sans autre fournisseur : on le dit
    env.setenv("OAI_VISION", "1")
    assert oaicompat.can_see("llama3")  # réglage « oui » : le modèle maison lit les images
    env.setenv("OAI_VISION", "0")
    assert not oaicompat.can_see("gpt-4o")


def test_fallback_goes_to_the_other_provider_and_back(env):
    env.setenv("OAI_API_KEY", "sk-x")
    env.setenv("ANTHROPIC_API_KEY", "a")
    env.setenv("AI_PROVIDER", "openai")
    assert ai._alternative("oai:gpt-4o-mini") == "claude-haiku-4-5"
    assert ai._alternative("claude-haiku-4-5") == "oai:" + config.OAI_DEFAULT_MODEL
    env.setenv("ONEMIN_API_KEY", "k")
    assert ai._alternative("oai:gpt-4o-mini") == "claude-haiku-4-5" and ai._alternative("1min:gpt-4o-mini") == "claude-haiku-4-5"
    env.delenv("ANTHROPIC_API_KEY")
    assert ai._alternative("oai:gpt-4o-mini") == "1min:" + config.ONEMIN_DEFAULT_MODEL
    db.kv_set("ai_fallback", "0")
    assert ai._alternative("oai:gpt-4o-mini") is None
    db.kv_set("ai_fallback", "1")
    used = []
    real = ai._run

    def run(model, *a):
        used.append(model)
        return "via claude" if model == "claude-haiku-4-5" else real(model, *a)
    env.setenv("ANTHROPIC_API_KEY", "a")
    env.setattr(ai, "_run", run)
    env.setattr(httpx, "post", lambda *a, **k: FakeResp(401, {"error": {"message": "bad key"}}))
    assert ai._ask("s", "u", model="oai:gpt-4o-mini", feature="ask") == "via claude" and used == ["oai:gpt-4o-mini", "claude-haiku-4-5"]


def test_unset_provider_is_refused_cleanly(env):
    with pytest.raises(ai.AIError, match="non réglé"):
        ai._run("oai:gpt-4o-mini", "s", "u", 100, "low", None, "ask")


def test_models_list_test_and_cache(env):
    env.setenv("OAI_API_KEY", "sk-x")
    hits = []

    def get(url, headers=None, timeout=None, **kw):
        hits.append((url, headers.get("Authorization")))
        return FakeResp(200, {"data": [{"id": "gpt-4o"}, {"id": "gpt-4o-mini"}, {"id": "gpt-4o"}, {"nope": 1}]})
    env.setattr(httpx, "get", get)
    ok, msg = oaicompat.test()
    assert ok and "2 modèles" in msg and hits == [("https://api.openai.com/v1/models", "Bearer sk-x")]
    assert oaicompat.list_models() == ["gpt-4o", "gpt-4o-mini"] and len(hits) == 1  # lu dans le cache
    assert oaicompat.known_models() == ["gpt-4o", "gpt-4o-mini"]
    env.setenv("OAI_BASE_URL", "https://api.mistral.ai/v1")
    assert oaicompat.known_models() == []  # autre adresse : le cache d'OpenAI ne sert pas
    ok, msg = oaicompat.test("autre-cle", "https://api.mistral.ai/v1")
    assert ok and hits[-1] == ("https://api.mistral.ai/v1/models", "Bearer autre-cle")  # essai avec ce qui est saisi, sans l'enregistrer
    ok, msg = oaicompat.test("k", "http://example.com/v1")
    assert not ok and "Adresse" in msg
    env.setattr(httpx, "get", lambda *a, **k: FakeResp(401, {"error": {"message": "bad"}}))
    ok, msg = oaicompat.test("k", "https://api.mistral.ai/v1")
    assert not ok and "refusée" in msg


def test_the_key_goes_to_the_keychain_like_the_others():
    assert "OAI_API_KEY" in config.SECRET_ENV
