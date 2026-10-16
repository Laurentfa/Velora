"""Zoom de l'interface et densité de la liste : paliers, mémorisation, application à la fenêtre native, réglages."""
import threading
import time

from nicegui import ui
from nicegui.testing import User

import main
from mailia import db, native_menu, ui_zoom
from mailia.ui_settings import _tab_aff


def test_steps_stay_inside_the_range_and_zero_resets():
    assert ui_zoom.zoom() == 1.0
    assert ui_zoom.step(-1) == 0.9 and ui_zoom.step(-1) == 0.8 and ui_zoom.step(-1) == 0.7
    assert ui_zoom.step(-1) == 0.7  # plancher
    assert ui_zoom.step(0) == 1.0
    for _ in range(10):
        ui_zoom.step(1)
    assert ui_zoom.zoom() == 1.5  # plafond
    assert db.kv_get("ui_zoom") == "1.5"


def test_odd_values_snap_to_the_nearest_step():
    assert ui_zoom.set_zoom(0.83) == 0.8 and ui_zoom.set_zoom(5) == 1.5 and ui_zoom.set_zoom(0) == 0.7
    db.kv_set("ui_zoom", "n'importe quoi")
    assert ui_zoom.zoom() == 1.0
    assert ui_zoom.label(0.9) == "90 %"


def test_keyboard_shortcuts_need_cmd_or_ctrl_and_cover_plus_minus_zero():
    js = ui_zoom.KEYS_JS
    assert "e.metaKey || e.ctrlKey" in js and "e.altKey" in js and "'+'" in js and "'='" in js and "'-'" in js and "Digit0" in js and "Numpad0" in js  # le 0 est reconnu par sa touche (clavier français : e.key vaut « à »)
    assert "preventDefault" in js and "uizoom" in js and "__zoomKeys" in js  # un seul écouteur, même si la page est reconstruite


def run_watcher(apply, wait_for, timeout=3.0):
    stop = threading.Event()
    t = threading.Thread(target=native_menu.watch_zoom, args=(stop, 0.02), daemon=True)
    t.start()
    end = time.time() + timeout
    while time.time() < end and not wait_for():
        time.sleep(0.02)
    stop.set()
    t.join(2)


def test_native_window_follows_the_chosen_zoom(monkeypatch):
    applied = []
    monkeypatch.setattr(native_menu, "apply_zoom", lambda z: applied.append(z))
    ui_zoom.set_zoom(0.8)
    run_watcher(applied, lambda: applied == [0.8])
    assert applied == [0.8] and db.kv_get("ui_zoom_status") == "ok : 80 %"  # le zoom mémorisé est appliqué dès l'ouverture de la fenêtre


def test_nothing_is_applied_while_the_zoom_stays_at_100(monkeypatch):
    applied = []
    monkeypatch.setattr(native_menu, "apply_zoom", lambda z: applied.append(z))
    run_watcher(applied, lambda: False, timeout=0.3)
    assert applied == []


def test_changes_after_start_are_applied_including_the_return_to_100(monkeypatch):
    applied = []
    monkeypatch.setattr(native_menu, "apply_zoom", lambda z: applied.append(z))
    stop = threading.Event()
    t = threading.Thread(target=native_menu.watch_zoom, args=(stop, 0.02), daemon=True)
    t.start()
    time.sleep(0.15)
    ui_zoom.set_zoom(0.7)
    time.sleep(0.2)
    ui_zoom.set_zoom(1.0)
    time.sleep(0.2)
    stop.set()
    t.join(2)
    assert applied == [0.7, 1.0]


def test_a_failure_is_reported_then_retried_until_it_works(monkeypatch):
    calls = []

    def flaky(z):
        calls.append(z)
        if len(calls) < 3:
            raise RuntimeError("fenêtre pas prête")
    monkeypatch.setattr(native_menu, "apply_zoom", flaky)
    ui_zoom.set_zoom(0.9)
    run_watcher(calls, lambda: len(calls) >= 3 and (db.kv_get("ui_zoom_status") or "").startswith("ok"))
    assert len(calls) >= 3 and db.kv_get("ui_zoom_status") == "ok : 90 %"


def test_density_is_remembered():
    assert not ui_zoom.compact()
    db.kv_set("list_density", "compact")
    assert ui_zoom.compact()


def test_compact_css_rules_exist():
    from mailia.ui_assets import CSS
    assert "body.compact .mrow" in CSS and "body.compact .mrow .sum:not(.snip)" in CSS  # le résumé disparaît, l'extrait de recherche reste


async def test_settings_offer_zoom_and_density(user: User) -> None:
    @ui.page("/aff")
    def page():
        _tab_aff({"CONVERSATIONS": True, "SIG_ON": True, "SIG_HTML": ""})

    await user.open("/aff")
    await user.should_see(marker="ui-zoom")
    await user.should_see(marker="density-switch")
    user.find(marker="density-switch").elements.pop().set_value(True)
    assert ui_zoom.compact()


async def test_filter_menu_has_the_density_choice(user: User) -> None:
    from datetime import datetime
    ui.page("/")(main.index_page)
    db.kv_set("brief_day", datetime.now().strftime("%Y-%m-%d"))
    await user.open("/")
    await user.should_see(marker="density-normal")
    (compact,) = user.find(marker="density-compact").elements
    compact.parent_slot.parent  # l'entrée est bien dans le menu de la liste
    with user.client:
        for listener in compact._event_listeners.values():
            if listener.type == "click":
                from nicegui import events
                events.handle_event(listener.handler, events.ClickEventArguments(sender=compact, client=user.client))
    assert ui_zoom.compact()  # un clic sur « Compacte » mémorise la densité
