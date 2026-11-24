"""Modules personnels : découverte, chargement module par module, isolation des pannes, réglages."""
import json

import pytest
from nicegui import ui
from nicegui.testing import User

import main
from mailia import db, modules, sync
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
    modules.slots["sent"].clear(); modules.slots["attachment"].clear()
    modules.current["mailbox"] = None
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


async def test_settings_can_hide_a_module_tile(user: User, mod_dir) -> None:
    from mailia import tile_order
    make(mod_dir, "alpha", "def register(api):\n    api.add_view('v', 'Alpha', 'star', lambda a: None)\n")
    db.kv_set("modules_on", "1")
    modules.set_enabled("alpha", True)
    modules.apply("alpha")

    @ui.page("/settings-modules-tile")
    def page() -> None:
        open_settings(ui.element("div"), lambda acct: None)
    await user.open("/settings-modules-tile")
    await user.should_see("Importer un module")
    assert tile_order.hidden() == set()
    user.find(marker="module-tile-alpha").click()  # masquer
    assert tile_order.hidden() == {"alpha"}
    user.find(marker="module-tile-alpha").click()  # afficher de nouveau
    assert tile_order.hidden() == set()


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


def test_reimport_from_the_installed_folder_is_refused_and_keeps_the_module(mod_dir) -> None:
    make(mod_dir, "alpha", "def register(api): pass\n")
    with pytest.raises(ValueError):
        modules.import_module(mod_dir / "alpha", True)
    assert (mod_dir / "alpha" / "module.py").is_file()  # le module n'a pas été effacé


def test_update_replaces_the_files_from_the_source_folder(mod_dir, tmp_path_factory) -> None:
    src = tmp_path_factory.mktemp("src")
    make(src, "alpha", "VERSION = 2\ndef register(api): pass\n")
    make(mod_dir, "alpha", "VERSION = 1\ndef register(api): pass\n")
    assert modules.import_module(src / "alpha", True) == "alpha"
    assert "VERSION = 2" in (mod_dir / "alpha" / "module.py").read_text(encoding="utf-8")


def _fake_gh(monkeypatch, tmp_path, versions: dict[str, str], ok: bool = True, sign: bool = True, wrong_key: bool = False, tamper: str = "", extra: str = ""):
    """Fait passer « gh api …/zipball » pour une archive GitHub : un dossier racine « compte-depot-<commit> » qui contient les modules et, si sign, le manifeste signé
    (comme sign_modules.py). tamper : un module dont le code est modifié après la signature ; extra : un module qui reçoit un fichier de plus après la signature."""
    import base64
    import hashlib
    import io
    import subprocess
    import zipfile

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from mailia import version
    key = Ed25519PrivateKey.generate()
    pub = lambda k: base64.b64encode(k.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode()  # noqa: E731
    monkeypatch.setattr(version, "MODULES_PUBKEY", pub(Ed25519PrivateKey.generate() if wrong_key else key))
    files = {name: {"module.json": json.dumps({"name": name, "version": v, "api": 1}), "module.py": f"VERSION = '{v}'\ndef register(api): pass\n"} for name, v in versions.items()}
    manifest = {"v": 1, "modules": {n: {"version": versions[n], "files": {f: hashlib.sha256(c.encode()).hexdigest() for f, c in fs.items()}} for n, fs in files.items()}}
    if tamper:
        files[tamper]["module.py"] += "import os  # ajouté après la signature\n"
    if extra:
        files[extra]["porte_derobee.py"] = "x = 1\n"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, fs in files.items():
            for f, c in fs.items():
                z.writestr(f"me-modules-abc123/{name}/{f}", c)
        z.writestr("me-modules-abc123/README.md", "x")
        if sign:
            z.writestr("me-modules-abc123/modules.manifest.json", json.dumps(manifest, sort_keys=True, indent=1))
            z.writestr("me-modules-abc123/modules.manifest.sig", base64.b64encode(key.sign(json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode())).decode())
    monkeypatch.setattr(modules.shutil, "which", lambda n: "/usr/bin/gh")
    monkeypatch.setattr(modules.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0 if ok else 1, buf.getvalue() if ok else b"", b"" if ok else b"HTTP 404"))


@pytest.mark.parametrize("kwargs, text", [({"sign": False}, "pas signé"), ({"wrong_key": True}, "ne correspond pas"), ({"tamper": "alpha"}, "différents"), ({"extra": "alpha"}, "différents")])
def test_update_from_repo_refuses_anything_not_exactly_as_signed(mod_dir, monkeypatch, kwargs, text) -> None:
    make(mod_dir, "alpha", "VERSION = '1.0'\ndef register(api): pass\n", version="1.0")
    _fake_gh(monkeypatch, mod_dir, {"alpha": "1.1"}, **kwargs)
    with pytest.raises(ValueError, match=text):
        modules.update_from_repo("moi/modules")
    assert "1.0" in (mod_dir / "alpha" / "module.py").read_text(encoding="utf-8")  # rien n'a été installé


def test_update_from_repo_needs_a_verification_key(mod_dir, monkeypatch) -> None:
    from mailia import version
    make(mod_dir, "alpha", "VERSION = '1.0'\ndef register(api): pass\n", version="1.0")
    _fake_gh(monkeypatch, mod_dir, {"alpha": "1.1"})
    monkeypatch.setattr(version, "MODULES_PUBKEY", "")
    with pytest.raises(ValueError, match="clé de vérification"):
        modules.update_from_repo("moi/modules")


def test_update_from_repo_updates_only_newer_installed_modules(mod_dir, monkeypatch) -> None:
    make(mod_dir, "alpha", "VERSION = '1.0'\ndef register(api): pass\n", version="1.0")
    make(mod_dir, "beta", "VERSION = '2.0'\ndef register(api): pass\n", version="2.0")
    _fake_gh(monkeypatch, mod_dir, {"alpha": "1.1", "beta": "2.0", "gamma": "9.0"})
    assert modules.update_from_repo("moi/modules") == [("alpha", "1.0", "1.1")]
    assert "1.1" in (mod_dir / "alpha" / "module.py").read_text(encoding="utf-8")
    assert not (mod_dir / "gamma").exists()  # un module non installé n'est pas ajouté


def test_update_from_repo_explains_failures(mod_dir, monkeypatch) -> None:
    with pytest.raises(ValueError):
        modules.update_from_repo("pas un depot")
    monkeypatch.setattr(modules.shutil, "which", lambda n: None)
    with pytest.raises(ValueError, match="gh"):
        modules.update_from_repo("moi/modules")
    _fake_gh(monkeypatch, mod_dir, {}, ok=False)
    with pytest.raises(ValueError, match="inaccessible"):
        modules.update_from_repo("moi/modules")


async def test_zip_uploaded_through_the_settings_page_is_imported(user: User, mod_dir, tmp_path) -> None:
    import io
    import zipfile

    from nicegui.elements.upload_files import create_file_upload
    from starlette.datastructures import Headers, UploadFile

    @ui.page("/settings-modules-zip")
    def page() -> None:
        open_settings(ui.element("div"), lambda acct: None)
    db.kv_set("modules_on", "1")
    try:
        await user.open("/settings-modules-zip")
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("zipme/module.json", json.dumps({"name": "Zipme", "version": "1.0", "api": 1}))
            z.writestr("zipme/module.py", "def register(api): pass\n")
        up = next(u for u in user.find(kind=ui.upload).elements if u.props.get('accept') == '.zip')  # la page a d'autres envois de fichiers
        f = await create_file_upload(UploadFile(io.BytesIO(buf.getvalue()), filename="zipme.zip", headers=Headers({"content-type": "application/zip"})))
        await up.handle_uploads([f])
        await user.should_see("Module « zipme » importé")
        assert (mod_dir / "zipme" / "module.py").is_file()
    finally:
        db.kv_set("modules_on", "0")


def test_an_archive_with_several_modules_imports_them_all(mod_dir, tmp_path_factory) -> None:
    import zipfile
    out = tmp_path_factory.mktemp("zip")
    z = out / "Velora-modules.zip"
    with zipfile.ZipFile(z, "w") as f:
        for n in ("un", "deux"):
            f.writestr(f"{n}/module.json", json.dumps({"name": n, "version": "1.0", "api": 1}))
            f.writestr(f"{n}/module.py", "def register(api): pass\n")
        f.writestr("README.md", "x")
    assert sorted(modules.import_bundle(z)) == ["deux", "un"]
    with pytest.raises(FileExistsError, match="un"):
        modules.import_bundle(z)
    assert sorted(modules.import_bundle(z, True)) == ["deux", "un"]
    single = out / "s.zip"
    with zipfile.ZipFile(single, "w") as f:
        f.writestr("seul/module.json", json.dumps({"name": "seul", "version": "1.0", "api": 1}))
        f.writestr("seul/module.py", "def register(api): pass\n")
    assert modules.import_bundle(single) == ["seul"]
    with pytest.raises(ValueError):
        modules.import_bundle(out)  # un dossier sans aucun module


async def test_module_tools_sit_under_the_ai_summary_and_above_the_mail_buttons_and_survive_a_redraw(user, monkeypatch) -> None:
    """La fiche patient et la recherche (outils de modules) viennent juste sous le résumé IA, avant les boutons répondre / transférer, et ne sont PAS reconstruites quand
    le panneau se redessine (clic sur Répondre) ; un autre message les reconstruit."""
    import asyncio
    from tests.conftest import add_message
    monkeypatch.setattr(sync, "run_cycle", lambda: None)
    built = []
    modules.slots["reader"].clear()
    modules.slots["reader"].append({"module": "outil", "order": 100, "build": lambda api, mailbox, msg: (built.append(msg["id"]), ui.label("OUTIL-DE-MODULE").mark("outil-module"))})
    try:
        ui.page("/")(main.index_page)
        db.kv_set("brief_day", __import__("datetime").datetime.now().strftime("%Y-%m-%d"))
        boxes = []
        real = main.Mailbox.__init__
        monkeypatch.setattr(main.Mailbox, "__init__", lambda self, *a, **k: (real(self, *a, **k), boxes.append(self))[0])
        one = add_message(mid="testbox|INBOX|1", summary="Résumé de test", category="info", priority=1, needs_reply=0, suggested="", ai_at="2026-01-01T00:00:00")
        two = add_message(mid="testbox|INBOX|2", subject="Autre", summary="Autre résumé", category="info", priority=1, needs_reply=0, suggested="", ai_at="2026-01-01T00:00:00")
        await user.open("/")
        mb = boxes[-1]

        def dom_order() -> list[str]:
            """Éléments repérés, dans l'ordre où ils sont dessinés dans la page."""
            found = []

            def walk(el):
                mark = " ".join(str(x) for x in getattr(el, "_markers", []) or [])
                text = getattr(el, "text", "") or ""
                for key in ("outil-module", "reply"):
                    if key in mark:
                        found.append(key)
                if text == "Résumé de test":
                    found.append("summary")
                for ch in el.default_slot.children:
                    walk(ch)
            walk(mb.root.client.layout)
            return found[len(found) - 1 - found[::-1].index("summary"):] if "summary" in found else found  # la liste des mails a aussi un « résumé » et des boutons : on ne garde que le panneau de lecture
        await mb.open(one)
        await asyncio.sleep(0.5)
        order = dom_order()
        assert order[0] == "summary" and order.index("outil-module") < order.index("reply"), order  # sous le résumé, avant « Répondre »
        label = [e for e in user.find(marker="outil-module").elements][0]
        mb.reader.refresh()  # ce que fait un clic sur Répondre, Transférer, Reporter…
        await asyncio.sleep(0.5)
        order = dom_order()
        assert order[0] == "summary" and order.count("outil-module") == 1 and order.index("outil-module") < order.index("reply"), order
        assert [e.id for e in user.find(marker="outil-module").elements] == [label.id] and built == [one]  # le même élément, jamais reconstruit
        await mb.forward(mb.cur)  # Transférer : le panneau devient l'éditeur du nouveau message
        await asyncio.sleep(1.0)
        assert mb.new and [e.id for e in user.find(marker="outil-module").elements] == [label.id] and built == [one], (mb.new, built)  # la fiche est restée la même
        await mb.open(two)
        await asyncio.sleep(0.5)
        assert built == [one, two] and dom_order().count("outil-module") == 1  # un autre message : outils reconstruits, l'ancien est supprimé
    finally:
        modules.slots["reader"].clear()


async def test_a_module_view_takes_the_width_of_the_list_and_the_reader_and_a_mail_view_gets_it_back(user, monkeypatch, mod_dir) -> None:
    import asyncio
    make(mod_dir, "large", "def register(api):\n    api.add_view('v', 'Large', 'L', lambda api: None)\n")
    modules.set_enabled("large", True)
    db.kv_set("modules_on", "1")
    modules.load_all()
    calls: list = []
    monkeypatch.setattr(ui, "run_javascript", lambda code, **k: calls.append(code))
    seen: list = []
    init = main.Mailbox.__init__
    monkeypatch.setattr(main.Mailbox, "__init__", lambda self, *a, **k: (init(self, *a, **k), seen.append(self))[0])
    ui.page("/")(main.index_page)
    await user.open("/")
    box = seen[-1]
    await box.go("mod_large_v", "", "")
    await asyncio.sleep(0.4)
    assert any("wide-view" in c and "'wide-view', true" in c for c in calls)  # la vue du module élargie
    calls.clear()
    await box.go("inbox", "", "")
    await asyncio.sleep(0.4)
    assert any("'wide-view', false" in c for c in calls)  # une vue de mails : colonnes habituelles
    from mailia.ui_assets import CSS
    assert "html.wide-view .readercol" in CSS and "@media (min-width:1024px)" in CSS  # seulement sur grand écran : le téléphone garde sa mise en page


# ---------- courrier : un module prépare des brouillons, ne les envoie jamais, et est prévenu quand l'utilisateur envoie ----------
@pytest.fixture
def one_account(monkeypatch):
    """Une boîte connectée (et une suspendue, et une sans mot de passe) ; aucune fonction d'envoi ne peut partir : elles sont des pièges."""
    from mailia import outgoing
    from mailia.config import AccountCfg
    mk = lambda key, pw, enabled=True: AccountCfg(key=key, label=key.title(), kind="ews", email=f"{key}@x.fr", login=key, password=pw, ai_enabled=False, enabled=enabled)  # noqa: E731
    monkeypatch.setattr(sync, "ACCOUNTS", {"a": mk("a", "pw"), "off": mk("off", "pw", False), "nopw": mk("nopw", "")})
    sent: list = []

    def trap(name):
        def f(*a, **k):
            raise AssertionError(f"{name} ne doit jamais être appelé par un module")
        return f
    monkeypatch.setattr(sync, "send_mail", lambda *a, **k: sent.append((a, k)))
    monkeypatch.setattr(outgoing, "queue", trap("outgoing.queue"))
    monkeypatch.setattr(db, "outbox_add", trap("db.outbox_add"))
    return sent


def api_of(tmp_path, mid="demo"):
    return modules.ModuleAPI({"id": mid, "dir": tmp_path / mid, "name": mid.title()})


def test_a_module_sees_only_connected_active_mailboxes(mod_dir, one_account) -> None:
    assert api_of(mod_dir).mail_accounts() == [{"key": "a", "email": "a@x.fr", "label": "A"}]


def test_a_module_prepares_a_draft_and_never_sends_it(mod_dir, one_account) -> None:
    api = api_of(mod_dir)
    did = api.create_draft("a", "dr@x.fr; autre@x.fr", " Suivi ", "<p>Bonjour <script>alert(1)</script><b>Dr</b></p>", [("rapport.pdf", b"%PDF-1.4 x", "application/pdf")], cc="cc@x.fr")
    row, files = db.draft_load(did)
    assert row["account"] == "a" and row["to_addrs"] == "dr@x.fr, autre@x.fr" and row["cc"] == "cc@x.fr" and row["subject"] == "Suivi" and row["source"] == "module:demo"
    assert "<script" not in row["html"] and "Bonjour" in row["html"] and "<b>Dr</b>" in row["html"]  # nettoyé comme tout message sortant
    assert files == [("rapport.pdf", b"%PDF-1.4 x", "application/pdf")] and api.draft_exists(did)
    named = db.draft_load(api.create_draft("a", "", "S", "<p>x</p>", [("../../etc/passwd\n.pdf", b"x", "application/pdf")]))[1]
    assert named[0][0] == ".._.._etc_passwd_.pdf"  # un nom de pièce jointe ne contient jamais de chemin
    assert db.drafts_count() >= 1 and one_account == []  # rien n'est parti
    db.draft_delete(did)
    assert not api.draft_exists(did)


def test_a_draft_may_have_no_recipient_and_no_signature(mod_dir, one_account) -> None:
    api = api_of(mod_dir)
    did = api.create_draft("a", "", "Sans destinataire", "<p>Texte</p>", signature=False)
    row, _ = db.draft_load(did)
    assert row["to_addrs"] == "" and "msig" not in row["html"]
    with_sig = db.draft_load(api.create_draft("a", "", "Avec", "<p>Texte</p>"))[0]["html"]
    assert with_sig.startswith("<p>Texte</p>") and len(with_sig) > len("<p>Texte</p>")  # signature de la boîte ajoutée après le texte


def test_unload_calls_the_module_stop_hook_and_survives_its_failure(mod_dir) -> None:
    calls: list = []

    class Mod:
        def unload(self):
            calls.append("stop")
            raise RuntimeError("échec volontaire")
    modules.objects["demo"] = Mod()
    modules.unload("demo")  # n'échoue pas, même si le module échoue en s'arrêtant
    assert calls == ["stop"] and "demo" not in modules.objects
    modules.objects["demo"] = object()  # sans unload() : rien à appeler
    modules.unload("demo")
    assert "demo" not in modules.objects


def test_a_module_opens_only_its_own_drafts_in_the_editor(mod_dir, one_account) -> None:
    import asyncio
    run = lambda coro: asyncio.new_event_loop().run_until_complete(coro)  # noqa: E731 — sans fermer la boucle d'autres tests
    opened: list = []

    class MB:
        async def go(self, view, acc, folder=""):
            opened.append(("go", view))

        async def resume_draft(self, did):
            opened.append(("resume", did))
    api = api_of(mod_dir)
    did = api.create_draft("a", "", "S", "<p>x</p>")
    with pytest.raises(ValueError):  # pas de fenêtre
        run(api.open_draft(did))
    modules.current["mailbox"] = MB()
    try:
        run(api.open_draft(did))
        assert opened == [("go", "drafts"), ("resume", did)]
        with pytest.raises(ValueError):  # le brouillon d'un autre module (ou inconnu) n'est pas ouvert
            run(api_of(mod_dir, "autre").open_draft(did))
        with pytest.raises(ValueError):
            run(api.open_draft("inconnu"))
    finally:
        modules.current["mailbox"] = None


def test_a_module_prepares_a_reply_draft(mod_dir, one_account, monkeypatch) -> None:
    from mailia import compose, queries
    msg = {"id": "m1", "account": "a", "sender": "dr@x.fr", "subject": "TR : Ordonnance", "date": "2026-01-01"}
    monkeypatch.setattr(queries, "get_message", lambda mid: msg if mid == "m1" else None)
    monkeypatch.setattr(compose, "quote_html", lambda m: "<blockquote>cité</blockquote>")
    api = api_of(mod_dir)
    row, files = db.draft_load(api.create_draft("", "", "", "<p>Voici</p>", [("o.pdf", b"x", "application/pdf")], reply_to_message="m1"))
    assert row["account"] == "a" and row["to_addrs"] == "dr@x.fr" and row["subject"] == "Re : Ordonnance" and row["reply_to"] == "m1" and not row["is_new"]
    assert "cité" in row["html"] and files[0][0] == "o.pdf" and one_account == []
    with pytest.raises(ValueError):
        api.create_draft("a", "", "", "<p>x</p>", reply_to_message="inconnu")


def test_attachment_actions_are_offered_by_extension_and_removed_on_unload(mod_dir) -> None:
    api = api_of(mod_dir)
    api.on_attachment("Modifier", "edit", ("pdf", ".PNG"), lambda info, data: None)
    assert [it["label"] for it in modules.attachment_actions("Facture.PDF")] == ["Modifier"]
    assert modules.attachment_actions("photo.png") and not modules.attachment_actions("note.docx") and not modules.attachment_actions("")
    modules.unload("demo")
    assert not modules.attachment_actions("Facture.pdf")


def test_a_module_scans_with_velora_scanners_and_phone(mod_dir, monkeypatch) -> None:
    from mailia import scan
    calls: list = []
    monkeypatch.setattr(scan, "known", lambda: [{"name": "Brother", "base": "http://10.0.0.5/eSCL"}])
    monkeypatch.setattr(scan, "discover", lambda t=3.0: [{"name": "HP", "base": "http://10.0.0.9/eSCL"}])
    monkeypatch.setattr(scan, "remember", lambda name, base, used=False: calls.append(("remember", name, base, used)))
    monkeypatch.setattr(scan, "capabilities", lambda base: {"name": "Epson"})
    monkeypatch.setattr(scan, "scan", lambda base, source, color, dpi, caps=None: calls.append(("scan", base, source, color, dpi)) or [b"jpeg1", b"jpeg2"])
    api = api_of(mod_dir)
    assert api.scanners() == [{"id": "http://10.0.0.5/eSCL", "name": "Brother"}]
    api.find_scanners(99)
    assert ("remember", "HP", "http://10.0.0.9/eSCL", False) in calls
    assert api.add_scanner("10.0.0.7") == {"id": "http://10.0.0.7/eSCL", "name": "Epson"}
    assert api.scan_pages("http://10.0.0.5/eSCL", "feeder", "gray", 300) == [b"jpeg1", b"jpeg2"]
    assert ("scan", "http://10.0.0.5/eSCL", "feeder", "gray", 300) in calls
    for bad in ({"scanner_id": "http://evil/eSCL"}, {"scanner_id": "http://10.0.0.5/eSCL", "source": "x"}, {"scanner_id": "http://10.0.0.5/eSCL", "dpi": 5}):
        with pytest.raises(ValueError):
            api.scan_pages(**bad)

    def boom(*a, **k):
        raise scan.ScanError("occupé")
    monkeypatch.setattr(scan, "scan", boom)
    with pytest.raises(ValueError, match="occupé"):
        api.scan_pages("http://10.0.0.5/eSCL")
    stopped: list = []

    class Srv:
        class session:
            pages = [b"p1"]
            done = False
            lock = __import__("threading").Lock()
            expired = False

        def url(self): return "http://10.0.0.1:1/s/t"
        def stop(self): stopped.append(1)
    monkeypatch.setattr(scan, "PhoneServer", Srv)
    monkeypatch.setattr(scan, "qr_svg", lambda u: "<svg/>")
    st = api.phone_scan_start()
    assert st["url"].startswith("http://") and st["qr_svg"] == "<svg/>"
    assert api.phone_scan_poll(st["id"], 0) == {"pages": [b"p1"], "seen": 1, "done": False}
    assert api.phone_scan_poll(st["id"], 1)["pages"] == []
    with pytest.raises(ValueError):
        api_of(mod_dir, "autre").phone_scan_poll(st["id"])  # une numérisation n'est visible que de son module
    api.phone_scan_stop(st["id"])
    assert stopped == [1] and not modules.phone_scans
    api.phone_scan_start()
    modules.unload("demo")
    assert len(stopped) == 2 and not modules.phone_scans


@pytest.mark.parametrize("kwargs, text", [
    ({"account": "inconnue"}, "inconnue"), ({"account": "off"}, "off"), ({"account": "nopw"}, "nopw"),
    ({"to": "pas une adresse"}, "Adresse invalide"), ({"cc": "a@b"}, "Adresse invalide"),
    ({"to": ",".join(f"d{i}@x.fr" for i in range(21))}, "Trop de destinataires"),
    ({"html": "  "}, "vide"), ({"files": [("a.pdf", "pas des octets", "application/pdf")]}, "Pièce jointe invalide"), ({"files": [("", b"x", "application/pdf")]}, "Pièce jointe invalide"),
    ({"files": [("gros.pdf", b"x" * (25 * 1024 * 1024 + 1), "application/pdf")]}, "trop lourdes")])
def test_a_module_cannot_create_an_unacceptable_draft(mod_dir, one_account, kwargs, text) -> None:
    base = {"account": "a", "to": "dr@x.fr", "subject": "S", "html": "<p>x</p>"}
    before = len(db.drafts_list())
    with pytest.raises(ValueError, match=text):
        api_of(mod_dir).create_draft(**{**base, **kwargs})
    assert len(db.drafts_list()) == before and one_account == []


def test_module_drafts_are_copied_to_the_server_but_assistant_drafts_stay_local(mod_dir, one_account) -> None:
    api = api_of(mod_dir)
    mine = api.create_draft("a", "dr@x.fr", "Module", "<p>x</p>")
    other = api.create_draft("a", "dr@x.fr", "Assistant", "<p>y</p>")
    db.draft_mark_api(other, "cle", "note")
    pushed = {d["id"] for d in db.drafts_to_push(50)}
    assert mine in pushed and other not in pushed


def test_modules_are_told_when_a_message_is_really_sent(mod_dir, monkeypatch) -> None:
    from datetime import datetime, timedelta, timezone
    seen: list = []
    api = api_of(mod_dir)
    api.on_sent(seen.append)
    modules.slots["sent"].append({"module": "casse", "fn": lambda info: 1 / 0})  # un module en panne ne gêne ni l'envoi ni les autres
    monkeypatch.setattr(sync, "send_mail", lambda *a, **k: seen.append("parti"))
    monkeypatch.setattr(sync, "mark_answered", lambda *a, **k: None)
    past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    db.outbox_add({"id": "o1", "account": "a", "to": "dr@x.fr", "cc": "", "bcc": "", "subject": "Suivi", "html": "<p>x</p>", "body": "x", "draft_id": "d123", "reply_to": None, "send_at": past, "followup_days": 0})
    assert sync.send_due() == 1
    assert seen[0] == "parti" and seen[1] == {"draft_id": "d123", "account": "a", "to": "dr@x.fr", "cc": "", "bcc": "", "subject": "Suivi"}  # ni le texte ni les pièces jointes
    assert db.outbox_get("o1") is None


def test_modules_are_not_told_about_a_message_that_failed_to_send(mod_dir, monkeypatch) -> None:
    from datetime import datetime, timedelta, timezone
    seen: list = []
    api_of(mod_dir).on_sent(seen.append)

    def boom(*a, **k):
        raise RuntimeError("serveur injoignable")
    monkeypatch.setattr(sync, "send_mail", boom)
    past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    db.outbox_add({"id": "o2", "account": "a", "to": "dr@x.fr", "cc": "", "bcc": "", "subject": "S", "html": "<p>x</p>", "body": "x", "draft_id": None, "reply_to": None, "send_at": past, "followup_days": 0})
    assert sync.send_due() == 0 and seen == []
    db.outbox_delete("o2")


def test_unloading_a_module_removes_its_sent_hook(mod_dir) -> None:
    api_of(mod_dir, "tmp").on_sent(lambda info: None)
    assert any(x["module"] == "tmp" for x in modules.slots["sent"])
    modules.unload("tmp")
    assert not any(x["module"] == "tmp" for x in modules.slots["sent"])


# ---------- envoi automatique : réservé aux modules que l'utilisateur a autorisés, éteint par défaut ----------
@pytest.fixture
def mailbox(monkeypatch):
    """Une boîte connectée ; l'envoi réel (sync.send_mail) est un piège : la file d'envoi de Velora, elle, fonctionne (base de test)."""
    from mailia.config import AccountCfg
    monkeypatch.setattr(sync, "ACCOUNTS", {"a": AccountCfg(key="a", label="A", kind="ews", email="a@x.fr", login="a", password="pw", ai_enabled=False, enabled=True)})
    monkeypatch.setattr(sync, "send_mail", lambda *a, **k: (_ for _ in ()).throw(AssertionError("l'envoi réel ne doit pas être appelé : seulement la file")))
    for k in ("modules_send_allowed", "modules_send_cap"):
        db.kv_set(k, "")
    yield "a"
    db.kv_set("modules_send_allowed", "")
    db.kv_set("modules_send_cap", "")


def test_a_module_cannot_send_without_the_users_authorisation(mod_dir, mailbox) -> None:
    api = api_of(mod_dir)
    did = api.create_draft("a", "dr@x.fr", "S", "<p>x</p>")
    assert api.can_send() is False and modules.send_allowed_ids() == set()
    with pytest.raises(PermissionError, match="interrupteur"):
        api.send_draft(did)
    assert db.outbox_list() == [] and api.draft_exists(did)  # rien n'est parti, le brouillon est intact


def test_an_authorised_module_queues_its_own_draft_with_the_undo_delay(mod_dir, mailbox) -> None:
    from datetime import datetime, timedelta, timezone

    from mailia import outgoing
    api = api_of(mod_dir)
    modules.set_send_allowed("demo", True)
    assert api.can_send() and modules.send_allowed_ids() == {"demo"} and not api_of(mod_dir, "autre").can_send()  # l'autorisation est propre à chaque module
    did = api.create_draft("a", "dr@x.fr", "Suivi", "<p>x</p>", [("r.pdf", b"%PDF", "application/pdf")], cc="cc@x.fr")
    before = datetime.now(timezone.utc)
    oid = api.send_draft(did)
    row = db.outbox_get(oid)
    assert row["status"] == "pending" and row["source"] == "module:demo" and row["to_addrs"] == "dr@x.fr" and row["cc"] == "cc@x.fr" and row["subject"] == "Suivi" and row["draft_id"] == did
    assert db.outbox_files(oid) == [("r.pdf", b"%PDF", "application/pdf")]
    assert datetime.fromisoformat(row["send_at"]) >= before + timedelta(seconds=outgoing.undo_delay()) - timedelta(seconds=2)  # jamais avant le délai d'annulation
    assert not api.draft_exists(did)  # le brouillon est devenu un envoi en file (et peut y être annulé)
    later = api.send_draft(api.create_draft("a", "dr@x.fr", "S2", "<p>y</p>"), delay_seconds=10 ** 9)
    assert datetime.fromisoformat(db.outbox_get(later)["send_at"]) <= datetime.now(timezone.utc) + timedelta(days=7, minutes=1)  # report plafonné à 7 jours


def test_a_module_cannot_send_a_draft_it_did_not_prepare(mod_dir, mailbox) -> None:
    api = api_of(mod_dir)
    modules.set_send_allowed("demo", True)
    mine = db.new_draft_id() if hasattr(db, "new_draft_id") else "user-draft"
    db.draft_save({"id": mine, "account": "a", "is_new": True, "reply_to": None, "to": "dr@x.fr", "cc": "", "bcc": "", "subject": "À moi", "html": "<p>x</p>"}, [])
    with pytest.raises(PermissionError, match="pas été préparé par ce module"):
        api.send_draft(mine)
    other = api_of(mod_dir, "autre").create_draft("a", "dr@x.fr", "Autre", "<p>x</p>")
    with pytest.raises(PermissionError, match="pas été préparé par ce module"):
        api.send_draft(other)
    assert db.outbox_list() == []


def test_sending_needs_a_recipient_a_valid_mailbox_and_an_existing_draft(mod_dir, mailbox) -> None:
    api = api_of(mod_dir)
    modules.set_send_allowed("demo", True)
    with pytest.raises(ValueError, match="introuvable"):
        api.send_draft("inconnu")
    with pytest.raises(ValueError, match="destinataire"):
        api.send_draft(api.create_draft("a", "", "Sans destinataire", "<p>x</p>"))
    assert db.outbox_list() == []


def test_the_daily_cap_and_the_revocation_stop_a_module(mod_dir, mailbox) -> None:
    api = api_of(mod_dir)
    modules.set_send_allowed("demo", True)
    db.kv_set("modules_send_cap", "2")
    for i in range(2):
        api.send_draft(api.create_draft("a", "dr@x.fr", f"S{i}", "<p>x</p>"))
    third = api.create_draft("a", "dr@x.fr", "S3", "<p>x</p>")
    with pytest.raises(PermissionError, match="Plafond"):
        api.send_draft(third)
    assert len(db.outbox_list()) == 2 and api.draft_exists(third)
    db.kv_set("modules_send_cap", "")
    modules.set_send_allowed("demo", False)  # l'utilisateur retire l'autorisation : plus rien ne part
    with pytest.raises(PermissionError, match="interrupteur"):
        api.send_draft(third)


def test_a_module_declares_that_it_can_send_and_the_switch_is_off_by_default(mod_dir) -> None:
    (mod_dir / "m1").mkdir()
    (mod_dir / "m1" / "module.json").write_text(json.dumps({"name": "M1", "version": "1", "sends_mail": True}), encoding="utf-8")
    (mod_dir / "m1" / "module.py").write_text("def register(api):\n    pass\n", encoding="utf-8")
    (mod_dir / "m2").mkdir()
    (mod_dir / "m2" / "module.json").write_text(json.dumps({"name": "M2", "version": "1"}), encoding="utf-8")
    (mod_dir / "m2" / "module.py").write_text("def register(api):\n    pass\n", encoding="utf-8")
    found = {i["id"]: i for i in modules.discover()}
    assert found["m1"].get("sends_mail") is True and not found["m2"].get("sends_mail")
    assert "m1" not in modules.send_allowed_ids()  # déclarer ne suffit pas : l'utilisateur doit autoriser


async def test_settings_offers_the_send_switch_only_to_modules_that_declare_it(user: User, mod_dir) -> None:
    make(mod_dir, "alpha", "def register(api): pass\n")
    make(mod_dir, "beta", "def register(api): pass\n", sends_mail=True)
    db.kv_set("modules_on", "1")
    db.kv_set("modules_send_allowed", "")

    @ui.page("/settings-modules-send")
    def page() -> None:
        open_settings(ui.element("div"), lambda acct: None)
    await user.open("/settings-modules-send")
    await user.should_see("Importer un module")
    with pytest.raises(AssertionError):  # pas de déclaration : pas d'interrupteur
        user.find(marker="module-send-alpha")
    user.find(marker="module-send-beta").click()  # allumer demande une confirmation, rien n'est encore autorisé
    await user.should_see("Autoriser « Beta » à envoyer des mails ?")
    assert modules.send_allowed_ids() == set()
    user.find(marker="module-send-confirm").click()
    await user.should_see("Envoi automatique autorisé")
    assert modules.send_allowed_ids() == {"beta"}
    db.kv_set("modules_send_allowed", "")
