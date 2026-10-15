"""Contrôles avant l'envoi : pièce jointe oubliée, objet vide, destinataire jamais contacté, très nombreux destinataires.
Tout est local et gratuit (aucune IA). Ce sont des avertissements : l'utilisateur garde toujours la main."""
import re

from . import db
from .compose import split_user_part
from .textutil import html_to_text

ATTACH_WORDS = re.compile(
    r"\b(ci[\s-]?joint(?:e|es|s)?|pi[èe]ces?\s+jointes?|en\s+annexe|ci[\s-]?annex[ée]e?s?|je\s+joins|vous\s+trouverez\s+(?:ci|en)|"
    r"attached|attachment|enclosed|i(?:'m|\s+am)\s+attaching)\b", re.I)
MANY = 10


def enabled() -> bool:
    return db.kv_get("send_checks") != "0"


def _addrs(*fields: str) -> list[str]:
    out: list[str] = []
    for f in fields:
        for a in (f or "").replace(";", ",").split(","):
            a = a.strip().lower()
            if a and a not in out:
                out.append(a)
    return out


def _known(addrs: list[str]) -> set[str]:
    if not addrs:
        return set()
    marks = ",".join("?" * len(addrs))
    with db.conn() as c:
        rows = c.execute(f"SELECT email FROM contacts WHERE email IN ({marks}) AND (n_to>0 OR n_from>0 OR source<>'mail' OR favorite=1)", addrs)
        return {r[0] for r in rows}


def send_warnings(html: str, subject: str, to: str, cc: str, bcc: str, n_files: int, is_reply: bool = False, mine: set[str] | None = None) -> list[str]:
    """Phrases d'avertissement, de la plus importante à la moins importante (liste vide : rien à signaler)."""
    if not enabled():
        return []
    out: list[str] = []
    top, _ = split_user_part(html or "")
    written = html_to_text(top)
    if ATTACH_WORDS.search(written) and not n_files and "<img" not in top:
        out.append("Vous parlez d'une pièce jointe, mais aucun fichier n'est joint.")
    if not (subject or "").strip():
        out.append("L'objet est vide.")
    addrs = _addrs(to, cc, bcc)
    if len(addrs) >= MANY:
        out.append(f"Ce message part à {len(addrs)} destinataires.")
    if not is_reply:
        known = _known(addrs)
        new = [a for a in addrs if a not in known and a not in (mine or set())]
        if new:
            shown = ", ".join(new[:3]) + (f" et {len(new) - 3} autre(s)" if len(new) > 3 else "")
            out.append(f"Première fois que vous écrivez à : {shown}. Vérifiez l'adresse.")
    return out
