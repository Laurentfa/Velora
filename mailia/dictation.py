"""Dictée vocale. Deux moteurs, choisis automatiquement :
- « apple » (Mac, macOS 26 ou plus) : un petit programme natif (voir tools/dictate) écoute le micro et renvoie le texte au fur et à mesure, avec la
  reconnaissance d'Apple : tout se passe sur l'appareil, rien n'est téléchargé par Velora ;
- « whisper » (Windows, ou Mac plus ancien) : Whisper en local, avec un modèle téléchargé à la demande (voir whisper_engine) ;
- « parakeet » (même cas, au choix) : Parakeet TDT 0.6B v3 en local, plus précis et plus rapide, modèle téléchargé à la demande (voir parakeet_engine).
Dans les deux cas, rien ne quitte l'ordinateur et l'IA n'est pas appelée. `new_session` est remplaçable : les tests y mettent un faux moteur."""
from __future__ import annotations

import json
import platform
import queue
import subprocess
import sys
import threading
from pathlib import Path

HELPER = "velora-dictate"
ERRORS = {
    "mic_denied": "L'accès au micro est refusé. Autorisez Velora dans Réglages Système › Confidentialité et sécurité › Microphone.",
    "asset_missing": "La langue de dictée n'est pas installée sur ce Mac.",
    "unavailable": "La reconnaissance vocale n'est pas disponible sur ce Mac.",
    "mic": "Aucun micro utilisable. Sous Windows : Paramètres › Confidentialité et sécurité › Microphone, et autorisez les applications de bureau.",
    "failed": "La dictée a rencontré un problème.",
}


def helper_path() -> Path | None:
    """Le programme natif : à côté de l'exécutable dans l'appli Velora, sinon dans tools/bin (lancement depuis les sources)."""
    here = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent.parent / "tools" / "bin"
    p = here / HELPER
    return p if p.is_file() else None


def supported() -> bool:
    """Mac avec macOS 26 ou plus et programme natif présent."""
    if sys.platform != "darwin":
        return False
    try:
        major = int(platform.mac_ver()[0].split(".")[0] or 0)
    except ValueError:
        return False
    return major >= 26 and helper_path() is not None


def engine() -> str:
    """« apple » (Mac, macOS 26 ou plus : toujours), « parakeet » ou « whisper » (choix dans les Paramètres, Whisper par défaut), ou « » quand la dictée n'est pas possible ici."""
    if supported():
        return "apple"
    from . import db, parakeet_engine, whisper_engine
    if (db.kv_get("dictation_engine") or "") == "parakeet" and parakeet_engine.libs_ok():
        return "parakeet"
    return "whisper" if whisper_engine.libs_ok() else ("parakeet" if parakeet_engine.libs_ok() else "")


def engine_module(name: str | None = None):
    """Le module qui télécharge et gère les modèles du moteur (whisper_engine ou parakeet_engine) ; None pour Apple."""
    name = name or engine()
    if name == "parakeet":
        from . import parakeet_engine
        return parakeet_engine
    if name == "whisper":
        from . import whisper_engine
        return whisper_engine
    return None


def usable() -> bool:
    return bool(engine())


def check(locale: str = "fr-FR") -> dict:
    """{"available": bool, "installed": bool} : la dictée est-elle utilisable ici, et sa langue ou son modèle est-il en place ?"""
    mod = engine_module()
    if mod is not None:
        return {"available": True, "installed": mod.is_installed()}
    p = helper_path()
    if not supported() or p is None:
        return {"available": False, "installed": False}
    try:
        out = subprocess.run([str(p), "--check", "--locale", locale], capture_output=True, text=True, timeout=20).stdout
        return json.loads(out.strip().splitlines()[-1])
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        return {"available": False, "installed": False}


def install_language(locale: str = "fr-FR") -> str:
    """Fait installer la langue par macOS (téléchargement géré par le système). Retourne "" si tout va bien, sinon le message d'erreur."""
    p = helper_path()
    if p is None:
        return ERRORS["unavailable"]
    try:
        out = subprocess.run([str(p), "--install", "--locale", locale], capture_output=True, text=True, timeout=900).stdout
        last = json.loads(out.strip().splitlines()[-1]) if out.strip() else {}
    except (OSError, subprocess.SubprocessError, ValueError):
        return ERRORS["unavailable"]
    return "" if last.get("t") == "done" else ERRORS.get(last.get("code", ""), last.get("msg") or ERRORS["unavailable"])


class Session:
    """Une dictée en cours. Les événements arrivent dans `events` : {"t": "ready" | "partial" | "final" | "done" | "error", "text"?, "code"?, "msg"?}."""

    def __init__(self, locale: str = "fr-FR") -> None:
        self.locale = locale
        self.events: queue.Queue[dict] = queue.Queue()
        self.proc: subprocess.Popen | None = None
        self.finished = False

    def command(self) -> list[str] | None:
        p = helper_path()
        return [str(p), "--locale", self.locale] if p else None

    def start(self) -> None:
        cmd = self.command()
        if cmd is None:
            self.events.put({"t": "error", "code": "unavailable", "msg": ERRORS["unavailable"]})
            self.finished = True
            return
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self) -> None:
        assert self.proc and self.proc.stdout
        got_end = False
        for line in self.proc.stdout:
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            got_end = got_end or ev.get("t") in ("done", "error")
            self.events.put(ev)
        self.proc.wait()
        if not got_end:  # le programme s'est arrêté sans prévenir
            self.events.put({"t": "error", "code": "crash", "msg": "La dictée s'est arrêtée de façon inattendue."})
        self.finished = True

    def stop(self) -> None:
        """Arrête l'écoute : le texte restant est encore envoyé avant l'événement « done »."""
        if self.proc and self.proc.stdin and self.proc.poll() is None:
            try:
                self.proc.stdin.write("stop\n")
                self.proc.stdin.flush()
                self.proc.stdin.close()
            except OSError:
                pass

    def kill(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.kill()
        self.finished = True


def new_session():
    name = engine()
    if name == "parakeet":
        from . import parakeet_engine
        return parakeet_engine.ParakeetSession()
    if name == "whisper":
        from . import whisper_engine
        return whisper_engine.WhisperSession()
    return Session()


def message(code: str, msg: str = "") -> str:
    if code in ("failed", "crash", "mic") and msg:  # le détail (type d'erreur, endroit du code) aide à comprendre un problème
        return f"{ERRORS.get(code, '')} {msg}".strip() if code != "mic" else msg
    return ERRORS.get(code) or msg or "La dictée a rencontré un problème."


def preload() -> bool:
    """Prépare le moteur en tâche de fond : charge le modèle de Whisper ou de Parakeet pour que le premier clic sur le micro ne fasse pas attendre. Apple n'en a pas besoin."""
    from . import db
    if (db.kv_get("dictation_preload") or "1") != "1":  # réglable dans les Paramètres : le modèle occupe 1 Go de mémoire tant qu'il est chargé
        return False
    mod = engine_module()
    if mod is None or not hasattr(mod, "preload"):
        return False
    try:
        return bool(mod.preload())
    except Exception:  # noqa: BLE001 — le préchargement est un confort : une erreur se verra au vrai clic, avec son message
        import logging
        logging.getLogger("dictation").warning("préchargement impossible", exc_info=True)
        return False
