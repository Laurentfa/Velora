"""Corriger et reformuler un texte avec Apple Intelligence (Mac, macOS 26 ou plus) : le modèle d'Apple tourne sur l'appareil, gratuitement.

Indépendant des réglages « IA » de Velora (clé Claude, interrupteur, boîtes sans IA) : aucun texte ne quitte le Mac et rien n'est facturé. Un petit programme natif
(tools/write) dialogue avec le modèle. Le résultat est toujours proposé avant d'être appliqué : le modèle d'Apple est petit et peut, rarement, changer le sens.
Mesuré : bon pour corriger et reformuler ; pas fiable pour trier le courrier (voir README)."""
from __future__ import annotations

import json
import platform
import subprocess
import sys
import time
from pathlib import Path

HELPER = "velora-write"
MAX_CHUNK = 2500  # caractères : la fenêtre du modèle est courte (environ 4 000 jetons)
MODES = {  # clé -> (titre du menu, détail)
    "correct": ("Corriger", "orthographe, grammaire, ponctuation, sans changer le style"),
    "rephrase": ("Reformuler", "plus clair et mieux écrit, même sens"),
    "formal": ("Plus professionnel", "registre poli et soigné"),
    "friendly": ("Plus chaleureux", "ton cordial"),
    "shorter": ("Plus court", "l'essentiel seulement"),
    "expand": ("Développer légèrement", "formules de liaison, sans rien ajouter d'inventé"),
}
ERRORS = {
    "unavailable": "Apple Intelligence n'est pas disponible : activez-la dans Réglages Système › Apple Intelligence et Siri, et attendez la fin du téléchargement du modèle.",
    "too_long": "Le texte est trop long pour Apple Intelligence : sélectionnez un passage plus court.",
    "refused": "Apple Intelligence a refusé de traiter ce texte (ses règles de sécurité).",
    "language": "Apple Intelligence ne prend pas en charge la langue de ce texte.",
    "failed": "Apple Intelligence n'a pas pu traiter ce texte.",
}
_cache = {"at": 0.0, "ok": False}


def helper_path() -> Path | None:
    here = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent.parent / "tools" / "bin"
    p = here / HELPER
    return p if p.is_file() else None


def supported() -> bool:
    """Mac avec macOS 26 ou plus et programme natif présent (sans savoir encore si Apple Intelligence est activée)."""
    if sys.platform != "darwin":
        return False
    try:
        major = int(platform.mac_ver()[0].split(".")[0] or 0)
    except ValueError:
        return False
    return major >= 26 and helper_path() is not None


def enabled() -> bool:
    """Les boutons Apple Intelligence sont proposés (réglage des Paramètres, activé par défaut) : indépendant de l'interrupteur « IA » de Velora."""
    from . import db
    return (db.kv_get("apple_writer") or "1") == "1"


def available(max_age: float = 60.0) -> bool:
    """Apple Intelligence est-elle activée et prête sur ce Mac ? (le résultat est gardé une minute)"""
    if not supported():
        return False
    if time.monotonic() - _cache["at"] < max_age:
        return _cache["ok"]
    try:
        out = subprocess.run([str(helper_path()), "--check"], capture_output=True, text=True, timeout=20).stdout
        ok = bool(json.loads(out.strip().splitlines()[-1]).get("available"))
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        ok = False
    _cache.update(at=time.monotonic(), ok=ok)
    return ok


class WriterError(Exception):
    pass


def _ask(mode: str, text: str) -> str:
    p = helper_path()
    if p is None:
        raise WriterError(ERRORS["unavailable"])
    try:
        out = subprocess.run([str(p)], input=json.dumps({"mode": mode, "text": text}), capture_output=True, text=True, timeout=90).stdout
        d = json.loads(out.strip().splitlines()[-1])
    except subprocess.TimeoutExpired as e:
        raise WriterError("Apple Intelligence met trop de temps à répondre.") from e
    except (OSError, ValueError, IndexError) as e:
        raise WriterError(ERRORS["failed"]) from e
    if "text" in d:
        return d["text"]
    raise WriterError(ERRORS.get(d.get("error", ""), ERRORS["failed"]))


def chunks(text: str, size: int = MAX_CHUNK) -> list[str]:
    """Coupe un long texte aux paragraphes (puis aux phrases) en morceaux d'au plus `size` caractères."""
    pieces: list[str] = []
    for para in text.split("\n"):
        pieces += [para] if len(para) <= size else [x + "." if not x.endswith((".", "!", "?")) else x for x in para.replace(". ", ".\n").split("\n")]
    parts, cur = [], ""
    for piece in pieces:
        if cur and len(cur) + len(piece) + 1 > size:
            parts.append(cur)
            cur = ""
        cur = f"{cur}\n{piece}" if cur else piece
    if cur.strip():
        parts.append(cur)
    return parts or [text]


def rewrite(mode: str, text: str) -> str:
    """Le texte corrigé ou reformulé. Les paragraphes sont conservés (un morceau à la fois si le texte est long)."""
    if mode not in MODES:
        raise WriterError(ERRORS["failed"])
    text = text.strip()
    if not text:
        return ""
    if len(text) <= MAX_CHUNK:
        return _ask(mode, text)
    return "\n".join(_ask(mode, c) for c in chunks(text))
