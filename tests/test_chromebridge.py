"""Liaison avec l'extension Chrome : le vrai relais (--chrome-host) est lancé comme Chrome le ferait, et une fausse extension répond."""
import json
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from mailia import chromebridge as cb

ROOT = Path(__file__).resolve().parent.parent


def frame(obj):
    d = json.dumps(obj).encode()
    return struct.pack("<I", len(d)) + d


def read_frame(f):
    head = f.read(4)
    return json.loads(f.read(struct.unpack("<I", head)[0])) if len(head) == 4 else None


@pytest.fixture
def bridge(tmp_path, monkeypatch):
    monkeypatch.setattr(cb, "INFO", tmp_path / "chrome_bridge.json")
    monkeypatch.setattr(cb.config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(cb.sys, "platform", "linux")
    monkeypatch.setattr(cb, "_token", "")
    monkeypatch.setattr(cb, "_conn", None)
    cb.start()
    host = subprocess.Popen([sys.executable, str(ROOT / "main.py"), "--chrome-host"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            env={**__import__("os").environ, "MAILIA_HOME": str(tmp_path)})
    # le relais lit le même fichier d'information que Velora
    yield host
    host.kill()


def test_a_request_goes_to_the_extension_and_back(bridge, monkeypatch):
    def extension():
        while True:
            m = read_frame(bridge.stdout)
            if m is None:
                return
            bridge.stdin.write(frame({"id": m["id"], "result": m["cmd"].upper() + ":" + m.get("code", "")}))
            bridge.stdin.flush()
    threading.Thread(target=extension, daemon=True).start()
    end = time.time() + 15
    while not cb.connected() and time.time() < end:
        time.sleep(0.1)
    assert cb.connected()
    assert cb.call("run", "https://prescripteurs.ventilea.fr", code="1+1") == "RUN:1+1"


def test_no_connection_is_a_clear_error(monkeypatch):
    monkeypatch.setattr(cb, "_conn", None)
    with pytest.raises(cb.BridgeError, match="extension"):
        cb.call("find", "https://prescripteurs.ventilea.fr")


def test_the_host_manifest_only_lets_our_extension_start_the_relay(tmp_path, monkeypatch):
    monkeypatch.setattr(cb.config, "DATA_DIR", tmp_path)
    m = json.loads(cb.register().read_text())
    assert m["allowed_origins"] == [f"chrome-extension://{cb.EXTENSION_ID}/"] and m["type"] == "stdio"
    ext = json.loads((ROOT / "chrome_extension" / "manifest.json").read_text())
    import base64, hashlib
    der = base64.b64decode(ext["key"])
    assert "".join(chr(97 + int(c, 16)) for c in hashlib.sha256(der).hexdigest()[:32]) == cb.EXTENSION_ID
    assert ext["host_permissions"] == ["https://prescripteurs.ventilea.fr/*"]


def test_the_chrome_relay_quits_at_once_while_an_update_runs(tmp_path, monkeypatch):
    """Régression : Chrome relançait le relais (Velora.exe --chrome-host) toutes les 5 s pendant la mise à jour ; l'installateur échouait (code 5, fichiers utilisés)."""
    import io
    import os
    from mailia import config
    flag = tmp_path / "update-en-cours.flag"
    monkeypatch.setattr(config, "UPDATE_FLAG", flag)
    monkeypatch.setattr(sys, "stdin", type("In", (), {"buffer": io.BytesIO(b"")})())  # Chrome ferme tout de suite : le relais s'arrête après son démarrage
    monkeypatch.setattr(sys, "stdout", type("Out", (), {"buffer": io.BytesIO()})())
    started = []
    monkeypatch.setattr(cb.threading, "Thread", lambda *a, **k: started.append(1) or type("T", (), {"start": lambda self: None})())
    flag.write_text("1")
    cb.run_host()
    assert not started  # fichier frais : le relais est parti sans rien démarrer
    old = time.time() - config.UPDATE_FLAG_MAX_AGE - 60
    os.utime(flag, (old, old))
    cb.run_host()
    assert started  # fichier ancien (mise à jour interrompue) : ignoré, le relais fonctionne normalement
