"""Chat de compagnie : réglages (désactivé par défaut), script valide et embarqué dans les installateurs, affichage dans les Paramètres."""
import shutil
import subprocess
from pathlib import Path

import pytest
from nicegui import ui
from nicegui.testing import User

from mailia import db, ui_pet
from mailia.ui_settings import open_settings

ROOT = Path(__file__).resolve().parent.parent


def test_the_pet_is_off_by_default_and_settings_are_clamped() -> None:
    c = ui_pet.config()
    assert c == {"on": False, "size": .5, "follow": False, "mood": "normal"}
    db.kv_set("pet_on", "1"); db.kv_set("pet_size", "500"); db.kv_set("pet_mood", "n'importe quoi"); db.kv_set("pet_follow", "1")
    assert ui_pet.config() == {"on": True, "size": .9, "follow": True, "mood": "normal"}
    db.kv_set("pet_size", "abc")
    assert ui_pet.config()["size"] == .5


def test_the_script_is_valid_and_self_contained(tmp_path) -> None:
    js = (ui_pet.DIR / "pet.js").read_text(encoding="utf-8")
    assert "window.veloraPet" in js and "prefers-reduced-motion" in js and "fetch(" not in js and "XMLHttpRequest" not in js and "eval(" not in js  # aucun réseau
    assert "getBBox" not in js  # sur un calque masqué la mesure renvoie 0 : le chat couché sortait de l'écran
    node = shutil.which("node")
    if node:
        r = subprocess.run([node, "--check", str(ui_pet.DIR / "pet.js")], capture_output=True, text=True)
        assert r.returncode == 0, r.stderr[:400]


@pytest.mark.skipif(not shutil.which("node"), reason="Node n'est pas installé")
def test_the_script_builds_and_runs_a_frame_in_a_minimal_dom(tmp_path) -> None:
    """Le script s'exécute avec un faux DOM : il construit le chat, accepte les réglages et dessine une image sans erreur."""
    harness = tmp_path / "run.js"
    harness.write_text(r'''
const mk = (tag) => ({tag, attrs: {}, style: {}, children: [], listeners: {}, setAttribute(k, v) { this.attrs[k] = v; }, getAttribute(k) { return this.attrs[k]; },
  appendChild(c) { this.children.push(c); return c; }, addEventListener(t, f) { this.listeners[t] = f; }, getBBox() { return {x: 0, y: 0, width: 100, height: 50}; }, set textContent(v) { this._t = v; }});
global.window = {innerWidth: 1200, matchMedia: () => ({matches: false}), addEventListener() {}};
global.document = {head: mk('head'), body: mk('body'), createElement: mk, createElementNS: (ns, t) => mk(t), addEventListener() {}};
global.performance = {now: () => 1000};
let frames = 0; global.requestAnimationFrame = (f) => { frames++; return frames; }; global.cancelAnimationFrame = () => {};
global.setTimeout = () => 0; global.matchMedia = window.matchMedia;
require(process.argv[2]);
window.veloraPet.set({on: true, size: .6, follow: true, mood: 'lively'});
const host = document.body.children[0];
if (!host || host.id !== 'velora-pet' || host.style.display !== '') throw new Error('chat non affiché');
window.veloraPet.set({on: false});
if (host.style.display !== 'none') throw new Error('chat non masqué');
console.log('OK', frames);
''', encoding="utf-8")
    r = subprocess.run([shutil.which("node"), str(harness), str(ui_pet.DIR / "pet.js")], capture_output=True, text=True)
    assert r.returncode == 0 and "OK" in r.stdout, (r.stdout + r.stderr)[:600]


def test_the_builds_ship_the_pet_script() -> None:
    assert "mailia/pet" in (ROOT / "build_mac.sh").read_text(encoding="utf-8")
    assert "mailia\\pet" in (ROOT / "build_windows.bat").read_text(encoding="utf-8")
    assert ui_pet.version() > 0
    assert "/pet/pet.js" in (ROOT / "main.py").read_text(encoding="utf-8") and '"/pet"' in (ROOT / "main.py").read_text(encoding="utf-8")


async def test_the_settings_show_the_pet_options_and_save_them(user: User) -> None:
    @ui.page("/settings-pet")
    def page() -> None:
        open_settings(ui.element("div"), lambda acct: None)
    await user.open("/settings-pet")
    await user.should_see("Chat de compagnie")
    user.find(marker="pet-switch").click()
    assert db.kv_get("pet_on") == "1"
    user.find(marker="pet-follow").click()
    assert db.kv_get("pet_follow") == "1"
