"""Compteurs d'envois (Exchange et IMAP) : rattachés à la conversation, envoyés sans rattachement, incertains, sans copie dans « Envoyés ».
Les cas qui demandent un coup d'œil de l'utilisateur déposent aussi une alerte, affichée en haut de la liste tant qu'elle n'est pas lue."""
import json
import logging
from datetime import datetime, timezone

from . import db

log = logging.getLogger("sendstats")
ALERTING = ("fallback", "uncertain", "nocopy")


def record(kind: str, subject: str = "") -> None:
    try:
        s = json.loads(db.kv_get("send_stats") or "{}")
        s[kind] = int(s.get(kind, 0)) + 1
        db.kv_set("send_stats", json.dumps(s))
        if kind in ALERTING:
            db.kv_set("send_alert", json.dumps({"kind": kind, "subject": subject[:120], "at": datetime.now(timezone.utc).isoformat()}))
    except Exception:
        log.warning("alerte d'envoi non enregistrée", exc_info=True)
