import importlib.util
import threading
import urllib.request
from http.server import HTTPServer
from pathlib import Path

import pytest
from mailia import oauth

spec = importlib.util.spec_from_file_location("mock_oauth", Path(__file__).resolve().parent.parent / "tools" / "mock_oauth.py")
mock = importlib.util.module_from_spec(spec); spec.loader.exec_module(mock)


@pytest.fixture
def provider(monkeypatch):
    srv = HTTPServer(("127.0.0.1", 0), mock.H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    monkeypatch.setitem(oauth.PROVIDERS["microsoft"], "auth_url", base + "/authorize")
    monkeypatch.setitem(oauth.PROVIDERS["microsoft"], "token_url", base + "/token")
    monkeypatch.setenv("OAUTH_MICROSOFT_CLIENT_ID", "client-test")
    oauth._cache.clear()
    yield
    srv.shutdown()


def browser(url):
    threading.Thread(target=lambda: urllib.request.urlopen(url, timeout=10).read(), daemon=True).start()


def test_pkce_flow_and_refresh(provider):
    assert oauth.configured("microsoft") and not oauth.configured("google")
    pw = oauth.authorize("microsoft", "moi@outlook.com", open_browser=browser, timeout=15)
    assert pw == "oauth2:microsoft:RT1" and oauth.is_oauth(pw)
    assert oauth.access_token("boite", pw) == "AT2" and oauth.access_token("boite", pw) == "AT2"   # 2e appel : en cache
    assert oauth.xoauth2_string("a@b.fr", "T") == "user=a@b.fr\x01auth=Bearer T\x01\x01"


def test_token_cache_never_crosses_accounts(provider):
    a = oauth.access_token("test", oauth.pack("microsoft", "RT1"))
    with pytest.raises(oauth.OAuthError):
        oauth.access_token("test", oauth.pack("microsoft", "autre-compte"))   # même clé « test », autre jeton : pas de réutilisation
    assert a == "AT2"


def test_refusals_are_clear(provider):
    with pytest.raises(oauth.OAuthError, match="refusé"):
        oauth.access_token("x", oauth.pack("microsoft", "mauvais"))
    with pytest.raises(oauth.OAuthError, match="configurée"):
        oauth.authorize("google")
