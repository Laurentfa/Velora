"""Recherche avancée : du texte libre et des filtres du type  de:martin  pj:oui  avant:31/01/2026  dossier:archive  non-lu.

Le texte libre passe par la recherche plein texte ; les filtres deviennent des conditions SQL. Rien n'est envoyé à l'IA."""
import re
import unicodedata
from datetime import date, datetime, timedelta, timezone

from . import db

HELP = [
    ("de:martin", "expéditeur (nom ou adresse)"), ("à:dupont", "destinataire"), ("objet:devis", "mot dans l'objet"),
    ("pj:oui", "avec pièce jointe (pj:non : sans)"), ("non-lu", "mails non lus (lu : lus)"), ("épinglé", "mails épinglés"),
    ("depuis:01/09/2026  avant:01/10/2026", "période, jour/mois/année (aussi 7j, 2s, 3m, hier)"), ("cat:newsletter", "catégorie : action, info, newsletter, notification, perso"),
    ("dossier:archive", "dossier (tous : partout)"),
]
_TOKEN = re.compile(r'(?<!\S)(-?)([^\s:"]+):("([^"]*)"|\S*)')
_FLAGS = {"non-lu": ("is_read=0", None), "nonlu": ("is_read=0", None), "unread": ("is_read=0", None),
          "lu": ("is_read=1", None), "read": ("is_read=1", None),
          "épinglé": ("flagged=1", None), "epingle": ("flagged=1", None), "pinned": ("flagged=1", None)}


def _norm(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s.lower()) if unicodedata.category(c) != "Mn")


def _fold(s: str) -> str:
    """Minuscules sans accents, EN GARDANT la même longueur que le texte d'origine (pour reporter les positions sur l'original)."""
    return "".join(unicodedata.normalize("NFD", c)[0].lower() for c in s)


def highlight_terms(q: str) -> list[str]:
    """Mots à surligner dans les résultats : le texte libre de la recherche et les valeurs de « de: » et « objet: » (pas les filtres exclus)."""
    adv = parse(q or "")
    words = (adv["text"] if adv["advanced"] else q or "").replace('"', " ").replace("*", " ").split()
    for m in _TOKEN.finditer(q or ""):
        if m.group(1) != "-" and _norm(m.group(2)) in ("de", "from", "exp", "objet", "subject", "sujet"):
            words += (m.group(4) if m.group(4) is not None else m.group(3)).replace('"', " ").split()
    seen, out = set(), []
    for w in words:
        w = _fold(w)
        if len(w) >= 2 and w not in seen:
            seen.add(w)
            out.append(w)
    return out


def _spans(text: str, terms: list[str]) -> list[tuple[int, int]]:
    """Positions des mots cherchés dans le texte, sans tenir compte de la casse ni des accents ; comme la recherche, un mot peut être
    tapé en partie (« lemas » trouve « Lemasson »)."""
    if not terms:
        return []
    folded = _fold(text)
    rx = re.compile(r"(?<!\w)(?:" + "|".join(re.escape(t) for t in sorted(terms, key=len, reverse=True)) + ")")
    return [m.span() for m in rx.finditer(folded)]


def highlight_html(text: str, terms: list[str]) -> str:
    """Le texte échappé (jamais de HTML venu du mail), avec <mark> autour des mots cherchés."""
    import html as _html
    out, pos = [], 0
    for a, b in _spans(text or "", terms):
        out += [_html.escape(text[pos:a]), "<mark>", _html.escape(text[a:b]), "</mark>"]
        pos = b
    out.append(_html.escape((text or "")[pos:]))
    return "".join(out)


def snippet(body: str, terms: list[str], before: int = 40, after: int = 110) -> str:
    """Extrait du texte autour du premier mot cherché (« …le patient sous VNI… »), ou « » s'il n'y en a pas."""
    body = " ".join((body or "").split())
    spans = _spans(body, terms)
    if not spans:
        return ""
    a = spans[0][0]
    start = max(0, a - before)
    if start:
        start = body.rfind(" ", 0, start + 1) + 1 or start  # ne pas couper un mot
    end = min(len(body), a + after)
    return ("…" if start else "") + body[start:end].strip() + ("…" if end < len(body) else "")


def _yes(v: str) -> bool:
    return _norm(v) in ("", "oui", "o", "yes", "y", "true", "1", "vrai")


def _when(v: str) -> str | None:
    """Date ISO (début de journée UTC) d'après 01/09/2026 (jour/mois/année, format européen ; 01-09-2026 et 01.09.2026 aussi), 2026-09-01, 7j, 2s, 3m, aujourdhui, hier."""
    v = _norm(v.strip())
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    if v in ("aujourdhui", "today"):
        return today.isoformat()
    if v in ("hier", "yesterday"):
        return (today - timedelta(days=1)).isoformat()
    m = re.fullmatch(r"(\d+)([jsma])", v)
    if m:
        n, u = int(m.group(1)), m.group(2)
        days = n * {"j": 1, "s": 7, "m": 30, "a": 365}[u]
        return (today - timedelta(days=days)).isoformat()
    eu = re.fullmatch(r"(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{4})", v)  # jour/mois/année : jamais mois/jour (le format américain n'est pas accepté)
    try:
        d = date(int(eu.group(3)), int(eu.group(2)), int(eu.group(1))) if eu else date.fromisoformat(v)
        return datetime.combine(d, datetime.min.time(), tzinfo=timezone.utc).isoformat()
    except ValueError:
        return None


def parse(q: str) -> dict:
    """{'text': texte libre, 'where': [conditions SQL], 'args': [...], 'all_folders': bool, 'advanced': bool}"""
    where: list[str] = []
    args: list = []
    all_folders = False
    advanced = False
    rest = q or ""

    def like(cols: list[str], val: str, neg: bool) -> None:
        w = "(" + " OR ".join(f"{c} LIKE ? ESCAPE '\\'" for c in cols) + ")"
        where.append(("NOT " if neg else "") + w)
        esc = "%" + val.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        args.extend([esc] * len(cols))

    for m in list(_TOKEN.finditer(q or "")):
        neg, key, raw = m.group(1) == "-", _norm(m.group(2)), (m.group(4) if m.group(4) is not None else m.group(3))
        val = raw.strip()
        ok = True
        if key in ("de", "from", "exp"):
            like(["sender", "sender_name"], val, neg)
        elif key in ("a", "to", "dest", "destinataire"):
            like(["to_addrs", "cc_addrs"], val, neg)
        elif key in ("objet", "subject", "sujet"):
            like(["subject"], val, neg)
        elif key in ("pj", "att", "attachment", "piece"):
            where.append("has_att=1" if _yes(val) != neg else "has_att=0")
        elif key in ("depuis", "apres", "after", "since"):
            d = _when(val)
            ok = d is not None
            if ok:
                where.append("date>=?"); args.append(d)
        elif key in ("avant", "before", "until"):
            d = _when(val)
            ok = d is not None
            if ok:
                where.append("date<?"); args.append(d)
        elif key in ("cat", "categorie", "category"):
            cat = {"perso": "personnel", "personnel": "personnel", "newsletters": "newsletter", "notifications": "notification"}.get(_norm(val), _norm(val))
            where.append(("category<>?" if neg else "category=?")); args.append(cat)
        elif key in ("dossier", "in", "folder"):
            if _norm(val) in ("tous", "partout", "all", "*"):
                all_folders = True
            else:
                with db.conn() as c:
                    keys = [r["key"] for r in c.execute("SELECT key FROM folders WHERE name LIKE ? OR path LIKE ?", (f"%{val}%", f"%{val}%"))]
                if keys:
                    all_folders = True
                    where.append(f"folder IN ({','.join('?' * len(keys))})"); args.extend(keys)
                else:
                    ok = False
        else:
            ok = False
        if ok:
            advanced = True
            rest = rest.replace(m.group(0), " ", 1)
    for tok in re.findall(r"(?<!\S)([^\s:]+)(?!\S)", rest):
        k = _norm(tok)
        if k in _FLAGS:
            where.append(_FLAGS[k][0])
            advanced = True
            rest = re.sub(rf"(?<!\S){re.escape(tok)}(?!\S)", " ", rest, count=1)
    return {"text": " ".join(rest.split()), "where": where, "args": args, "all_folders": all_folders, "advanced": advanced}
