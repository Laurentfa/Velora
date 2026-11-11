"""Statistiques de courrier : mails reçus, lus et envoyés, par boîte et par période.
Calculées sur les mails gardés sur cet ordinateur (ceux supprimés ou jamais synchronisés ne comptent pas). « Lus » est l'état actuel : Velora ne sait pas quand un mail a été lu."""
from datetime import date, datetime, timedelta

from . import config, db

PERIODS = {"30j": "30 jours", "12m": "12 mois", "tout": "Tout"}
_SKIP = {"drafts", "trash", "junk"}  # ni reçus ni envoyés
_MIN_DATE = "1990-01-01"  # certaines boîtes ont des mails sans vraie date (1900-01-01)


def _bounds(period: str, today: date) -> tuple[str, int]:
    """(premier jour inclus, nombre de caractères de la date qui définissent un groupe : 10 = jour, 7 = mois, 4 = année)."""
    if period == "30j":
        return (today - timedelta(days=29)).isoformat(), 10
    if period == "12m":
        first = today.replace(day=1)
        for _ in range(11):
            first = (first - timedelta(days=1)).replace(day=1)
        return first.isoformat(), 7
    return _MIN_DATE, 7


def _label(key: str, n: int) -> str:
    if n == 10:
        return f"{key[8:10]}/{key[5:7]}"
    if n == 7:
        return f"{key[5:7]}/{key[2:4]}"
    return key


def _buckets(keys: list[str], n: int, today: date, first: str) -> list[str]:
    """Tous les groupes de la période, y compris ceux sans mail (le graphique n'a pas de trous)."""
    if n == 10:
        return [(date.fromisoformat(first) + timedelta(days=i)).isoformat() for i in range((today - date.fromisoformat(first)).days + 1)]
    start = date.fromisoformat(first[:7] + "-01")
    out, cur = [], start
    while cur <= today:
        out.append(cur.isoformat()[:7])
        cur = (cur.replace(day=28) + timedelta(days=4)).replace(day=1)
    return out


def summary(account: str = "", period: str = "12m", today: date | None = None) -> dict:
    """{'received', 'read', 'sent', 'per_day_received', 'per_day_sent', 'buckets': [(libellé, reçus, envoyés)], 'accounts': [(boîte, reçus, lus, envoyés)], 'first'}"""
    today = today or date.today()
    first, n = _bounds(period if period in PERIODS else "12m", today)
    where, args = "m.date >= ? AND m.date < ?", [first, (today + timedelta(days=2)).isoformat()]
    if account:
        where += " AND m.account = ?"
        args.append(account)
    with db.conn() as c:
        rows = c.execute(
            f"SELECT m.account a, substr(m.date,1,{n}) b, COALESCE(f.role,'') role, COUNT(*) n, SUM(CASE WHEN m.is_read=1 THEN 1 ELSE 0 END) r "
            f"FROM messages m LEFT JOIN folders f ON f.account=m.account AND f.key=m.folder WHERE {where} GROUP BY m.account, b, role", args).fetchall()
    recv: dict[str, int] = {}
    sent: dict[str, int] = {}
    per_acc: dict[str, list[int]] = {}
    read = 0
    seen_first = ""
    for r in rows:
        role = r["role"]
        if role in _SKIP:
            continue
        acc = per_acc.setdefault(r["a"], [0, 0, 0])
        if r["b"] and (not seen_first or r["b"] < seen_first):
            seen_first = r["b"]
        if role == "sent":
            sent[r["b"]] = sent.get(r["b"], 0) + r["n"]
            acc[2] += r["n"]
        else:
            recv[r["b"]] = recv.get(r["b"], 0) + r["n"]
            acc[0] += r["n"]
            acc[1] += r["r"] or 0
            read += r["r"] or 0
    if period == "tout" and seen_first:
        first = seen_first[:7] + "-01" if len(seen_first) >= 7 else first
        if len(seen_first) >= 7 and (today.year - int(seen_first[:4])) * 12 + today.month - int(seen_first[5:7]) > 36:
            n = 4  # plus de trois ans : un groupe par année
    keys = _buckets([], n, today, first) if n != 4 else [str(y) for y in range(int(first[:4]), today.year + 1)]
    if n == 4:  # on regroupe ce qui a été compté par mois
        def by_year(d: dict[str, int]) -> dict[str, int]:
            out: dict[str, int] = {}
            for k, v in d.items():
                out[k[:4]] = out.get(k[:4], 0) + v
            return out
        recv, sent = by_year(recv), by_year(sent)
    days = max(1, (today - date.fromisoformat(first[:10] if len(first) >= 10 else first + "-01")).days + 1)
    total_r, total_s = sum(recv.values()), sum(sent.values())
    names = {k: c.label for k, c in config.load_accounts(enabled_only=False).items()}
    return {
        "received": total_r, "read": read, "sent": total_s, "days": days,
        "per_day_received": total_r / days, "per_day_sent": total_s / days,
        "buckets": [(_label(k, n), recv.get(k, 0), sent.get(k, 0)) for k in keys],
        "accounts": sorted(((names.get(a, a), v[0], v[1], v[2]) for a, v in per_acc.items()), key=lambda x: -x[1]),
    }


def accounts() -> list[tuple[str, str]]:
    """[(clé, libellé)] des boîtes connues."""
    return [(k, c.label) for k, c in config.load_accounts(enabled_only=False).items()]


WAIT_MAX_DAYS = 60  # au-delà, un mail sans réponse n'est plus une relance mais un oubli assumé


def waiting(days: int = 3, account: str = "", now: datetime | None = None, limit: int = 50) -> list[dict]:
    """Mails reçus qui attendent une réponse depuis plus de `days` jours : [{id, account, sender, subject, date, days}], du plus ancien au plus récent.
    Un mail attend une réponse quand l'IA l'a jugé « à répondre », qu'il n'a ni réponse ni transfert, et qu'aucun mail envoyé n'est parti depuis dans sa conversation."""
    now = now or datetime.now().astimezone()
    old = (now - timedelta(days=days)).isoformat()
    floor = (now - timedelta(days=WAIT_MAX_DAYS)).isoformat()
    where, args = "COALESCE(f.role,'') IN ('inbox','') AND m.needs_reply = 1 AND COALESCE(m.verb,0) = 0 AND m.date <= ? AND m.date >= ?", [old, floor]
    if account:
        where += " AND m.account = ?"
        args.append(account)
    with db.conn() as c:
        rows = c.execute(
            f"SELECT m.id, m.account, m.ckey, m.sender, m.sender_name, m.subject, m.date FROM messages m "
            f"LEFT JOIN folders f ON f.account=m.account AND f.key=m.folder WHERE {where} "
            "AND NOT EXISTS (SELECT 1 FROM messages s JOIN folders sf ON sf.account=s.account AND sf.key=s.folder "
            "                WHERE s.ckey=m.ckey AND sf.role='sent' AND s.date > m.date) "
            "ORDER BY m.date LIMIT ?", [*args, limit * 4]).fetchall()
    out, seen = [], set()
    for r in rows:
        if r["ckey"] in seen:  # un seul mail par conversation : le plus ancien en attente
            continue
        seen.add(r["ckey"])
        try:
            age = (now - datetime.fromisoformat(r["date"])).days
        except ValueError:
            continue
        out.append({"id": r["id"], "account": r["account"], "sender": r["sender_name"] or r["sender"], "subject": r["subject"] or "(sans objet)", "date": r["date"], "days": age})
        if len(out) >= limit:
            break
    return out


def response_time(period: str = "30j", account: str = "", today: date | None = None) -> dict:
    """Temps de réponse habituel : pour chaque conversation, délai entre un mail reçu et le premier mail envoyé ensuite. {'median_hours': float|None, 'n': nombre de réponses}.
    La médiane évite qu'une réponse très tardive fausse le chiffre."""
    today = today or date.today()
    first, _ = _bounds(period if period in PERIODS else "30j", today)
    where, args = "s.date >= ?", [first]
    if account:
        where += " AND s.account = ?"
        args.append(account)
    with db.conn() as c:
        rows = c.execute(
            "SELECT s.ckey, s.date sd, (SELECT MAX(m.date) FROM messages m JOIN folders mf ON mf.account=m.account AND mf.key=m.folder "
            "                          WHERE m.ckey=s.ckey AND COALESCE(mf.role,'') NOT IN ('sent','drafts','trash','junk') AND m.date < s.date) rd "
            f"FROM messages s JOIN folders sf ON sf.account=s.account AND sf.key=s.folder WHERE sf.role='sent' AND {where}", args).fetchall()
    hours = []
    seen = set()
    for r in sorted(rows, key=lambda r: r["sd"]):
        if not r["rd"] or (r["ckey"], r["rd"]) in seen:  # une seule réponse par mail reçu : la première
            continue
        seen.add((r["ckey"], r["rd"]))
        try:
            delta = datetime.fromisoformat(r["sd"]) - datetime.fromisoformat(r["rd"])
        except ValueError:
            continue
        h = delta.total_seconds() / 3600
        if 0 <= h <= 24 * 30:
            hours.append(h)
    hours.sort()
    return {"median_hours": (hours[len(hours) // 2] if len(hours) % 2 else (hours[len(hours) // 2 - 1] + hours[len(hours) // 2]) / 2) if hours else None, "n": len(hours)}


def hours_text(h: float | None) -> str:
    if h is None:
        return "—"
    if h < 1:
        return f"{max(1, round(h * 60))} min"
    if h < 24:
        return f"{int(h)} h {round((h - int(h)) * 60):02d}"
    return f"{round(h / 24, 1)} j".replace(".", ",")
