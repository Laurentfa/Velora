"""Clés d'accès à l'API et au serveur MCP : création (la clé n'est montrée qu'une fois, seule son empreinte est gardée), vérification, droits,
limites (débit, envois et appels IA par jour, destinataires autorisés) et journal de chaque appel."""
import logging
import hashlib
import json
import secrets
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from . import db

SCOPES = {
    "read": "Lire les mails (liste, recherche, contenu, pièces jointes)",
    "draft": "Créer et modifier des brouillons, voir la file d'envoi",
    "send": "Envoyer ou programmer des messages, annuler un envoi",
    "ai": "Utiliser la rédaction et les résumés par l'IA (consomme votre budget IA)",
    "manage": "Agir sur les mails (lu/non lu, archiver, déplacer, épingler)",
}
PRESETS = {
    "Lecture seule": ["read"],
    "Rédaction (brouillons à valider)": ["read", "draft", "ai"],
    "Envoi": ["read", "draft", "send", "ai"],
    "Complet": ["read", "draft", "send", "ai", "manage"],
}
CHANNELS = {"rest": "API REST", "mcp": "Assistants IA (MCP)"}
RATE_PER_MINUTE = 60


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


@dataclass
class Key:
    id: str
    name: str
    scopes: set[str]
    channels: set[str]
    accounts: list[str] = field(default_factory=list)
    folders: list[str] = field(default_factory=list)
    send_mode: str = "approval"
    daily_send_cap: int = 20
    ai_daily_cap: int = 30
    allowed_domains: list[str] = field(default_factory=list)
    channel: str = "rest"  # canal par lequel cet appel arrive

    def can(self, scope: str) -> bool:
        return scope in self.scopes


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def _loads(v, default):
    try:
        return json.loads(v) if v else default
    except ValueError:
        return default


def create(name: str, scopes: list[str], channels: list[str], accounts: list[str] | None = None, folders: list[str] | None = None,
           send_mode: str = "approval", daily_send_cap: int = 20, ai_daily_cap: int = 30, allowed_domains: list[str] | None = None,
           expires_days: int = 0) -> tuple[str, str]:
    """Crée une clé. Retourne (identifiant, clé en clair) : la clé en clair n'existera plus nulle part ensuite."""
    raw = "mia_" + secrets.token_urlsafe(32)
    kid = secrets.token_hex(6)
    exp = (datetime.now().astimezone() + timedelta(days=expires_days)).isoformat() if expires_days else ""
    db.api_key_save({
        "id": kid, "name": name.strip() or "Clé sans nom", "prefix": raw[:8], "hash": _hash(raw),
        "scopes": json.dumps([s for s in scopes if s in SCOPES]), "channels": json.dumps([c for c in channels if c in CHANNELS]),
        "accounts": json.dumps(accounts or []), "folders": json.dumps(folders or []),
        "send_mode": "direct" if send_mode == "direct" else "approval", "daily_send_cap": int(daily_send_cap), "ai_daily_cap": int(ai_daily_cap),
        "allowed_domains": json.dumps([d.strip().lower().lstrip("@") for d in (allowed_domains or []) if d.strip()]),
        "expires": exp, "created": datetime.now().astimezone().isoformat()})
    return kid, raw


def authenticate(raw: str, channel: str) -> Key:
    """Vérifie une clé présentée par un programme. Lève ApiError(401/403) sinon."""
    raw = (raw or "").strip()
    if not raw.startswith("mia_"):
        raise ApiError(401, "missing_key", "Clé d'accès absente ou invalide (en-tête Authorization: Bearer mia_…).")
    row = db.api_key_by_hash(_hash(raw))
    if not row or row["revoked"]:
        raise ApiError(401, "invalid_key", "Clé d'accès invalide ou révoquée.")
    if row["expires"] and datetime.fromisoformat(row["expires"]) < datetime.now().astimezone():
        raise ApiError(401, "expired_key", "Cette clé a expiré.")
    channels = set(_loads(row["channels"], []))
    if channel not in channels:
        raise ApiError(403, "channel_not_allowed", f"Cette clé n'est pas autorisée pour le canal « {CHANNELS.get(channel, channel)} ».")
    k = Key(id=row["id"], name=row["name"], scopes=set(_loads(row["scopes"], [])), channels=channels, accounts=_loads(row["accounts"], []),
            folders=_loads(row["folders"], []), send_mode=row["send_mode"], daily_send_cap=int(row["daily_send_cap"] or 0),
            ai_daily_cap=int(row["ai_daily_cap"] or 0), allowed_domains=_loads(row["allowed_domains"], []), channel=channel)
    _rate_limit(k.id)
    db.api_key_touch(k.id)
    return k


_buckets: dict[str, deque] = defaultdict(deque)
_lock = threading.Lock()
_auth_fails: dict[str, deque] = defaultdict(deque)
MAX_AUTH_FAILS = 10  # par adresse et par minute


def check_not_blocked(ip: str) -> None:
    """Trop de clés invalides présentées depuis une même adresse : on bloque un moment (anti-devinette)."""
    now = time.time()
    with _lock:
        q = _auth_fails[ip]
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= MAX_AUTH_FAILS:
            raise ApiError(429, "too_many_failures", "Trop de clés invalides depuis cette adresse. Réessayez dans une minute.")


def note_failure(ip: str, channel: str, detail: str) -> None:
    with _lock:
        _auth_fails[ip].append(time.time())
    try:
        db.api_log_add("", "?", channel, "auth_failed", ip, "denied", detail)
    except Exception:
        logging.getLogger("apikeys").warning("échec d'authentification non journalisé", exc_info=True)


def _rate_limit(kid: str) -> None:
    now = time.time()
    with _lock:
        q = _buckets[kid]
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= RATE_PER_MINUTE:
            raise ApiError(429, "rate_limited", f"Trop de requêtes (maximum {RATE_PER_MINUTE} par minute). Réessayez dans quelques secondes.")
        q.append(now)


def require(k: Key, scope: str) -> None:
    if not k.can(scope):
        raise ApiError(403, "scope_missing", f"Cette clé n'a pas le droit « {scope} » ({SCOPES.get(scope, '')}).")


def day_start_iso() -> str:
    """Minuit local, exprimé en UTC : le journal est en UTC, la comparaison de textes reste exacte même au changement d'heure."""
    from datetime import timezone
    return datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc).isoformat()


def check_daily(k: Key, action_prefix: str, cap: int, what: str) -> None:
    exact = action_prefix == "send"
    used = db.api_log_count(k.id, action_prefix, day_start_iso(), exact)
    if exact:  # un envoi annulé à temps ne compte pas
        used -= db.api_log_count(k.id, "send_cancel", day_start_iso(), True)
    if cap and used >= cap:
        raise ApiError(429, "daily_cap", f"Plafond quotidien atteint pour cette clé ({cap} {what} par jour).")


def domain_allowed(k: Key, addrs: list[str]) -> bool:
    if not k.allowed_domains:
        return True
    for a in addrs:
        a = a.strip().lower()
        dom = a.rsplit("@", 1)[-1]
        if not any(p == a or p == dom or dom.endswith("." + p) for p in k.allowed_domains):
            return False
    return True


def log(k: Key | None, action: str, target: str = "", status: str = "ok", detail: str = "") -> None:
    try:
        db.api_log_add(k.id if k else "", k.name if k else "", k.channel if k else "", action, target, status, detail)
    except Exception:
        pass  # le journal ne doit jamais bloquer une réponse
