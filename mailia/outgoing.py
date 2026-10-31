"""Mise en file d'envoi : le même code pour l'interface, l'API et le serveur MCP (un message envoyé de l'une ou l'autre façon suit exactement
les mêmes règles : délai d'annulation, file, relance, retour en brouillon)."""
import uuid
from datetime import datetime, timedelta, timezone

from . import db
from .textutil import html_to_text


def undo_delay() -> int:
    """Secondes pendant lesquelles un envoi peut encore être annulé (réglage de l'utilisateur, 0 à 60 ; 10 par défaut)."""
    try:
        return max(0, min(60, int(db.kv_get("undo_delay") or 10)))
    except ValueError:
        return 10


def earliest() -> datetime:
    return datetime.now(timezone.utc) + timedelta(seconds=undo_delay())


def queue(account: str, to: str, cc: str, bcc: str, subject: str, html: str, files: list, at: datetime, *, draft_id: str | None = None,
          reply_to: str | None = None, followup_days: int = 0, source: str = "", api_key: str = "", importance: int = 1, receipt: bool = False) -> str:
    """Met le message dans la file d'envoi pour l'heure `at` ; retire le brouillon d'origine. Retourne l'identifiant de l'envoi."""
    oid = uuid.uuid4().hex
    db.outbox_add({"id": oid, "account": account, "to": to, "cc": cc, "bcc": bcc or "", "subject": subject, "html": html,
                   "body": html_to_text(html or "").strip(), "draft_id": draft_id, "reply_to": reply_to,
                   "send_at": at.astimezone(timezone.utc).isoformat(), "followup_days": int(followup_days or 0), "source": source, "api_key": api_key, "importance": importance, "receipt": int(bool(receipt))}, list(files or []))
    if draft_id:
        db.draft_delete(draft_id)
        db.kv_set(f"api_send_at:{draft_id}", "")
    return oid


def to_draft(oid: str, source: str = "", api_key: str = "", note: str = "") -> str | None:
    """Rappelle un message pas encore parti : il redevient un brouillon modifiable. Retourne l'identifiant du brouillon (None si introuvable)."""
    row = db.outbox_get(oid)
    if not row:
        return None
    files = db.outbox_files(oid)
    did = row["draft_id"] or uuid.uuid4().hex
    db.draft_save({"id": did, "account": row["account"], "is_new": not row["reply_to"] or row["reply_to"].startswith("fwd:"), "reply_to": row["reply_to"], "to": row["to_addrs"], "cc": row["cc"],
                   "bcc": row["bcc"] or "", "subject": row["subject"], "html": row["html"],
                   "importance": 1 if row.get("importance") is None else int(row["importance"])}, files)
    if source == "api":
        db.draft_mark_api(did, api_key, note)
    db.outbox_delete(oid)
    return did
