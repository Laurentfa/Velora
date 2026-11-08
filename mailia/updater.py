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
import urllib.error
import urllib.request
from pathlib import Path

from . import db, tlsutil
from .version import APP_VERSION, UPDATE_PUBKEY, UPDATE_REPO, release_message

log = logging.getLogger("updater")
CHECK_EVERY = 24 * 3600
ASSET_SUFFIX = {"darwin": "-mac.zip", "win32": "-setup.exe"}

# Remplace l'appli une fois fermée, puis la relance ; en cas d'échec l'ancienne version est remise en place.
SWAP_SCRIPT = """#!/bin/sh
PID="$1"; NEW="$2"; DST="$3"; OPEN="${4:-open}"; LOG="${5:-/dev/null}"
note() { echo "$(date '+%Y-%m-%d %H:%M:%S') mise à jour : $1" >> "$LOG" 2>/dev/null; }
note "attente de la fermeture de Velora (processus $PID)"
while kill -0 "$PID" 2>/dev/null; do sleep 0.5; done
BAK="$DST.old"
rm -rf "$BAK"
if mv "$DST" "$BAK" && mv "$NEW" "$DST"; then rm -rf "$BAK"; note "application remplacée"; else [ -d "$BAK" ] && [ ! -d "$DST" ] && mv "$BAK" "$DST"; note "remplacement impossible, ancienne version remise"; fi
"$OPEN" "$DST" && note "relance demandée" || note "relance impossible"
"""

# Windows : attend la fermeture de Velora, lance l'installateur (silencieux) jusqu'au bout, puis relance Velora.
# (L'option /RESTARTAPPLICATIONS de l'installateur ne relance que les applications QU'IL a fermées : Velora s'est déjà fermée toute seule, elle n'était donc jamais relancée.)
WIN_SCRIPT = """@echo off
set PID=%~1
set LOG=%~4
echo %date% %time% mise a jour : attente de la fermeture de Velora (processus %PID%)>> "%LOG%"
:wait
tasklist /FI "PID eq %PID%" /NH 2>nul | find "%PID%" >nul
if not errorlevel 1 (
  ping -n 2 127.0.0.1 >nul
  goto wait
)
rem les autres processus Velora (fenetre native, relais lance par Chrome pour l'extension) gardent des fichiers ouverts : l'installateur silencieux abandonnerait (code 5)
taskkill /F /IM "%~nx3" >nul 2>&1
echo %date% %time% mise a jour : processus Velora restants fermes>> "%LOG%"
start /wait "" "%~2" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /CLOSEAPPLICATIONS
echo %date% %time% mise a jour : installateur termine (code %errorlevel%)>> "%LOG%"
start "" "%~3"
echo %date% %time% mise a jour : relance demandee>> "%LOG%"
"""


def available() -> bool:
    return bool(UPDATE_REPO and UPDATE_PUBKEY)


def parse(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", (v or "").split("-")[0])[:3])


def is_newer(candidate: str, current: str | None = None) -> bool:
    return parse(candidate) > parse(current or APP_VERSION)


def _get(url: str, timeout: int = 15, octet: bool = False):
    """`octet` : adresse de l'API GitHub d'un fichier de version (elle renvoie le fichier lui-même, avec cet en-tête)."""
    accept = "application/octet-stream" if octet else "application/vnd.github+json"
    return urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": f"Velora/{APP_VERSION}", "Accept": accept}), timeout=timeout, context=tlsutil.context())


def friendly_error(e: BaseException) -> str:
    """Ce que voit l'utilisateur quand le téléchargement échoue : GitHub répond parfois « 504 » sur ses adresses de téléchargement, ce n'est pas un problème de l'ordinateur."""
    if isinstance(e, urllib.error.HTTPError) and (e.code >= 500 or e.code == 429):
        return f"GitHub ne répond pas pour le moment (erreur {e.code}). Ce n'est pas un problème de votre ordinateur : réessayez dans quelques minutes."
    if isinstance(e, urllib.error.HTTPError):
        return f"GitHub a refusé le téléchargement (erreur {e.code})."
    return f"Connexion à GitHub impossible ({type(e).__name__}) : vérifiez votre connexion, puis réessayez."


# Points d'extension pour un module (voir modules.py) : source de téléchargement supplémentaire, par exemple un miroir privé. Rien de ce qu'un module annonce n'est cru :
# chaque fichier reste vérifié par sa signature (liée à la version et au nom du fichier) avant d'être installé.
#   hooks[id_du_module] = {"latest": fn(suffix) -> info | None,        # quand GitHub ne répond pas du tout : la dernière version, même forme que fetch_latest()
#                          "sources": fn(info, kind) -> [(url, octet)]}  # kind : "file" ou "sig" ; adresses essayées après celles de GitHub
hooks: dict[str, dict] = {}


def _hook_sources(info: dict, kind: str) -> list[tuple[str, bool]]:
    out: list[tuple[str, bool]] = []
    for mid, h in list(hooks.items()):
        try:
            for url, octet in (h.get("sources") or (lambda *_: []))(info, kind):
                if isinstance(url, str) and url.startswith("https://"):  # jamais d'adresse en clair
                    out += [(url, bool(octet))] * 2
        except Exception:  # noqa: BLE001 — un module en panne ne doit pas empêcher la mise à jour
            log.warning("source de téléchargement du module %s en erreur", mid, exc_info=True)
    return out


def _hook_latest(suffix: str) -> dict | None:
    for mid, h in list(hooks.items()):
        try:
            found = (h.get("latest") or (lambda *_: None))(suffix)
        except Exception:  # noqa: BLE001
            log.warning("recherche de version du module %s en erreur", mid, exc_info=True)
            continue
        if isinstance(found, dict) and found.get("version") and is_newer(str(found["version"])) and found.get("name") and found.get("url") and found.get("sig_url"):
            return {"api_url": "", "sig_api_url": "", "notes": "", **found}
    return None


def _sources(info: dict, key: str, api_key: str, kind: str = "file") -> list[tuple[str, bool]]:
    """Où télécharger un fichier : l'adresse directe, puis (si elle est en panne) l'adresse de l'API de GitHub, puis les sources ajoutées par un module. Chacune est tentée deux fois."""
    out = [(info[key], False)] * 2
    if info.get(api_key):
        out += [(info[api_key], True)] * 2
    return out + _hook_sources(info, kind)


def _try_each(sources: list[tuple[str, bool]], work, pause: float = 1.5):
    """Exécute `work(url, octet)` sur chaque source jusqu'à la première qui marche. Lève la dernière erreur (réseau ou HTTP) si aucune ne marche."""
    last: BaseException | None = None
    for n, (url, octet) in enumerate(sources):
        try:
            return work(url, octet)
        except (urllib.error.URLError, OSError, TimeoutError) as e:  # HTTPError en fait partie
            last = e
            log.warning("téléchargement de la mise à jour : %s (%s)", type(e).__name__, getattr(e, "code", "") or str(e)[:80])
            if n < len(sources) - 1:
                time.sleep(pause * (1 + n % 2))
    raise last or RuntimeError("aucune adresse de téléchargement")


def fetch_latest() -> dict | None:
    """Dernière version publiée pour ce système, ou None si l'on est à jour / rien de publié."""
    suffix = ASSET_SUFFIX.get(sys.platform)
    if not (available() and suffix):
        return None
    try:
        with _get(f"https://api.github.com/repos/{UPDATE_REPO}/releases/latest") as r:
            rel = json.load(r)
    except (urllib.error.URLError, OSError, TimeoutError, ValueError) as e:  # GitHub injoignable : un module (miroir) peut dire s'il y a mieux
        log.warning("GitHub ne répond pas (%s)", type(e).__name__)
        found = _hook_latest(suffix)
        if found:
            return found
        raise
    if rel.get("draft") or rel.get("prerelease") or not is_newer(rel.get("tag_name") or ""):
        return None
    assets = {a["name"]: a["browser_download_url"] for a in rel.get("assets") or []}
    api_assets = {a["name"]: a["url"] for a in rel.get("assets") or [] if a.get("url")}  # adresses de l'API : repli quand le téléchargement direct est en panne
    name = next((n for n in assets if n.endswith(suffix)), None)
    if not name or name + ".sig2" not in assets:  # signature liée à la version obligatoire (une version publiée sans elle n'est pas proposée)
        log.warning("version %s ignorée : signature liée à la version absente", rel.get("tag_name"))
        return None
    return {"version": ".".join(map(str, parse(rel["tag_name"]))), "notes": (rel.get("body") or "").strip()[:1500],
            "name": name, "url": assets[name], "sig_url": assets[name + ".sig2"], "api_url": api_assets.get(name, ""), "sig_api_url": api_assets.get(name + ".sig2", "")}


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


def verify_release(version: str, name: str, digest_hex: str, signature_b64: str) -> bool:
    """La signature couvre la version, le nom du fichier et son empreinte (voir version.release_message) : refusée pour toute autre version ou tout autre fichier."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    try:
        Ed25519PublicKey.from_public_bytes(base64.b64decode(UPDATE_PUBKEY)).verify(base64.b64decode(signature_b64.strip()), release_message(version, name, digest_hex))
        return True
    except (InvalidSignature, ValueError):
        return False


def download(info: dict, dest: Path | None = None) -> Path:
    """Télécharge puis vérifie la signature (liée à la version et au nom du fichier) ; lève RuntimeError si elle ne correspond pas (le fichier est alors supprimé)."""
    dest = (dest or Path(tempfile.mkdtemp(prefix="velora-maj-"))) / info["name"]

    def fetch(url: str, octet: bool) -> str:
        h = hashlib.sha256()
        with _get(url, 60, octet) as r, open(dest, "wb") as f:
            while chunk := r.read(1 << 20):
                f.write(chunk)
                h.update(chunk)
        return h.hexdigest()

    def fetch_sig(url: str, octet: bool) -> str:
        with _get(url, 15, octet) as r:
            return r.read().decode()

    try:
        digest = _try_each(_sources(info, "url", "api_url", "file"), fetch)
        sig = _try_each(_sources(info, "sig_url", "sig_api_url", "sig"), fetch_sig)
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        dest.unlink(missing_ok=True)
        raise RuntimeError(friendly_error(e)) from e
    if not (is_newer(info["version"]) and verify_release(info["version"], info["name"], digest, sig)):  # jamais une version qui n'est pas plus récente que celle-ci
        dest.unlink(missing_ok=True)
        raise RuntimeError("La signature de la mise à jour ne correspond pas : elle est refusée.")
    return dest


def app_bundle() -> Path | None:
    """Chemin de Velora.app quand l'appli tourne installée (jamais en développement ni depuis l'image disque)."""
    if sys.platform != "darwin" or not getattr(sys, "frozen", False):
        return None
    p = Path(sys.executable).resolve().parents[2]
    return p if p.suffix == ".app" and os.access(p.parent, os.W_OK) and not str(p).startswith("/Volumes/") else None


def _log_path() -> Path:
    """Journal du remplacement (dossier des journaux de Velora) : de quoi comprendre pourquoi la relance n'a pas eu lieu."""
    from . import config
    d = config.DATA_DIR / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d / "mise-a-jour.log"


def can_install() -> bool:
    return (sys.platform == "darwin" and app_bundle() is not None) or (sys.platform == "win32" and getattr(sys, "frozen", False))


def install(archive: Path, pid: int | None = None, opener: str = "open") -> None:
    """Prépare le remplacement ; l'appelant doit ensuite fermer l'appli (le script attend sa fermeture)."""
    log = _log_path()
    if sys.platform == "win32":
        # installateur Inno Setup : silencieux, s'installe pour l'utilisateur seul (aucun droit administrateur). Un petit script attend la fermeture de Velora,
        # lance l'installateur puis relance Velora (voir WIN_SCRIPT).
        script = archive.parent / "maj.bat"
        script.write_text(WIN_SCRIPT, encoding="cp1252", errors="replace")
        subprocess.Popen(["cmd", "/c", str(script), str(pid or os.getpid()), str(archive), sys.executable, str(log)],
                         creationflags=0x08000000 | 0x00000200, close_fds=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)  # sans fenêtre, hors du groupe de Velora
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
    subprocess.Popen(["/bin/sh", str(script), str(pid or os.getpid()), str(staged), str(target), opener, str(log)], start_new_session=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
