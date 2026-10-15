"""Mise à jour automatique depuis les versions publiées sur GitHub (gratuit, sans signature Apple ni Microsoft).

Comme l'appli n'est pas signée par un éditeur reconnu, chaque mise à jour est signée par VOTRE clé (Ed25519) : la clé publique est dans
version.py, la clé privée reste chez vous (secret GitHub). Une archive modifiée en route, ou publiée par quelqu'un d'autre, est refusée.
Le téléchargement est fait par l'appli elle-même : le système ne le marque donc pas « venu d'Internet » et n'affiche pas d'avertissement."""
import base64
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from . import db
from .version import APP_VERSION, UPDATE_PUBKEY, UPDATE_REPO

log = logging.getLogger("updater")
CHECK_EVERY = 24 * 3600
ASSET_SUFFIX = {"darwin": "-mac.zip", "win32": "-setup.exe"}

# Remplace l'appli une fois fermée, puis la relance ; en cas d'échec l'ancienne version est remise en place.
SWAP_SCRIPT = """#!/bin/sh
PID="$1"; NEW="$2"; DST="$3"; OPEN="${4:-open}"
while kill -0 "$PID" 2>/dev/null; do sleep 0.5; done
BAK="$DST.old"
rm -rf "$BAK"
if mv "$DST" "$BAK" && mv "$NEW" "$DST"; then rm -rf "$BAK"; else [ -d "$BAK" ] && [ ! -d "$DST" ] && mv "$BAK" "$DST"; fi
"$OPEN" "$DST"
"""


def available() -> bool:
    return bool(UPDATE_REPO and UPDATE_PUBKEY)


def parse(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", (v or "").split("-")[0])[:3])


def is_newer(candidate: str, current: str = APP_VERSION) -> bool:
    return parse(candidate) > parse(current)


def _get(url: str, timeout: int = 15):
    return urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": f"Velora/{APP_VERSION}", "Accept": "application/vnd.github+json"}), timeout=timeout)


def fetch_latest() -> dict | None:
    """Dernière version publiée pour ce système, ou None si l'on est à jour / rien de publié."""
    suffix = ASSET_SUFFIX.get(sys.platform)
    if not (available() and suffix):
        return None
    with _get(f"https://api.github.com/repos/{UPDATE_REPO}/releases/latest") as r:
        rel = json.load(r)
    if rel.get("draft") or rel.get("prerelease") or not is_newer(rel.get("tag_name") or ""):
        return None
    assets = {a["name"]: a["browser_download_url"] for a in rel.get("assets") or []}
    name = next((n for n in assets if n.endswith(suffix)), None)
    if not name or name + ".sig" not in assets:
        return None
    return {"version": ".".join(map(str, parse(rel["tag_name"]))), "notes": (rel.get("body") or "").strip()[:1500],
            "name": name, "url": assets[name], "sig_url": assets[name + ".sig"]}


def check(force: bool = False) -> dict | None:
    """Vérifie au plus une fois par jour (sauf demande explicite). Ne lève jamais d'erreur : sans réseau, on réessaiera plus tard."""
    if not available() or (not force and (db.kv_get("update_auto") == "0" or time.time() - float(db.kv_get("update_checked") or 0) < CHECK_EVERY)):
        return None
    try:
        info = fetch_latest()
    except Exception as e:
        log.warning("recherche de mise à jour impossible : %s", e)
        return None
    db.kv_set("update_checked", str(time.time()))
    return info


def verify(digest_hex: str, signature_b64: str) -> bool:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    try:
        Ed25519PublicKey.from_public_bytes(base64.b64decode(UPDATE_PUBKEY)).verify(base64.b64decode(signature_b64.strip()), digest_hex.encode())
        return True
    except (InvalidSignature, ValueError):
        return False


def download(info: dict, dest: Path | None = None) -> Path:
    """Télécharge puis vérifie la signature ; lève RuntimeError si elle ne correspond pas (le fichier est alors supprimé)."""
    dest = (dest or Path(tempfile.mkdtemp(prefix="velora-maj-"))) / info["name"]
    h = hashlib.sha256()
    with _get(info["url"], timeout=60) as r, open(dest, "wb") as f:
        while chunk := r.read(1 << 20):
            f.write(chunk)
            h.update(chunk)
    with _get(info["sig_url"]) as r:
        sig = r.read().decode()
    if not verify(h.hexdigest(), sig):
        dest.unlink(missing_ok=True)
        raise RuntimeError("La signature de la mise à jour ne correspond pas : elle est refusée.")
    return dest


def app_bundle() -> Path | None:
    """Chemin de Velora.app quand l'appli tourne installée (jamais en développement ni depuis l'image disque)."""
    if sys.platform != "darwin" or not getattr(sys, "frozen", False):
        return None
    p = Path(sys.executable).resolve().parents[2]
    return p if p.suffix == ".app" and os.access(p.parent, os.W_OK) and not str(p).startswith("/Volumes/") else None


def can_install() -> bool:
    return (sys.platform == "darwin" and app_bundle() is not None) or (sys.platform == "win32" and getattr(sys, "frozen", False))


def install(archive: Path, pid: int | None = None, opener: str = "open") -> None:
    """Prépare le remplacement ; l'appelant doit ensuite fermer l'appli (le script attend sa fermeture)."""
    if sys.platform == "win32":
        # installateur Inno Setup : silencieux, ferme et relance Velora, s'installe pour l'utilisateur seul (aucun droit administrateur)
        subprocess.Popen([str(archive), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS", "/RESTARTAPPLICATIONS"],
                         creationflags=0x00000008 | 0x00000200, close_fds=True)
        return
    target = app_bundle()
    if target is None:
        raise RuntimeError("Velora n'est pas installée dans un dossier modifiable : copiez-la dans Applications, puis relancez.")
    out = archive.parent / "nouvelle"
    shutil.rmtree(out, ignore_errors=True)
    subprocess.run(["ditto", "-x", "-k", str(archive), str(out)], check=True)
    new = next(out.glob("*.app"), None)
    if new is None:
        raise RuntimeError("L'archive de mise à jour ne contient pas d'application.")
    staged = target.parent / (target.name + ".new")  # même volume que la cible : le remplacement est instantané
    shutil.rmtree(staged, ignore_errors=True)
    shutil.move(str(new), staged)
    script = archive.parent / "echange.sh"
    script.write_text(SWAP_SCRIPT)
    subprocess.Popen(["/bin/sh", str(script), str(pid or os.getpid()), str(staged), str(target), opener], start_new_session=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
