"""Choix de l'agenda (Exchange / Google) : aiguillage, appel Google, déconnexion. Le réseau est simulé."""
import json
from datetime import datetime

import httpx
import pytest
from mailia import config, gcal, oauth, sync


class R:
    def __init__(self, status=200, data=None):
        self.status_code, self._d, self.content, self.text = status, data or {}, b"x", json.dumps(data or {})

    def json(self):
        return self._d


@pytest.fixture
def g(monkeypatch):
    monkeypatch.setenv("CALENDAR_BACKEND", "google")
    monkeypatch.setenv("OAUTH_GOOGLE_CLIENT_ID", "cid")
    monkeypatch.setenv("OAUTH_GOOGLE_CLIENT_SECRET", "sec")
    monkeypatch.setenv("GCAL_REFRESH_TOKEN", "rt")
    monkeypatch.setattr(oauth, "refresh_access", lambda p, r, s: ("access", r))
    monkeypatch.setattr(sync, "_dry", lambda: False)
    return monkeypatch


def test_default_backend_is_exchange(monkeypatch):
    monkeypatch.delenv("CALENDAR_BACKEND", raising=False)
    assert config.calendar_backend() == "exchange"
    monkeypatch.setenv("CALENDAR_BACKEND", "google")
    assert config.calendar_backend() == "google"


def test_google_event_created_without_invitees(g):
    sent = {}
    g.setattr(httpx, "request", lambda method, url, headers, timeout, **kw: sent.update(m=method, u=url, h=headers, kw=kw) or R(200, {"htmlLink": "https://x"}))
    sync.create_event("testbox", "Réunion", datetime(2026, 10, 12, 9), datetime(2026, 10, 12, 10), "Salle 2", "note")
    assert sent["m"] == "POST" and sent["u"].endswith("/calendars/primary/events") and sent["h"]["Authorization"] == "Bearer access"
    ev = sent["kw"]["json"]
    assert ev["summary"] == "Réunion" and ev["location"] == "Salle 2" and "attendees" not in ev
    assert sent["kw"]["params"]["sendUpdates"] == "none" and ev["start"]["dateTime"].startswith("2026-10-12T09:00:00")


def test_exchange_backend_untouched(monkeypatch):
    monkeypatch.setenv("CALENDAR_BACKEND", "exchange")
    monkeypatch.setattr(sync, "_dry", lambda: False)
    seen = []

    class P:
        def create_event(self, *a):
            seen.append(a)
    monkeypatch.setattr(sync, "provider", lambda k: P())
    sync.create_event("testbox", "T", datetime(2026, 1, 1, 9), datetime(2026, 1, 1, 10))
    assert seen and seen[0][0] == "T"


def test_accepted_invitation_also_added_to_google(g):
    calls = []

    class P:
        def respond_meeting(self, *a):
            calls.append(("ews", a[-1]))
    g.setattr(sync, "provider", lambda k: P())
    g.setattr(httpx, "request", lambda method, url, headers, timeout, **kw: calls.append(("g", kw["json"].get("status"))) or R(200, {}))
    m = {"account": "testbox", "folder": "INBOX", "uid": "1", "subject": "Point", "meeting": json.dumps({"start": "2026-10-12T09:00:00+00:00", "end": "2026-10-12T10:00:00+00:00"})}
    sync.respond_meeting(m, "accept")
    sync.respond_meeting(m, "tentative")
    sync.respond_meeting(m, "decline")
    assert calls == [("ews", "accept"), ("g", None), ("ews", "tentative"), ("g", "tentative"), ("ews", "decline")]


def test_google_errors_are_readable(g):
    g.setattr(httpx, "request", lambda *a, **k: R(403, {"error": {"message": "Google Calendar API has not been used in project 1 before"}}))
    ok, msg = gcal.test()
    assert not ok and "activée" in msg
    g.setattr(httpx, "request", lambda *a, **k: R(401, {}))
    assert "reconnectez" in gcal.test()[1]
    g.delenv("GCAL_REFRESH_TOKEN")
    assert "pas connecté" in gcal.test()[1]


def test_forget_token(g):
    gcal.disconnect()
    assert not gcal.connected()
