"""Requêtes de lecture sur la base locale (aucune interface ici)."""
import json
import logging
import threading
import time

from . import ai, aipolicy, config, db, search

TODO_SQL = "(needs_reply=1 OR category='action' OR priority=3) AND category NOT IN ('newsletter','notification')"
LIST_COLS = "id,account,folder,ckey,subject,sender,sender_name,date,is_read,has_att,summary,category,priority,needs_reply,suggested,verb"


def enabled_sql(col: str = "account") -> str:
    """Condition SQL « boîte active » : les mails d'une boîte désactivée sont conservés mais invisibles partout."""
    keys = [k.replace("'", "''") for k in config.enabled_keys()]
    return f"{col} IN ({','.join(repr(k) for k in keys)})" if keys else "0"


def list_messages(account: str = "", q: str = "", unread: bool = False, view: str = "",
                  limit: int = 80, offset: int = 0, folder: str = "", group: bool = True, pins_top: bool = True, read: bool = False, oldest_first: bool = False,
                  extra_ids: set[str] | None = None, everywhere: bool = False) -> list[dict]:
    """Sans `folder`, on ne parle que des boîtes de réception ; avec `folder`, d'un dossier précis.
    Par défaut les mails d'un même fil sont regroupés (le plus récent représente la conversation) ;
    une recherche textuelle reste, elle, mail par mail. Les conversations épinglées passent en tête de la boîte de réception."""
    searching = bool(q.strip())  # une recherche (texte ou filtres) reste mail par mail, sans épingles ni reports
    grouped = group and config.conversations_enabled() and not searching
    adv = search.parse(q) if q.strip() else {"text": "", "where": [], "args": [], "all_folders": False, "advanced": False}
    q = adv["text"] if adv["advanced"] else q
    where: list[str] = [enabled_sql()]
    args: list = []
    scope: list[tuple[str, list]] = []  # dossier + texte : ce qui décide « ce mail correspond à la recherche »
    if everywhere and searching and not folder and not view and not adv["all_folders"]:
        # recherche depuis la boîte de réception : tous les dossiers de la boîte, sauf corbeille, indésirables et brouillons
        adv["all_folders"] = True
        where.append("folder NOT IN (SELECT key FROM folders WHERE account=messages.account AND role IN ('trash','junk','drafts'))")
    if not adv["all_folders"]:
        scope.append(("folder=?", [folder or "INBOX"]))
    where += adv["where"]; args += adv["args"]
    if view == "todo":
        where.append(TODO_SQL)
    elif view == "noise":
        where.append("category IN ('newsletter','notification')")
    elif view in ("newsletter", "notification"):
        where.append("category=?"); args.append(view)
    if account:
        where.append("account=?"); args.append(account)
    if unread:
        where.append("is_read=0")
    if read:  # conversations entièrement lues
        where.append("NOT EXISTS (SELECT 1 FROM messages m2 WHERE m2.ckey=messages.ckey AND m2.folder=messages.folder AND m2.is_read=0)")
    if q.strip():
        terms = " ".join(f'"{t}"*' for t in q.replace('"', " ").split())
        scope.append(("id IN (SELECT id FROM fts WHERE fts MATCH ?)", [terms]))
    if not folder and not adv["all_folders"]:  # boîte de réception : les conversations reportées sont masquées jusqu'à l'heure dite
        where.append("ckey NOT IN (SELECT ckey FROM snoozes WHERE status='wait')")
    if scope:
        group_sql, group_args = " AND ".join(c for c, _ in scope), [a for _, aa in scope for a in aa]
        if extra_ids:  # mails trouvés par le serveur : ils comptent même si le texte local ou le dossier ne les retrouve pas (les autres filtres s'appliquent)
            ids = sorted(extra_ids)[:900]
            group_sql = f"(({group_sql}) OR id IN ({','.join('?' * len(ids))}))"
            group_args += ids
        where.append(group_sql); args += group_args
    woke = db.snooze_keys("woke") if (not folder and not searching and pins_top) else set()
    pins = (db.pin_keys() | woke) if (not folder and not searching and pins_top) else set()

    def fetch(c, extra: str, extra_args: list, lim: int, off: int) -> list[dict]:
        fetch_limit = lim * 4 if grouped else lim
        conds = where + ([extra] if extra else [])
        if oldest_first and grouped:  # un fil est représenté par son message le plus récent, quel que soit le sens du tri
            conds.append("date=(SELECT MAX(m3.date) FROM messages m3 WHERE m3.ckey=messages.ckey AND m3.folder=messages.folder)")
        sql = f"SELECT {LIST_COLS} FROM messages WHERE " + " AND ".join(conds)
        sql += f" ORDER BY date {'ASC' if oldest_first else 'DESC'} LIMIT ? OFFSET ?"
        rows = [dict(r) for r in c.execute(sql, (*args, *extra_args, fetch_limit, off))]
        if not grouped:
            for r in rows:
                r["conv_n"], r["conv_unread"] = 1, 0 if r["is_read"] else 1
            return rows
        reps, seen = [], set()
        for r in rows:  # trié du plus récent au plus ancien : le premier de chaque fil le représente
            if r["ckey"] not in seen:
                seen.add(r["ckey"])
                reps.append(r)
                if len(reps) >= lim:
                    break
        stats: dict = {}
        keys = [r["ckey"] for r in reps]
        for n in range(0, len(keys), 400):
            part = keys[n:n + 400]
            marks = ",".join("?" * len(part))
            for s in c.execute(f"SELECT ckey, COUNT(*) n, SUM(is_read=0) u FROM messages WHERE ckey IN ({marks}) GROUP BY ckey", part):
                stats[s["ckey"]] = (s["n"], s["u"] or 0)
        for r in reps:
            r["conv_n"], r["conv_unread"] = stats.get(r["ckey"], (1, 0 if r["is_read"] else 1))
        return reps

    with db.conn() as c:
        if not pins:
            return fetch(c, "", [], limit, offset)
        marks = ",".join("?" * len(pins))
        top = fetch(c, f"ckey IN ({marks})", list(pins), 200, 0) if offset == 0 else []
        real_pins = db.pin_keys()
        for r in top:
            r["woke"] = r["ckey"] in woke
            r["pinned"] = r["ckey"] in real_pins and not r["woke"]
        top.sort(key=lambda r: (not r["woke"],))  # « à revoir » d'abord, puis les épinglés (ordre par date conservé)
        return top + fetch(c, f"ckey NOT IN ({marks})", list(pins), limit, offset)


def thread(ckey: str) -> list[dict]:
    return db.thread_rows(ckey)


def get_message(mid: str) -> dict | None:
    with db.conn() as c:
        r = c.execute("SELECT * FROM messages WHERE id=?", (mid,)).fetchone()
    return dict(r) if r else None


def set_read(mid: str) -> None:
    with db.conn() as c:
        c.execute("UPDATE messages SET is_read=1 WHERE id=?", (mid,))


def ai_scope_clause() -> tuple[str, list]:
    """Mails que l'analyse automatique a le droit de traiter : boîte de réception et, selon les réglages,
    non lus seulement / pas plus vieux que N jours."""
    from datetime import datetime, timedelta, timezone
    from . import config
    sql, args = f"folder='INBOX' AND {enabled_sql()}", []
    ex_sql, ex_args = aipolicy.sql_clause()  # boîtes et expéditeurs exclus : jamais envoyés à l'IA
    sql += f" AND {ex_sql}"
    args += ex_args
    if config.ai_unread_only():
        sql += " AND is_read=0"
    days = config.ai_max_age_days()
    if days:
        sql += " AND date >= ?"
        args.append((datetime.now(timezone.utc) - timedelta(days=days)).isoformat())
    return sql, args


def ai_pending_clause() -> tuple[str, list]:
    sql, args = ai_scope_clause()
    return "ai_at IS NULL AND " + sql, args


def reanalyze_count() -> int:
    """Combien de mails déjà analysés repasseraient par l'IA (même périmètre que l'analyse automatique)."""
    sql, args = ai_scope_clause()
    with db.conn() as c:
        return c.execute(f"SELECT COUNT(*) FROM messages WHERE ai_at IS NOT NULL AND {sql}", args).fetchone()[0]


def reanalyze_reset() -> int:
    """Remet ces mails en file d'attente : l'analyse automatique les traite avec les nouvelles consignes."""
    sql, args = ai_scope_clause()
    with db.conn() as c:
        return c.execute(f"UPDATE messages SET ai_at=NULL WHERE ai_at IS NOT NULL AND {sql}", args).rowcount


# résumés écrits quand l'IA n'avait pas le texte du message : ils disent « sans contenu », « message vide »…
BODYLESS_MARKERS = ("sans contenu", "contenu visible", "contenu incomplet", "contenu détaillé", "message vide", "aucun contenu", "corps du message vide",
                    "corps du message est vide", "contexte est incomplet", "contenu non chargé")


def requeue_bodyless_summaries() -> int:
    """Remet en file d'analyse les messages dont le résumé dit « sans contenu » alors que leur texte est maintenant en base."""
    cond = " OR ".join("lower(summary) LIKE ?" for _ in BODYLESS_MARKERS)
    with db.conn() as c:
        return c.execute(f"UPDATE messages SET ai_at=NULL WHERE ai_at IS NOT NULL AND length(trim(COALESCE(body,''))) >= 80 AND ({cond})",
                         [f"%{m}%" for m in BODYLESS_MARKERS]).rowcount


def counts() -> dict:
    act = enabled_sql()
    inbox = f"folder='INBOX' AND {act} AND ckey NOT IN (SELECT ckey FROM snoozes WHERE status='wait')"
    with db.conn() as c:
        per = {r["account"]: r["u"] or 0 for r in c.execute(
            f"SELECT account, SUM(is_read=0) u FROM messages WHERE {inbox} GROUP BY account")}
        folders = {f"{r['account']}|{r['folder']}": r["u"] or 0 for r in c.execute(
            f"SELECT account, folder, SUM(is_read=0) u FROM messages WHERE folder<>'INBOX' AND {act} GROUP BY account, folder")}
        one = lambda sql: c.execute(sql).fetchone()[0] or 0
        cond, cargs = ai_pending_clause()
        n = "COUNT(DISTINCT ckey)" if config.conversations_enabled() else "COUNT(*)"  # comme la liste : une ligne par conversation
        tot_acc = {r["account"]: r["t"] for r in c.execute(f"SELECT account, {n} t FROM messages WHERE {inbox} GROUP BY account")}
        tot_fold = {f"{r['account']}|{r['folder']}": r["t"] for r in c.execute(
            f"SELECT account, folder, {n} t FROM messages WHERE folder<>'INBOX' AND {act} GROUP BY account, folder")}
        return {
            "accounts": per,
            "folders": folders,
            "tot_accounts": tot_acc,
            "tot_folders": tot_fold,
            "tot_all": one(f"SELECT {n} FROM messages WHERE {inbox}"),
            "tot_todo": one(f"SELECT {n} FROM messages WHERE {inbox} AND {TODO_SQL}"),
            "tot_newsletter": one(f"SELECT {n} FROM messages WHERE {inbox} AND category='newsletter'"),
            "tot_notification": one(f"SELECT {n} FROM messages WHERE {inbox} AND category='notification'"),
            "newsletter": one(f"SELECT COUNT(*) FROM messages WHERE {inbox} AND is_read=0 AND category='newsletter'"),
            "notification": one(f"SELECT COUNT(*) FROM messages WHERE {inbox} AND is_read=0 AND category='notification'"),
            "tot_noise": one(f"SELECT {n} FROM messages WHERE {inbox} AND category IN ('newsletter','notification')"),
            "unread": one(f"SELECT COUNT(*) FROM messages WHERE {inbox} AND is_read=0"),
            "todo": one(f"SELECT COUNT(*) FROM messages WHERE {inbox} AND is_read=0 AND {TODO_SQL}"),
            "noise": one(f"SELECT COUNT(*) FROM messages WHERE {inbox} AND is_read=0 AND category IN ('newsletter','notification')"),
            "pending": c.execute(f"SELECT COUNT(*) FROM messages WHERE {cond}", cargs).fetchone()[0],
            "drafts": db.drafts_count(),
            "outbox": db.outbox_count(),
            "snoozed": len(db.snoozes_list("wait")),
            "woke": len(db.snoozes_list("woke")),
            "followups": len(db.followups_due()),
            "tasks": db.tasks_open_count(),
        }


def version() -> tuple:
    """Change dès que quelque chose de visible a changé (synchro, lecture, suppression, analyse IA, synthèse)."""
    with db.conn() as c:
        s = c.execute(f"SELECT COUNT(*), SUM(is_read), COUNT(ai_at) FROM messages WHERE {enabled_sql()}").fetchone()
    return (db.kv_get("last_sync"), tuple(s), db.kv_get("brief:sig"))


# ---------- brief du jour ----------
_headline_lock = threading.Lock()


RETRY_AFTER = 120  # secondes : après un échec (réseau, clé, quota), on ne réessaie pas plus souvent


def _headline_work(sig: str, items: list[dict]) -> None:
    """Rédige la synthèse. En cas d'échec, le message est affiché mais la synthèse n'est PAS marquée « à jour » : elle sera retentée
    (sinon une coupure réseau au premier affichage du matin gâcherait le brief de toute la journée)."""
    ok = False
    try:
        db.kv_set("brief:headline", json.dumps(ai.brief_structured(items), ensure_ascii=False))
        ok = True
    except ai.AIError as e:
        db.kv_set("brief:headline", json.dumps({"intro": f"Synthèse indisponible : {e}", "urgent": [], "waiting": [], "info": ""}, ensure_ascii=False))
    except Exception:
        logging.getLogger("brief").exception("synthèse")
    finally:
        if ok:
            db.kv_set("brief:sig", sig)
            db.kv_set("brief:fail_at", "")
        else:
            db.kv_set("brief:fail_at", str(time.time()))
        _headline_lock.release()


def _refresh_headline(sig: str, items: list[dict]) -> None:
    """Rédige la synthèse en arrière-plan : l'interface ne l'attend jamais."""
    if not _headline_lock.acquire(blocking=False):
        return
    threading.Thread(target=_headline_work, args=(sig, items), daemon=True).start()


def _recently_failed() -> bool:
    try:
        return time.time() - float(db.kv_get("brief:fail_at") or 0) < RETRY_AFTER
    except ValueError:
        return False


def brief(refresh: bool = False) -> dict:
    with db.conn() as c:
        rows = [dict(r) for r in c.execute(
            f"SELECT {LIST_COLS} FROM messages WHERE folder='INBOX' AND {enabled_sql()} AND is_read=0 AND ai_at IS NOT NULL "
            "AND ckey NOT IN (SELECT ckey FROM snoozes WHERE status='wait') "
            "ORDER BY priority DESC, date DESC LIMIT 80")]
    todo = [r for r in rows if (r["needs_reply"] or r["category"] == "action" or r["priority"] == 3)
            and r["category"] not in ("newsletter", "notification")]
    todo_ids = {r["id"] for r in todo}
    noise = [r for r in rows if r["category"] in ("newsletter", "notification") and r["id"] not in todo_ids]
    noise_ids = {r["id"] for r in noise}
    info = [r for r in rows if r["id"] not in todo_ids and r["id"] not in noise_ids]
    for part in (todo, info, noise):
        part.sort(key=lambda r: r["date"], reverse=True)

    sig = f"{len(rows)}:{rows[0]['id'] if rows else ''}"
    if not config.ai_enabled():
        return {"headline": "", "summary": None, "todo": todo, "info": info, "noise": noise}
    if refresh or (db.kv_get("brief:sig") != sig and not _recently_failed()):
        _refresh_headline(sig, [r for r in (todo + info) if not aipolicy.is_excluded(r)][:25])  # les exclus ne partent pas chez l'IA
    raw = db.kv_get("brief:headline") or ""
    try:
        summary = json.loads(raw) if raw.startswith("{") else {"intro": raw or "Synthèse en cours de rédaction…", "urgent": [], "waiting": [], "info": ""}
    except json.JSONDecodeError:
        summary = {"intro": raw, "urgent": [], "waiting": [], "info": ""}
    return {"headline": raw, "summary": summary, "todo": todo, "info": info, "noise": noise}


def digest_text(use_ai: bool = False) -> tuple[str, str]:
    """(titre, texte) du résumé quotidien : ce qui attend, sans appel à l'IA sauf demande (une phrase de synthèse)."""
    c = counts()
    with db.conn() as cx:
        urgent = [dict(r) for r in cx.execute(
            f"SELECT sender, sender_name, subject, summary FROM messages WHERE folder='INBOX' AND {enabled_sql()} AND is_read=0 "
            f"AND ckey NOT IN (SELECT ckey FROM snoozes WHERE status='wait') AND ({TODO_SQL}) ORDER BY priority DESC, date DESC LIMIT 3")]
    parts = []
    if c["todo"]:
        parts.append(f"{c['todo']} à traiter")
    parts.append(f"{c['unread']} non lu(s)")
    if c.get("followups"):
        parts.append(f"{c['followups']} à relancer")
    if c.get("tasks"):
        parts.append(f"{c['tasks']} tâche(s)")
    text = " · ".join(parts)
    if urgent:
        text += ". En tête : " + " ; ".join(f"{u['sender_name'] or u['sender']} — {u['subject']}" for u in urgent[:2])
    if use_ai and urgent and config.ai_enabled():
        try:
            intro = (ai.brief_structured(urgent) or {}).get("intro", "")
            if intro:
                text = intro + " " + text
        except Exception:
            logging.getLogger("queries").debug("phrase de synthèse du résumé quotidien indisponible", exc_info=True)
    return "Votre brief du jour", text


def snoozed_rows() -> list[dict]:
    """Une ligne par conversation reportée (son message le plus récent), avec l'heure de réveil."""
    out = []
    for sn in db.snoozes_list("wait"):
        rows = db.thread_rows(sn["ckey"])
        inbox = [r for r in rows if r["folder"] == "INBOX"] or rows
        if inbox:
            r = dict(inbox[-1])
            r["until"] = sn["until"]
            out.append(r)
    return out
