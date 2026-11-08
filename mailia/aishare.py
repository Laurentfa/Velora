"""Partage des analyses IA entre vos ordinateurs, par la boîte Exchange elle-même.

Quand Velora analyse un mail (résumé, catégorie, priorité, « à répondre », action suggérée), elle publie le résultat sur le serveur, attaché à ce mail, dans une
propriété cachée (Outlook ne la montre pas). Un autre ordinateur qui lit la même boîte la reprend au lieu de relancer l'IA : une seule analyse payée par mail.

Ce qui est partagé : le résultat brut de l'IA, JAMAIS vos corrections de catégorie ni vos expéditeurs prioritaires (chaque ordinateur applique les siens).
Une analyse n'est reprise que si elle a été faite avec les mêmes consignes, pour la même personne et dans la même langue (empreinte). Sinon le mail est réanalysé
localement, comme avant. Exchange seulement pour l'instant ; actif par défaut, boîte par boîte (Paramètres › Intelligence artificielle).
Si le serveur refuse l'écriture (boîte partagée en lecture seule…), Velora continue en local et n'insiste pas."""
import hashlib
import json
import logging
from datetime import datetime, timezone

from . import aipolicy, config, db

log = logging.getLogger("aishare")
VERSION = 1
_write_refused: set[str] = set()  # comptes dont le serveur a refusé l'écriture : on n'insiste pas jusqu'au prochain lancement


# ---------- réglages ----------
def supported(account: str) -> bool:
    cfg = config.load_accounts(enabled_only=False).get(account)
    return bool(cfg and cfg.kind == "ews" and cfg.ai_enabled)


def enabled(account: str) -> bool:
    """Actif par défaut : la fonction ne fait économiser de l'IA qu'à ceux qui l'ont, et ne coûte rien aux autres."""
    return db.kv_get(f"ai_share:{account}") != "0" and supported(account)


def set_enabled(account: str, on: bool) -> None:
    db.kv_set(f"ai_share:{account}", "1" if on else "0")
    if on:
        _write_refused.discard(account)


# ---------- empreinte et format ----------
def fingerprint() -> str:
    """Change dès que les consignes de tri, vos règles, votre nom ou la langue changent : une analyse faite autrement n'est pas reprise.
    (Les exemples tirés de vos corrections ne comptent pas : ils sont propres à chaque ordinateur.)"""
    from . import ai
    parts = [ai.prompt_text("analyze"), ai.prompt_text("rules"), config.persona("‹‹NOM››|‹‹PRENOM››"), aipolicy.language_note("analyze"), "|".join(ai.CATEGORIES)]
    return hashlib.sha1("\n".join(parts).encode("utf-8")).hexdigest()[:12]


def encode(d: dict) -> str:
    return json.dumps({"v": VERSION, "f": fingerprint(), "t": datetime.now(timezone.utc).isoformat(timespec="seconds"), "s": d["summary"], "c": d["category"],
                       "p": d["priority"], "n": d["needs_reply"], "g": d["suggested"]}, ensure_ascii=False, separators=(",", ":"))


def decode(raw: str | None) -> dict | None:
    """L'analyse publiée si elle est lisible et faite avec les mêmes consignes que les vôtres, sinon None."""
    if not raw:
        return None
    try:
        p = json.loads(raw)
        if not isinstance(p, dict) or p.get("v") != VERSION or p.get("f") != fingerprint():
            return None
        from . import ai
        cat = p["c"] if p["c"] in ai.CATEGORIES else "info"
        return {"summary": str(p["s"])[:600], "category": cat, "priority": min(3, max(1, int(p["p"]))), "needs_reply": 1 if p["n"] else 0, "suggested": str(p["g"])[:120]}
    except (ValueError, KeyError, TypeError):
        return None


# ---------- reprise ----------
def _count(kind: str) -> None:
    try:
        st = json.loads(db.kv_get("ai_share_stats") or "{}")
        st[kind] = int(st.get(kind, 0)) + 1
        db.kv_set("ai_share_stats", json.dumps(st))
    except (ValueError, TypeError):
        pass


def stats() -> dict:
    try:
        return json.loads(db.kv_get("ai_share_stats") or "{}")
    except ValueError:
        return {}


def take(row: dict, raw: str | None) -> bool:
    """Range sur le message l'analyse publiée `raw`, si elle convient. Rend True si le message est maintenant analysé (aucun appel à l'IA)."""
    if not raw or row.get("ai_at") or not enabled(row["account"]) or aipolicy.is_excluded(row):
        return False
    d = decode(raw)
    if not d:
        return False
    from . import ai
    ai.store_analysis(row, d)
    _count("reused")
    return True


def lookup(row: dict) -> bool:
    """Avant d'appeler l'IA pour un mail : demande au serveur si un autre ordinateur l'a déjà analysé. Toute erreur = on analyse nous-mêmes."""
    if not enabled(row["account"]) or row.get("ai_at"):
        return False
    try:
        from . import sync
        raw = sync.provider(row["account"]).read_analysis(row["uid"])
    except Exception as e:  # noqa: BLE001 — pas de réseau, pas de droit… : sans conséquence, l'analyse se fera ici
        log.info("analyse partagée non lue (%s) : %s", row["account"], type(e).__name__)
        return False
    return take(row, raw)


# ---------- publication ----------
def publish(row: dict, raw_result: dict) -> bool:
    """Après une analyse faite ici : la publie sur le serveur pour les autres ordinateurs. Ne lève jamais d'erreur."""
    account = row["account"]
    if not enabled(account) or account in _write_refused:
        return False
    try:
        from . import sync
        sync.provider(account).write_analysis(row["uid"], encode(raw_result))
    except Exception as e:  # noqa: BLE001
        _write_refused.add(account)
        log.warning("partage des analyses : le serveur refuse l'écriture pour %s (%s) ; Velora continue en local", account, type(e).__name__)
        return False
    _count("published")
    return True
