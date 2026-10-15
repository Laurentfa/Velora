import subprocess

from mailia import notify


def test_tests_never_pop_real_notifications(monkeypatch):
    called = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: called.append(a))
    assert notify.send("Brouillon à relire", "test : Sujet") is False
    assert not called
