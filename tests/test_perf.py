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
