"""Garde-fou de connexion : le script est valide et installé dans la page."""
import shutil
import subprocess
from pathlib import Path

import pytest

from mailia.ui_assets import WATCHDOG_JS

ROOT = Path(__file__).resolve().parent.parent


def test_the_watchdog_is_installed_and_reconnects_then_reloads() -> None:
    assert "ui.add_head_html(WATCHDOG_JS)" in (ROOT / "main.py").read_text(encoding="utf-8")
    assert "s.connect()" in WATCHDOG_JS and "/_velora/alive/" in WATCHDOG_JS and "/_velora/alive/{cid}" in (ROOT / "main.py").read_text(encoding="utf-8")
    assert "location.reload()" in WATCHDOG_JS  # dernier recours si la connexion ne revient pas


@pytest.mark.skipif(not shutil.which("node"), reason="Node n'est pas installé")
def test_the_watchdog_script_is_valid(tmp_path) -> None:
    f = tmp_path / "wd.js"
    f.write_text(WATCHDOG_JS.replace("<script>", "").replace("</script>", ""), encoding="utf-8")
    r = subprocess.run([shutil.which("node"), "--check", str(f)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[:300]
