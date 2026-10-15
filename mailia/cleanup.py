"""Nettoyage de la boîte : repérer les expéditeurs qui envoient beaucoup et que vous lisez peu, puis tout archiver ou se désabonner.
Calculs locaux (aucune IA). Jamais de suppression : « archiver » déplace les mails dans le dossier d'archive du compte."""
import json
import logging

from . import config, db, queries, sync

log = logging.getLogger("cleanup")


def ignored() -> set[str]:
    try:
        return set(json.loads(db.kv_get("cleanup_ignored") or "[]"))
    except ValueError:
        return set()


def ignore(sender: str) -> None:
    s = ignored() | {sender.lower()}
    db.kv_set("cleanup_ignored", json.dumps(sorted(s)))


def sender_stats(min_count: int = 3, max_read_pct: int = 100, limit: int = 60) -> list[dict]:
    """Expéditeurs de la boîte de réception, du plus bavard au moins bavard. read_pct = part de leurs mails que vous avez lus."""
    hidden = ignored()
    with db.conn() as c:
        rows = c.execute(
            f"""SELECT lower(sender) s, MAX(sender_name) name, MIN(account) account, COUNT(*) n, SUM(is_read=1) n_read, MAX(date) last,
                       SUM(category IN ('newsletter','notification')) noise, SUM(unsub IS NOT NULL AND unsub<>'') n_unsub
                FROM messages WHERE folder='INBOX' AND {queries.enabled_sql()} AND sender<>''
                GROUP BY lower(sender) HAVING COUNT(*)>=? ORDER BY COUNT(*) DESC LIMIT 400""", (min_count,)).fetchall()
    out = []
    for r in rows:
        if r["s"] in hidden:
            continue
        pct = round(100 * (r["n_read"] or 0) / r["n"])
        if pct > max_read_pct:
            continue
        out.append({"sender": r["s"], "name": r["name"] or r["s"], "account": r["account"], "n": r["n"], "n_read": r["n_read"] or 0,
                    "read_pct": pct, "last": r["last"], "noise": (r["noise"] or 0) * 2 > r["n"], "can_unsub": bool(r["n_unsub"])})
        if len(out) >= limit:
            break
    return out


def inbox_rows(sender: str) -> list[dict]:
    with db.conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM messages WHERE folder='INBOX' AND lower(sender)=? ORDER BY date DESC", (sender.lower(),))]


def archive_sender(sender: str, progress=None, stop=None) -> tuple[int, int]:
    """Archive tous les mails de la boîte de réception venant de cet expéditeur. (archivés, en échec)
    `progress(i, n)` est appelée après chaque mail ; `stop` (threading.Event) permet d'interrompre proprement entre deux mails."""
    rows = inbox_rows(sender)
    done = failed = 0
    for i, m in enumerate(rows, 1):
        if stop is not None and stop.is_set():
            break
        if config.env("MAILIA_DRY_RUN") == "1":  # essais : rien ne bouge
            log.warning("ARCHIVAGE SIMULÉ : %s (%s)", m["subject"], sender)
            done += 1
        else:
            try:
                sync.perform(m, "archive")
                done += 1
            except Exception as e:
                log.warning("archivage %s : %s", m["id"], e)
                failed += 1
                if failed >= 3 and done == 0:
                    raise  # dossier d'archive absent ou serveur injoignable : inutile d'insister
        if progress:
            progress(i, len(rows))
    return done, failed
