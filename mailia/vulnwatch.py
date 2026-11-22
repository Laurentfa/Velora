"""Surveillance des avis de sécurité des composants de Velora.

Velora embarque des bibliothèques (Python, et des fichiers JavaScript dans certains modules). Une faille connue dans l'une d'elles ne se corrige que par une nouvelle version de Velora :
encore faut-il le savoir. Une fois par semaine, Velora demande à OSV.dev (base publique et gratuite des avis de sécurité, sans clé) si les composants qu'elle contient, À LEURS VERSIONS
EXACTES, ont des avis. Ce qui part sur Internet : des noms de bibliothèques et leurs numéros de version, rien d'autre (jamais un mail, un nom, un réglage). Désactivable.

Sources de la liste des composants : « requirements.lock » (embarqué dans l'appli, sinon les versions installées) ; le fichier « components.json » éventuel de chaque module
({"components": [{"ecosystem": "npm", "name": "pdfjs-dist", "version": "4.10.38"}]}) pour ce qu'il embarque lui-même."""
import json
import logging
import re
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from . import db, modules, tlsutil

log = logging.getLogger("vulnwatch")
OSV = "https://api.osv.dev/v1"
INTERVAL = 7 * 24 * 3600  # une vérification par semaine
MAX_DETAILS = 150  # plafond d'avis détaillés par vérification
SEVERITY_ORDER = {"CRITICAL": 4, "HIGH": 3, "MODERATE": 2, "MEDIUM": 2, "LOW": 1}
SEVERITY_FR = {"CRITICAL": "critique", "HIGH": "élevée", "MODERATE": "moyenne", "MEDIUM": "moyenne", "LOW": "faible"}
KV_LAST, KV_ON, KV_OFF = "vuln:last", "vuln_on", "vuln:dismissed"


def enabled() -> bool:
    return db.kv_get(KV_ON) != "0"


# ---------- liste des composants ----------
def lock_path() -> Path:
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent)) / "requirements.lock"


def python_components() -> list[dict]:
    out: dict = {}
    try:
        for line in lock_path().read_text(encoding="utf-8").splitlines():
            m = re.match(r"^\s*([A-Za-z0-9_.\-]+)==([A-Za-z0-9_.!+\-]+)\s*(?:;.*)?(?:#.*)?$", line)
            if m:
                out[re.sub(r"[-_.]+", "-", m.group(1)).lower()] = m.group(2)
    except OSError:
        pass
    if not out:  # pas de fichier de verrou : les versions installées
        try:
            from importlib import metadata
            for d in metadata.distributions():
                n, v = (d.metadata["Name"] or ""), (d.version or "")
                if n and v:
                    out[re.sub(r"[-_.]+", "-", n).lower()] = v
        except Exception:  # noqa: BLE001
            log.debug("versions installées illisibles", exc_info=True)
    return [{"ecosystem": "PyPI", "name": n, "version": v} for n, v in sorted(out.items())]


def module_components() -> list[dict]:
    out = []
    try:
        for d in sorted(modules.DIR.iterdir()):
            f = d / "components.json"
            if not f.is_file():
                continue
            for c in json.loads(f.read_text(encoding="utf-8")).get("components", []):
                if isinstance(c, dict) and all(isinstance(c.get(k), str) and c[k] for k in ("ecosystem", "name", "version")) and c["ecosystem"] in ("npm", "PyPI"):
                    out.append({"ecosystem": c["ecosystem"], "name": c["name"], "version": c["version"], "from": d.name})
    except (OSError, ValueError, AttributeError):
        log.debug("composants des modules illisibles", exc_info=True)
    return out


def components() -> list[dict]:
    seen, out = set(), []
    for c in python_components() + module_components():
        k = (c["ecosystem"], c["name"], c["version"])
        if k not in seen:
            seen.add(k)
            out.append(c)
    return out


# ---------- OSV.dev ----------
def _http(method: str, url: str, body: dict | None = None, timeout: float = 25) -> dict:
    """Échange JSON avec OSV.dev : certificats vérifiés avec le magasin embarqué (comme tous les accès de Velora), aucune redirection."""
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None, method=method,
                                 headers={"Content-Type": "application/json", "User-Agent": "Velora-vulnwatch"})
    with tlsutil.opener(follow=False).open(req, timeout=timeout) as r:
        return json.loads(r.read(5_000_000).decode("utf-8"))


def _severity(v: dict) -> str:
    s = str((v.get("database_specific") or {}).get("severity") or "").upper()
    return s if s in SEVERITY_ORDER else ""


def _fixed(v: dict, name: str) -> str:
    fixes = []
    for a in v.get("affected") or []:
        if re.sub(r"[-_.]+", "-", str((a.get("package") or {}).get("name") or "")).lower() != re.sub(r"[-_.]+", "-", name).lower():
            continue
        for r in a.get("ranges") or []:
            fixes += [str(e["fixed"]) for e in r.get("events") or [] if e.get("fixed")]

    def key(x: str):
        return [int(p) if p.isdigit() else 0 for p in re.split(r"[.\-+]", x)]
    return max(fixes, key=key) if fixes else ""


def scan(comps: list[dict], http=_http) -> list[dict]:
    """Les avis qui touchent ces composants à ces versions : [{"id", "aliases", "name", "version", "ecosystem", "severity", "summary", "fixed", "from"}], les plus graves d'abord."""
    issues, ids_of = [], {}
    for i in range(0, len(comps), 500):
        part = comps[i:i + 500]
        res = http("POST", f"{OSV}/querybatch", {"queries": [{"package": {"name": c["name"], "ecosystem": c["ecosystem"]}, "version": c["version"]} for c in part]}).get("results", [])
        for c, r in zip(part, res):
            for v in (r or {}).get("vulns") or []:
                ids_of.setdefault(v["id"], []).append(c)
    for n, (vid, cs) in enumerate(ids_of.items()):
        if n >= MAX_DETAILS:
            break
        try:
            v = http("GET", f"{OSV}/vulns/{vid}")
        except (OSError, ValueError):
            v = {"id": vid}
        for c in cs:
            issues.append({"id": vid, "aliases": [a for a in v.get("aliases", []) if isinstance(a, str)][:4], "name": c["name"], "version": c["version"], "ecosystem": c["ecosystem"],
                           "severity": _severity(v), "summary": str(v.get("summary") or "")[:200], "fixed": _fixed(v, c["name"]), "from": c.get("from", "")})
    issues.sort(key=lambda x: (-SEVERITY_ORDER.get(x["severity"], 0), x["name"], x["id"]))
    return issues


def check(force: bool = False, http=_http) -> dict | None:
    """Vérifie (au plus une fois par semaine, sauf force) et garde le résultat. None : désactivé ou trop tôt. Ne lève jamais d'erreur : l'échec est gardé dans le résultat."""
    if not enabled() and not force:
        return None
    last = last_result()
    if not force and last.get("at_ts") and time.time() - last["at_ts"] < INTERVAL and not last.get("error"):
        return None
    comps = components()
    res = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "at_ts": time.time(), "n": len(comps), "issues": [], "error": ""}
    try:
        res["issues"] = scan(comps, http)
    except Exception as e:  # noqa: BLE001 — pas de réseau, service indisponible : on réessaie au prochain passage
        res["error"] = type(e).__name__
        log.info("vérification des avis de sécurité impossible : %s", type(e).__name__)
    db.kv_set(KV_LAST, json.dumps(res, ensure_ascii=False))
    if res["issues"]:
        log.warning("%d avis de sécurité sur les composants (%s)", len(res["issues"]), ", ".join(sorted({i["name"] for i in res["issues"]}))[:200])
    return res


def last_result() -> dict:
    try:
        d = json.loads(db.kv_get(KV_LAST) or "{}")
        return d if isinstance(d, dict) else {}
    except ValueError:
        return {}


def dismissed() -> set[str]:
    try:
        return set(json.loads(db.kv_get(KV_OFF) or "[]"))
    except ValueError:
        return set()


def dismiss(advisory: str, on: bool = True) -> None:
    s = dismissed()
    (s.add if on else s.discard)(advisory)
    db.kv_set(KV_OFF, json.dumps(sorted(s)))


def open_issues() -> list[dict]:
    """Les avis qui restent à traiter (ceux que vous n'avez pas écartés)."""
    off = dismissed()
    return [i for i in last_result().get("issues", []) if i["id"] not in off]
