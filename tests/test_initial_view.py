import json
from datetime import datetime, timedelta

from mailia import db, ui_common


def test_brief_once_a_day_then_last_view_restored(monkeypatch):
    monkeypatch.setenv("AI_ENABLED", "1")
    assert ui_common.initial_view() == ("brief", "", "")          # première ouverture du jour
    assert ui_common.initial_view() == ("inbox", "", "")          # fenêtre rechargée : plus de brief
    db.kv_set("ui_last", json.dumps({"view": "unread", "account": "testbox", "folder": ""}))
    assert ui_common.initial_view() == ("unread", "testbox", "")  # on retrouve la vue où l'on était
    db.kv_set("ui_last", json.dumps({"view": "folder", "account": "testbox", "folder": "testbox|Archive"}))
    assert ui_common.initial_view() == ("folder", "testbox", "testbox|Archive")


def test_next_day_shows_brief_again(monkeypatch):
    monkeypatch.setenv("AI_ENABLED", "1")
    db.kv_set("brief_day", (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d"))
    assert ui_common.initial_view()[0] == "brief"
    assert ui_common.initial_view()[0] != "brief"


def test_no_brief_when_ai_disabled_and_bad_state_is_ignored(monkeypatch):
    monkeypatch.setenv("AI_ENABLED", "0")
    assert ui_common.initial_view() == ("inbox", "", "")
    db.kv_set("ui_last", "pas du json")
    assert ui_common.initial_view() == ("inbox", "", "")
    db.kv_set("ui_last", json.dumps({"view": "n-importe-quoi", "account": "x"}))
    assert ui_common.initial_view() == ("inbox", "", "")
    db.kv_set("ui_last", json.dumps({"view": "brief"}))
    assert ui_common.initial_view() == ("inbox", "", "")  # brief mémorisé mais IA coupée
    db.kv_set("ui_last", json.dumps({"view": "unread", "account": "compte-disparu"}))
    assert ui_common.initial_view() == ("unread", "", "")


def test_default_account_is_applied_once_per_launch(monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setenv("AI_ENABLED", "0")
    monkeypatch.setattr(ui_common, "_launched", {"done": False})
    monkeypatch.setattr(ui_common.sync, "ACCOUNTS", {"perso": SimpleNamespace(enabled=True), "pro": SimpleNamespace(enabled=True)})
    db.kv_set("default_account", "perso")
    db.kv_set("ui_last", json.dumps({"view": "unread", "account": "pro", "folder": ""}))
    assert ui_common.initial_view() == ("inbox", "perso", "")  # lancement : la boîte choisie, pas la dernière vue
    db.kv_set("ui_last", json.dumps({"view": "unread", "account": "pro", "folder": ""}))  # l'utilisateur est passé à une autre boîte à la main
    assert ui_common.initial_view() == ("unread", "pro", "")  # une fenêtre rechargée garde la vue : la boîte par défaut ne revient pas


def test_default_account_ignored_when_disabled_or_removed_and_brief_follows_it(monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(ui_common.sync, "ACCOUNTS", {"perso": SimpleNamespace(enabled=False), "pro": SimpleNamespace(enabled=True)})
    db.kv_set("default_account", "perso")
    assert ui_common.default_account() == ""  # désactivée
    db.kv_set("default_account", "disparue")
    assert ui_common.default_account() == ""  # retirée
    db.kv_set("default_account", "pro")
    monkeypatch.setenv("AI_ENABLED", "1")
    monkeypatch.setattr(ui_common, "_launched", {"done": False})
    db.kv_set("brief_day", "")
    assert ui_common.initial_view() == ("brief", "pro", "")  # le brief du jour porte sur cette boîte
