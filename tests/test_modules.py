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
    modules.slots["reader"].clear()
    modules.services.clear()
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


def test_reader_widgets_load_and_a_broken_module_leaves_none_behind(mod_dir) -> None:
    make(mod_dir, "outil", "def register(api):\n    api.add_reader_widget(lambda api, box, msg: None)\n")
    make(mod_dir, "casse", "def register(api):\n    api.add_reader_widget(lambda api, box, msg: None)\n    raise RuntimeError('boum')\n")
    for m in ("outil", "casse"):
        modules.set_enabled(m, True)
    db.kv_set("modules_on", "1")
    modules.load_all()
    assert [x["module"] for x in modules.slots["reader"]] == ["outil"]


def test_reader_widgets_are_drawn_in_order(mod_dir) -> None:
    make(mod_dir, "aaa", "def register(api):\n    api.add_reader_widget(lambda api, box, msg: None, order=30)\n")
    make(mod_dir, "zzz", "def register(api):\n    api.add_reader_widget(lambda api, box, msg: None, order=10)\n")
    for m in ("aaa", "zzz"):
        modules.set_enabled(m, True)
    db.kv_set("modules_on", "1")
    modules.load_all()
    drawn = []
    for it in sorted(modules.slots["reader"], key=lambda x: (x.get("order", 100), x["module"])):
        drawn.append(it["module"])
    assert drawn == ["zzz", "aaa"]  # l'ordre demandé l'emporte sur l'ordre alphabétique


def test_a_module_can_use_another_one_s_service_only_when_it_exists(mod_dir) -> None:
    make(mod_dir, "fournisseur", "def register(api):\n    api.provide('dates', lambda ref: {'ok': ref})\n")
    make(mod_dir, "client", "SEEN = []\ndef register(api):\n    SEEN.append(api.service('dates'))\n")
    make(mod_dir, "casse", "def register(api):\n    api.provide('autre', lambda: 1)\n    raise RuntimeError('boum')\n")
    for m in ("fournisseur", "client", "casse"):
        modules.set_enabled(m, True)
    db.kv_set("modules_on", "1")
    modules.load_all()
    assert "autre" not in modules.services  # le service d'un module en panne est retiré
    api_client = modules.ModuleAPI({"id": "client", "dir": mod_dir, "name": "c"})
    assert api_client.service("dates")("x") == {"ok": "x"}
    assert api_client.service("inconnu") is None
    assert modules.ModuleAPI({"id": "fournisseur", "dir": mod_dir, "name": "f"}).service("dates") is None  # un module n'est pas son propre client
    modules.set_enabled("fournisseur", False)
    modules.load_all()
    assert api_client.service("dates") is None  # fournisseur éteint : le client doit fonctionner sans


async def test_a_module_can_ask_the_ai_through_velora(mod_dir, monkeypatch) -> None:
    from mailia import ai, aipolicy
    seen = []
    monkeypatch.setattr(ai, "_ask", lambda system, user, **kw: (seen.append((system, user, kw["feature"])), "réponse")[1])
    make(mod_dir, "alpha", "def register(api):\n    pass\n")
    api = modules.ModuleAPI({"id": "alpha", "dir": mod_dir / "alpha", "name": "Alpha"})
    assert await api.ask_ai("consigne", "faits") == "réponse"
    assert seen == [("consigne", "faits", "module:alpha")]
    assert aipolicy.feature_label("module:alpha") == "Module : alpha" and aipolicy.feature_label("analyze") == "Tri et résumé des mails"


def test_a_module_folder_or_zip_can_be_imported_and_removed(mod_dir, tmp_path) -> None:
    import zipfile
    store = mod_dir / "store"
    store.mkdir()
    monkey = modules.DIR
    modules.DIR = store
    try:
        src = tmp_path / "src" / "mon_module"
        src.mkdir(parents=True)
        (src / "module.json").write_text('{"name": "M"}', encoding="utf-8")
        (src / "module.py").write_text("def register(api): pass\n", encoding="utf-8")
        (src / "__pycache__").mkdir()
        assert modules.import_module(src) == "mon_module"
        assert (store / "mon_module" / "module.py").is_file() and not (store / "mon_module" / "__pycache__").exists()
        with pytest.raises(FileExistsError):
            modules.import_module(src)
        assert modules.import_module(src, replace=True) == "mon_module"

        z = tmp_path / "autre.zip"  # archive faite depuis le dossier : un seul dossier à la racine
        with zipfile.ZipFile(z, "w") as zf:
            zf.write(src / "module.json", "autre/module.json")
            zf.write(src / "module.py", "autre/module.py")
        assert modules.import_module(z) == "autre"

        bad = tmp_path / "bad.zip"
        with zipfile.ZipFile(bad, "w") as zf:
            zf.writestr("../evil/module.json", "{}")
        with pytest.raises(ValueError, match="chemins"):
            modules.import_module(bad)
        empty = tmp_path / "vide"
        empty.mkdir()
        with pytest.raises(ValueError, match="module.json"):
            modules.import_module(empty)

        modules.set_enabled("autre", True)
        modules.remove_module("autre")
        assert not (store / "autre").exists() and "autre" not in modules.enabled_ids()
        with pytest.raises(ValueError):
            modules.remove_module("../store")
    finally:
        modules.DIR = monkey


async def test_settings_can_delete_a_module_after_confirmation(user: User, mod_dir) -> None:
    make(mod_dir, "alpha", "def register(api): pass\n")
    db.kv_set("modules_on", "1")

    @ui.page("/settings-modules-del")
    def page() -> None:
        open_settings(ui.element("div"), lambda acct: None)
    await user.open("/settings-modules-del")
    await user.should_see("Importer un module")
    user.find(marker="module-delete-alpha").click()
    await user.should_see("sans retour possible")
    user.find(marker="module-delete-confirm").click()
    await user.should_see("Module supprimé")
    assert not (mod_dir / "alpha").exists()


def test_a_module_can_be_enabled_and_removed_without_restarting(mod_dir, monkeypatch) -> None:
    make(mod_dir, "alpha", "def register(api):\n    api.add_view('v', 'Alpha', 'star', lambda a: None)\n    api.add_reader_widget(lambda a, m, msg: None)\n    api.provide('s', lambda: 1)\n")
    db.kv_set("modules_on", "1")
    seen = []
    monkeypatch.setattr(modules, "on_change", lambda: seen.append(1))
    modules.apply("alpha")  # pas activé : rien ne se charge
    assert not modules.views and not modules.slots["reader"] and seen == [1]
    modules.set_enabled("alpha", True)
    modules.apply("alpha")
    assert list(modules.views) == ["mod_alpha_v"] and "mod_alpha_v" in VIEWS and len(modules.slots["reader"]) == 1 and "s" in modules.services
    assert modules.status["alpha"] == "ok"
    modules.set_enabled("alpha", False)
    modules.apply("alpha")
    assert not modules.views and "mod_alpha_v" not in VIEWS and not modules.slots["reader"] and not modules.services and "alpha" not in modules.status
    modules.set_enabled("alpha", True)
    modules.apply("alpha")
    modules.apply()  # l'interrupteur général : tout est rechargé, rien n'est en double
    assert len(modules.slots["reader"]) == 1 and len(modules.views) == 1
    db.kv_set("modules_on", "0")
    modules.apply()
    assert not modules.views and not modules.slots["reader"]
