"""Relevé des performances : mesures, rapport, bouton des paramètres et accès au dossier des données."""
import asyncio
import types

from nicegui import ui
from nicegui.testing import User

from mailia import config, perf, ui_common
from mailia.ui_settings import _tab_use


def test_span_and_report():
    perf.STATS.clear()
    with perf.span("opération de test"):
        pass
    perf.record("opération de test", 0.25)
    assert perf.STATS["opération de test"][0] == 2 and perf.STATS["opération de test"][2] >= 0.25
    text = perf.report({"Disque": "0.01 s"}, "Edg/130 WebView2")
    assert "opération de test" in text and "Disque : 0.01 s" in text and "WebView2" in text and str(config.DATA_DIR) in text


def test_wrap_times_a_function_once():
    perf.STATS.clear()

    M = types.SimpleNamespace(__name__="mailia.fake", f=lambda x: x + 1)
    perf.wrap(M, "f")
    perf.wrap(M, "f")  # deuxième appel : pas de double mesure
    assert M.f(1) == 2 and perf.STATS["fake.f"][0] == 1


def test_benchmark_has_no_personal_content():
    out = perf.benchmark()
    assert len(out) >= 6 and all(isinstance(v, str) for v in out.values())
    assert not (config.DATA_DIR / "mesure.tmp").exists()  # le fichier de mesure est supprimé


async def test_lag_monitor_sees_a_blocked_loop():
    import time
    perf.STATS.clear()
    task = asyncio.create_task(perf.lag_monitor())
    await asyncio.sleep(0.1)
    time.sleep(0.6)  # bloque la boucle
    await asyncio.sleep(0.6)
    task.cancel()
    assert "serveur de l'interface bloqué" in perf.STATS


def test_open_folder_uses_the_system_opener(monkeypatch, tmp_path):
    import subprocess
    import sys
    calls = []
    monkeypatch.setattr(subprocess, "Popen", lambda cmd, *a, **k: calls.append(cmd))
    monkeypatch.setattr(ui_common.os, "startfile", lambda p: calls.append(["startfile", p]), raising=False)
    ui_common.open_folder(tmp_path)
    assert calls and str(tmp_path) in calls[0][-1] if sys.platform != "win32" else calls[0][0] == "startfile"


async def test_settings_buttons(user: User):
    @ui.page("/usage")
    def page():
        _tab_use(ui.row())

    await user.open("/usage")
    await user.should_see(marker="open-data")
    await user.should_see(marker="perf-measure")


def test_origin_is_shared_with_the_window_process_but_not_across_launches(monkeypatch):
    import os
    monkeypatch.setenv("VELORA_T0", f"{os.getppid()}:1234.5")  # posé par le programme principal, dont ce processus est l'enfant
    assert perf._origin() == (1234.5, True)
    monkeypatch.setenv("VELORA_T0", f"{os.getpid()}:99.0")  # même processus
    assert perf._origin() == (99.0, False)
    monkeypatch.setenv("VELORA_T0", "424242:5.0")  # variable laissée par un lancement précédent (ex : l'ancienne version avant une mise à jour)
    t0, child = perf._origin()
    assert t0 > 1e9 and not child and os.environ["VELORA_T0"].startswith(f"{os.getpid()}:")


def test_browser_marks_are_converted_to_seconds_since_launch(monkeypatch):
    monkeypatch.setattr(perf, "T0", 1000.0)
    monkeypatch.setattr(perf, "MARKS", [])
    perf.browser_marks({"origin": 1_005_000, "dcl": 400, "load": 900, "now": 2000})  # le navigateur compte en millisecondes
    got = dict(perf.MARKS)
    assert round(got["navigateur : page demandée"], 1) == 5.0 and round(got["navigateur : document chargé"], 1) == 5.4
    assert round(got["navigateur : interface dessinée à l'écran"], 1) == 7.0
    perf.browser_marks({"origin": 1_999_000, "now": 1})
    assert len(perf.MARKS) == 4  # une seule série, même si la page est rechargée


def test_window_marks_appear_in_the_report_of_the_same_launch_only(monkeypatch):
    import json
    from mailia import db
    monkeypatch.setattr(perf, "MARKS", [("modules chargés", 2.0)])
    db.kv_set("perf:window", json.dumps({"t0": perf.T0, "marks": [["fenêtre native : moteur d'affichage démarré", 4.5], ["autre", 1.0]]}))
    text = perf.report()
    assert "fenêtre native : moteur d'affichage démarré" in text and "autre" not in text
    assert text.index("modules chargés") < text.index("moteur d'affichage")  # triés par heure
    db.kv_set("perf:window", json.dumps({"t0": perf.T0 + 1, "marks": [["fenêtre native : ancien lancement", 9.0]]}))
    assert "ancien lancement" not in perf.report()
