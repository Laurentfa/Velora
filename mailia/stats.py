"""Statistiques de courrier : mails reçus, lus et envoyés, par boîte et par période.
Calculées sur les mails gardés sur cet ordinateur (ceux supprimés ou jamais synchronisés ne comptent pas). « Lus » est l'état actuel : Velora ne sait pas quand un mail a été lu."""
from datetime import date, timedelta

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
