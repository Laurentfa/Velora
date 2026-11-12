"""Liaison avec l'extension Chrome « Velora – liaison Ventiléa » (Windows ; le Mac passe par AppleScript).

Chrome lance le relais « --chrome-host » (messagerie native) ; le relais se connecte à Velora sur 127.0.0.1 avec une clé aléatoire gardée dans
le dossier de données. Velora envoie une demande {id, cmd, base, ...}, l'extension l'exécute dans l'onglet Ventiléa déjà connecté et répond."""
import itertools
import json
import os
import secrets
import socket
import struct
import subprocess
import sys
import threading
from pathlib import Path

from . import config

HOST_NAME = "fr.lfauvel.velora"
EXTENSION_ID = "alcjhnemdjbkdfgphajpjhbojmfnlpbo"  # fixé par la clé du manifeste (chrome_extension/manifest.json)
INFO = config.DATA_DIR / "chrome_bridge.json"
EXTENSION_VERSION = "0.1.1"  # version de l'extension livrée avec cette version de Velora (chrome_extension/manifest.json, vérifié par un test)
VENTILEA = "https://prescripteurs.ventilea.fr"
ext: dict = {"conn": None, "version": ""}  # version annoncée par l'extension connectée (« ancienne » si elle ne sait pas répondre)


class BridgeError(Exception):
    pass


# ---------- côté Velora ----------

_lock = threading.Lock()
_conn: socket.socket | None = None
_pending: dict[int, dict] = {}
_ids = itertools.count(1)
_token = ""


def start() -> None:
    """Ouvre l'écoute locale et déclare le relais à Chrome. Sans effet sur Mac (AppleScript), sauf pour un essai : VELORA_CHROME_BRIDGE=1."""
    global _token
    if (sys.platform == "darwin" and not os.environ.get("VELORA_CHROME_BRIDGE")) or _token:
        return
    _token = secrets.token_hex(16)
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(2)
    INFO.write_text(json.dumps({"port": srv.getsockname()[1], "token": _token}), encoding="utf-8")
    try:
        INFO.chmod(0o600)
    except OSError:
        pass
    threading.Thread(target=_accept, args=(srv,), daemon=True, name="chromebridge").start()
    try:
        register()
    except OSError:
        pass


def _accept(srv: socket.socket) -> None:
    while True:
        c, _ = srv.accept()
        threading.Thread(target=_serve, args=(c,), daemon=True).start()


def _serve(c: socket.socket) -> None:
    global _conn
    f = c.makefile("rb")
    try:
        hello = json.loads(f.readline() or b"{}")
        if not secrets.compare_digest(str(hello.get("hello", "")), _token):
            return
        with _lock:
            _conn = c
        for line in f:
            msg = json.loads(line)
            slot = _pending.get(msg.get("id"))
            if slot:
                slot["msg"] = msg
                slot["ev"].set()
    except (OSError, ValueError):
        pass
    finally:
        with _lock:
            if _conn is c:
                _conn = None
        c.close()


def connected() -> bool:
    return _conn is not None


def call(cmd: str, base: str, timeout: float = 60, **args) -> str:
    """Envoie une demande à l'extension et rend son résultat (texte). Lève BridgeError si Chrome ou l'extension ne répond pas."""
    c = _conn
    if c is None:
        raise BridgeError("L'extension Velora n'est pas active dans Chrome (ou Chrome est fermé).")
    i = next(_ids)
    slot = {"ev": threading.Event()}
    _pending[i] = slot
    try:
        c.sendall((json.dumps({"id": i, "cmd": cmd, "base": base, **args}) + "\n").encode())
        if not slot["ev"].wait(timeout):
            raise BridgeError("Chrome n'a pas répondu à temps.")
    except OSError:
        raise BridgeError("La liaison avec Chrome est coupée.") from None
    finally:
        _pending.pop(i, None)
    msg = slot["msg"]
    if msg.get("error"):
        raise BridgeError("Chrome : " + msg["error"])
    return msg.get("result") or ""


def check_extension() -> None:
    """Interroge l'extension connectée sur sa version (une fois par connexion). À appeler hors du fil de l'interface."""
    c = _conn
    if c is None:
        ext.update(conn=None, version="")
        return
    if ext["conn"] is c and ext["version"]:
        return
    try:
        v = call("version", VENTILEA, timeout=5).strip() or "?"
    except BridgeError as e:
        v = "ancienne" if "commande inconnue" in str(e) else ""  # une extension d'avant la commande « version » refuse : elle est donc à remplacer
    ext.update(conn=c, version=v)


def extension_outdated() -> str:
    """Texte d'avertissement si l'extension connectée n'est pas celle livrée avec cette version de Velora, sinon chaîne vide."""
    v = ext["version"]
    if not v or v == EXTENSION_VERSION:
        return ""
    return f"L'extension Chrome de Velora est {'ancienne' if v == 'ancienne' else 'en version ' + v} (attendue : {EXTENSION_VERSION}) : réinstallez-la depuis le dossier fourni."


# ---------- déclaration du relais à Chrome ----------

def _host_command() -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, "--chrome-host"]
    return [sys.executable, str(Path(__file__).resolve().parent.parent / "main.py"), "--chrome-host"]


def register() -> Path:
    """Écrit le fichier de déclaration lu par Chrome (clé de registre sous Windows) ; l'extension seule est autorisée à lancer le relais."""
    d = config.DATA_DIR / "chrome"
    d.mkdir(exist_ok=True)
    cmd = _host_command()
    if os.name == "nt":
        wrapper = d / "velora_host.bat"
        wrapper.write_text("@echo off\r\n" + subprocess.list2cmdline(cmd) + "\r\n", encoding="utf-8")
    else:
        wrapper = d / "velora_host.sh"
        wrapper.write_text("#!/bin/sh\nexec " + " ".join("'" + a.replace("'", "'\\''") + "'" for a in cmd) + "\n", encoding="utf-8")
        wrapper.chmod(0o755)
    manifest = d / f"{HOST_NAME}.json"
    manifest.write_text(json.dumps({
        "name": HOST_NAME, "description": "Liaison Velora ↔ extension Chrome", "path": str(wrapper), "type": "stdio",
        "allowed_origins": [f"chrome-extension://{EXTENSION_ID}/"]}, indent=2), encoding="utf-8")
    if sys.platform == "darwin" and os.environ.get("VELORA_CHROME_BRIDGE"):  # essai sur Mac seulement
        mac = Path.home() / "Library/Application Support/Google/Chrome/NativeMessagingHosts"
        mac.mkdir(parents=True, exist_ok=True)
        (mac / manifest.name).write_text(manifest.read_text(encoding="utf-8"), encoding="utf-8")
    if os.name == "nt":
        import winreg
        for root in ("Software\\Google\\Chrome\\NativeMessagingHosts", "Software\\Microsoft\\Edge\\NativeMessagingHosts"):
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, f"{root}\\{HOST_NAME}") as k:
                winreg.SetValueEx(k, "", 0, winreg.REG_SZ, str(manifest))
    return manifest


# ---------- le relais lancé par Chrome ----------

def run_host() -> None:
    """Relais : entrées/sorties de Chrome (messages préfixés par leur longueur) ↔ connexion locale vers Velora. Se reconnecte tant que Chrome est là."""
    import time
    try:  # mise à jour en cours : on ne reste pas (Chrome relance le relais toutes les 5 s, et un Velora.exe actif bloque l'installation, code 5)
        if time.time() - config.UPDATE_FLAG.stat().st_mtime < config.UPDATE_FLAG_MAX_AGE:
            return
    except OSError:
        pass
    out = sys.stdout.buffer
    out_lock = threading.Lock()
    sock: list[socket.socket | None] = [None]

    def to_chrome(obj: dict) -> None:
        data = json.dumps(obj).encode()
        with out_lock:
            out.write(struct.pack("<I", len(data)) + data)
            out.flush()

    def pump() -> None:
        while True:
            try:
                info = json.loads(INFO.read_text(encoding="utf-8"))
                s = socket.create_connection(("127.0.0.1", info["port"]), timeout=3)
                s.settimeout(None)
                s.sendall((json.dumps({"hello": info["token"]}) + "\n").encode())
                sock[0] = s
                for line in s.makefile("rb"):
                    to_chrome(json.loads(line))
            except (OSError, ValueError, KeyError):
                pass
            sock[0] = None
            time.sleep(3)  # Velora n'est pas (encore) lancé

    threading.Thread(target=pump, daemon=True).start()
    inp = sys.stdin.buffer
    while True:
        head = inp.read(4)
        if len(head) < 4:
            return  # Chrome a fermé
        msg = inp.read(struct.unpack("<I", head)[0])
        s = sock[0]
        if s:
            try:
                s.sendall(msg + b"\n")
            except OSError:
                pass
