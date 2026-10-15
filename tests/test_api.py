import json
import threading

import pytest
from conftest import add_message
from fastapi.testclient import TestClient

from mailia import apicore, apikeys, apiserver, db


@pytest.fixture
def client():
    db.kv_set("api_rest_on", "1"); db.kv_set("api_mcp_on", "1")
    apikeys._buckets.clear(); apikeys._auth_fails.clear(); apicore._inflight.clear()
    return TestClient(apiserver.build_app())


def key(scopes=("read",), channels=("rest",), **kw):
    return apikeys.create("test", list(scopes), list(channels), **kw)[1]


H = lambda k: {"Authorization": "Bearer " + k} if k else {}
BODY = {"account": "testbox", "to": "zzz@exemple.fr", "subject": "Sujet", "text": "Bonjour"}


def test_auth_and_scopes(client):
    assert client.get("/v1/accounts").status_code == 401
    assert client.get("/v1/accounts", headers=H("mia_faux")).status_code == 401
    ro = key()
    assert client.get("/v1/accounts", headers=H(ro)).json()["accounts"][0]["id"] == "testbox"
    r = client.post("/v1/send", json=BODY, headers=H(ro))
    assert r.status_code == 403 and r.json()["error"]["code"] == "scope_missing"
    assert client.get("/v1/accounts", headers=H(key(channels=("mcp",)))).json()["error"]["code"] == "channel_not_allowed"


def test_switches_are_independent(client):
    k = key(channels=("rest", "mcp"))
    db.kv_set("api_rest_on", "0")
    assert client.get("/v1/accounts", headers=H(k)).status_code == 404
    assert client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"}, headers=H(k)).status_code == 200
    db.kv_set("api_mcp_on", "0")
    assert client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"}, headers=H(k)).status_code == 404


def test_read_messages_and_opaque_ids(client):
    add_message("testbox|INBOX|AQMk/+==1", subject="Facture", body="montant 12")
    k = key()
    lst = client.get("/v1/messages", params={"q": "facture"}, headers=H(k)).json()
    assert lst["count"] == 1 and "|" not in lst["messages"][0]["id"] and "/" not in lst["messages"][0]["id"]
    got = client.get(f"/v1/messages/{lst['messages'][0]['id']}", headers=H(k)).json()
    assert got["body_text"] == "montant 12" and got["untrusted_content"] is True


def test_approval_mode_never_sends(client):
    k = key(("read", "draft", "send"))
    r = client.post("/v1/send", json=BODY, headers=H(k))
    assert r.status_code == 202 and r.json()["status"] == "pending_approval"
    assert db.outbox_count() == 0 and db.drafts_count() == 1
    d = db.drafts_list()[0]
    assert d["source"] == "api"


def test_direct_send_limits_and_cancel(client):
    k = key(("read", "draft", "send"), send_mode="direct", daily_send_cap=2, allowed_domains=["exemple.fr"])
    r = client.post("/v1/send", json=BODY, headers=H(k)); assert r.status_code == 201
    oid = r.json()["id"]
    assert client.post("/v1/send", json={**BODY, "to": "x@gmail.com"}, headers=H(k)).json()["error"]["code"] == "recipient_not_allowed"
    assert client.delete(f"/v1/outbox/{oid}", headers=H(k)).json()["status"] == "cancelled"   # annulé : ne compte plus
    assert client.post("/v1/send", json=BODY, headers=H(k)).status_code == 201
    assert client.post("/v1/send", json=BODY, headers=H(k)).status_code == 201
    assert client.post("/v1/send", json=BODY, headers=H(k)).json()["error"]["code"] == "daily_cap"
    assert client.post("/v1/send", json={**BODY, "to": "pas-une-adresse"}, headers=H(k)).status_code in (400, 429)


def test_scheduled_send_time(client):
    k = key(("send",), send_mode="direct")
    r = client.post("/v1/send", json={**BODY, "send_at": "2999-10-15T09:00:00+02:00"}, headers=H(k))
    assert r.status_code == 400 and r.json()["error"]["code"] == "bad_date"   # plus d'un an
    r = client.post("/v1/send", json={**BODY, "send_at": "2026-10-15T09:00:00+02:00"}, headers=H(k)).json()
    assert r["send_at"].startswith("2026-10-15T07:00") or r["send_at"] > "2026-10-15"   # UTC, ou repoussé après le délai d'annulation


def test_idempotency_no_duplicate_even_in_parallel(client):
    k = key(("send",), send_mode="direct")
    results = []
    def go():
        results.append(client.post("/v1/send", json={**BODY, "idempotency_key": "K1"}, headers=H(k)))
    ts = [threading.Thread(target=go) for _ in range(4)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert db.outbox_count() == 1
    assert {r.status_code for r in results} <= {201, 409}
    assert client.post("/v1/send", json={**BODY, "idempotency_key": "K1"}, headers=H(k)).json().get("idempotent_replay")


def test_caps_hold_under_parallel_requests(client):
    k = key(("send",), send_mode="direct", daily_send_cap=3)
    codes = []
    def go(i):
        codes.append(client.post("/v1/send", json={**BODY, "subject": f"s{i}"}, headers=H(k)).status_code)
    ts = [threading.Thread(target=go, args=(i,)) for i in range(8)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert codes.count(201) == 3 and db.outbox_count() == 3


def test_other_keys_cannot_touch_my_drafts(client):
    a, b = key(("draft",)), key(("draft",))
    did = client.post("/v1/drafts", json=BODY, headers=H(a)).json()["id"]
    assert client.get(f"/v1/drafts/{did}", headers=H(b)).status_code == 404
    assert client.delete(f"/v1/drafts/{did}", headers=H(b)).status_code == 404
    assert client.get(f"/v1/drafts/{did}", headers=H(a)).status_code == 200


def test_no_delete_and_scope_for_actions(client):
    mid = add_message()
    enc = apicore.enc(mid)
    assert client.post(f"/v1/messages/{enc}/actions", json={"action": "archive"}, headers=H(key())).status_code == 403
    r = client.post(f"/v1/messages/{enc}/actions", json={"action": "delete"}, headers=H(key(("manage",))))
    assert r.json()["error"]["code"] == "delete_not_supported"


def test_failed_auth_is_throttled_and_logged(client):
    for _ in range(apikeys.MAX_AUTH_FAILS):
        assert client.get("/v1/accounts", headers=H("mia_x")).status_code == 401
    assert client.get("/v1/accounts", headers=H("mia_x")).json()["error"]["code"] == "too_many_failures"
    assert any(r["action"] == "auth_failed" for r in db.api_log_list(50))


def test_rate_limit(client):
    k = key()
    codes = [client.get("/v1/accounts", headers=H(k)).status_code for _ in range(apikeys.RATE_PER_MINUTE + 5)]
    assert codes.count(429) == 5


def test_host_header_and_docs(client):
    assert client.get("/v1/health", headers={"Host": "evil.example.com"}).status_code == 400
    r = client.get("/docs"); assert r.status_code == 200 and "cdn" not in r.text.lower() and "jsdelivr" not in r.text
    assert client.get("/openapi.json").json()["paths"]


def test_attachment_filename_is_sanitized(client, monkeypatch):
    mid = add_message(att_json=json.dumps([{"id": "1", "name": 'a"\r\nb.pdf', "size": 3, "type": "x"}]), has_att=1)
    monkeypatch.setattr("mailia.sync.fetch_attachment", lambda m, att: (b"abc", 'a"\r\nb.pdf'))
    r = client.get(f"/v1/messages/{apicore.enc(mid)}/attachments/1", headers=H(key()))
    assert r.status_code == 200 and "\r" not in r.headers["content-disposition"] and "\n" not in r.headers["content-disposition"]


# ---------- MCP ----------
def rpc(client, k, method, params=None, id=1):
    return client.post("/mcp", json={"jsonrpc": "2.0", "id": id, "method": method, "params": params or {}}, headers=H(k))


def test_mcp_tools_follow_scopes_and_hide_excluded(client):
    ro, full = key(channels=("mcp",)), key(("read", "draft", "send", "ai", "manage"), ("mcp",))
    names = lambda k: [t["name"] for t in rpc(client, k, "tools/list").json()["result"]["tools"]]
    assert "mail_send" not in names(ro) and "mail_send" in names(full)
    assert rpc(client, ro, "initialize", {"protocolVersion": "2025-06-18"}).json()["result"]["serverInfo"]["name"] == "velora"
    assert client.post("/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"}, headers=H(ro)).status_code == 202
    from mailia import aipolicy
    aipolicy.set_excluded_patterns(["secret.fr"])
    mid = add_message("testbox|INBOX|9", sender="x@secret.fr", subject="Confidentiel", body="contenu très privé")
    res = rpc(client, full, "tools/call", {"name": "mail_get", "arguments": {"message_id": apicore.enc(mid)}}).json()["result"]
    text = res["content"][0]["text"]
    assert "très privé" not in text and "Confidentiel" not in text and "masqué" in text
    rest = key(("read",), ("rest", "mcp"))
    assert "très privé" in client.get(f"/v1/messages/{apicore.enc(mid)}", headers=H(rest)).text   # la clé REST (un programme) n'est pas une IA


def test_mcp_origin_and_unknown(client):
    k = key(channels=("mcp",))
    assert client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"}, headers={**H(k), "Origin": "https://evil.example.com"}).status_code == 403
    assert rpc(client, k, "bidon").json()["error"]["code"] == -32601
    assert rpc(client, None, "ping").status_code == 401


def test_idempotency_key_header_and_body_are_equivalent(client):
    k = key(("read", "draft", "send"), send_mode="direct", daily_send_cap=5)
    body = {"account": "testbox", "to": "zzz@exemple.fr", "subject": "Sujet", "text": "Bonjour"}
    a = client.post("/v1/send", json=body, headers={**H(k), "Idempotency-Key": "abc"})
    b = client.post("/v1/send", json={**body, "idempotency_key": "abc"}, headers=H(k))
    assert a.json()["id"] == b.json()["id"] and b.json()["idempotent_replay"] is True and db.outbox_count() == 1


def test_unexpected_errors_are_clean_502_not_500(client, monkeypatch):
    from mailia import sync
    add_message("testbox|INBOX|9", subject="X")
    k = key(("read", "manage"))
    mid = client.get("/v1/messages", headers=H(k)).json()["messages"][0]["id"]
    monkeypatch.setattr(sync, "perform", lambda *a: (_ for _ in ()).throw(OSError("réseau coupé")))
    r = client.post(f"/v1/messages/{mid}/actions", json={"action": "read"}, headers=H(k))
    assert r.status_code == 502 and r.json()["error"]["code"] == "mailbox_unreachable" and "réseau" not in r.text
