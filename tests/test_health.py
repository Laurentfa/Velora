"""Pastille d'avertissement : problèmes listés, version de l'extension."""
import json
import time
from pathlib import Path

from mailia import chromebridge as cb, health, modules, sync

EXT = Path(__file__).resolve().parent.parent / "chrome_extension"


def test_extension_version_matches_manifest():
    assert json.loads((EXT / "manifest.json").read_text(encoding="utf-8"))["version"] == cb.EXTENSION_VERSION
    assert "async version()" in (EXT / "background.js").read_text(encoding="utf-8")


def test_outdated_extension_warns(monkeypatch):
    monkeypatch.setitem(cb.ext, "version", "ancienne")
    assert "ancienne" in cb.extension_outdated()
    monkeypatch.setitem(cb.ext, "version", cb.EXTENSION_VERSION)
    assert cb.extension_outdated() == ""
    monkeypatch.setitem(cb.ext, "version", "")
    assert cb.extension_outdated() == ""  # pas connectée : ce n'est pas un problème de version


def test_problems_lists_ai_pause_and_module_errors(monkeypatch):
    monkeypatch.setitem(cb.ext, "version", "")
    monkeypatch.setattr(modules, "status", {"a": "ok", "b": "ImportError: x"})
    monkeypatch.setattr(sync, "_ai_pause", {"until": time.monotonic() + 300, "fp": sync._ai_fingerprint(), "why": "clé refusée"})
    out = health.problems()
    assert any("clé refusée" in p for p in out) and any("« b »" in p for p in out) and len(out) == 2
    monkeypatch.setattr(sync, "_ai_pause", {"until": 0.0, "fp": "", "why": ""})
    monkeypatch.setattr(modules, "status", {"a": "ok"})
    assert health.problems() == []
