"""Modules personnels : découverte, chargement module par module, isolation des pannes, réglages."""
import json

import pytest
from nicegui import ui
from nicegui.testing import User

from mailia import db, modules
from mailia.ui_common import NOLIST_VIEWS, VIEWS
from mailia.ui_settings import open_settings


def make(tmp_path, name: str, code: str, **meta) -> None:
    d = tmp_path / name
    d.mkdir()
    (d / "module.json").write_text(json.dumps({"name": name.title(), "version": "1.0", "api": 1, **meta}), encoding="utf-8")
    (d / "module.py").write_text(code, encoding="utf-8")


@pytest.fixture
def mod_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(modules, "DIR", tmp_path)
    yield tmp_path
    for vid in list(modules.views):
        VIEWS.pop(vid, None)
        NOLIST_VIEWS.discard(vid)
    modules.views.clear()
    modules.status.clear()
    db.kv_set("modules_on", "0")
    db.kv_set("modules_enabled", "[]")


def test_nothing_is_loaded_until_modules_are_switched_on(mod_dir) -> None:
    make(mod_dir, "alpha", "def register(api):\n    api.add_view('v', 'Alpha', 'A', lambda api: None)\n")
    modules.set_enabled("alpha", True)
    modules.load_all()
    assert modules.views == {}  # interrupteur général éteint
    db.kv_set("modules_on", "1")
    modules.load_all()
    assert list(modules.views) == ["mod_alpha_v"] and VIEWS["mod_alpha_v"][1] == "Alpha" and "mod_alpha_v" in NOLIST_VIEWS and modules.status["alpha"] == "ok"


def test_only_enabled_modules_load_and_a_broken_one_never_blocks_the_others(mod_dir) -> None:
    make(mod_dir, "good", "def register(api):\n    api.add_view('v', 'Bon', 'B', lambda api: None)\n")
    make(mod_dir, "bad", "def register(api):\n    api.add_view('v', 'Mauvais', 'M', lambda api: None)\n    raise RuntimeError('boum')\n")
    make(mod_dir, "off", "def register(api):\n    api.add_view('v', 'Éteint', 'E', lambda api: None)\n")
    make(mod_dir, "future", "def register(api):\n    pass\n", api=99)
    for m in ("good", "bad", "future"):
        modules.set_enabled(m, True)
    db.kv_set("modules_on", "1")
    modules.load_all()
    assert list(modules.views) == ["mod_good_v"]  # la vue à moitié ajoutée du module en panne est retirée, le module éteint n'est pas chargé
    assert modules.status["good"] == "ok" and "boum" in modules.status["bad"] and "plus récente" in modules.status["future"] and "off" not in modules.status


def test_a_folder_without_module_py_is_reported_not_loaded(mod_dir) -> None:
    d = mod_dir / "vide"
    d.mkdir()
    (d / "module.json").write_text("{}", encoding="utf-8")
    info = next(i for i in modules.discover() if i["id"] == "vide")
    assert "module.py" in info["error"]


def test_module_settings_are_kept_per_module(mod_dir) -> None:
    api = modules.ModuleAPI({"id": "alpha", "dir": mod_dir / "alpha", "name": "Alpha"})
    assert api.get("x", 5) == 5
    api.set("x", 7)
    assert api.get("x") == 7 and modules.ModuleAPI({"id": "beta", "dir": mod_dir, "name": "B"}).get("x") is None


async def test_settings_show_the_modules_block_and_the_switch_saves(user: User) -> None:
    @ui.page("/settings-modules")
    def page() -> None:
        open_settings(ui.element("div"), lambda acct: None)
    await user.open("/settings-modules")
    await user.should_see("Modules")
    assert db.kv_get("modules_on") != "1"
    user.find(marker="modules-switch").click()
    assert db.kv_get("modules_on") == "1"
    db.kv_set("modules_on", "0")
