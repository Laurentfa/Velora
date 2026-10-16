import atexit
import json
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta

from . import perf, vault
from .config import DB_PATH

_lock = threading.RLock()  # réentrant : un appel imbriqué ne doit jamais bloquer toute l'appli

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
  id TEXT PRIMARY KEY,           -- "<compte>|<dossier>|<uid>"
  account TEXT NOT NULL,
  uid TEXT NOT NULL,
  folder TEXT DEFAULT 'INBOX',
  subject TEXT, sender TEXT, sender_name TEXT, to_addrs TEXT,
  body_html TEXT,                -- HTML d'origine ; NULL = pas encore chargé, '' = message sans HTML
  conv_id TEXT, ckey TEXT,       -- ckey = compte|conversation : même valeur pour tous les mails d'un fil
  cc_addrs TEXT, att_json TEXT,  -- att_json NULL = pièces jointes pas encore chargées
  date TEXT, body TEXT, is_read INTEGER DEFAULT 0, has_att INTEGER DEFAULT 0,
  summary TEXT, category TEXT, priority INTEGER, needs_reply INTEGER, suggested TEXT,
  ai_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_msg_date ON messages(date DESC);
CREATE INDEX IF NOT EXISTS idx_msg_folder ON messages(account, folder);
CREATE VIRTUAL TABLE IF NOT EXISTS fts USING fts5(id UNINDEXED, subject, sender, body, tokenize='unicode61 remove_diacritics 2');
CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS usage (month TEXT, model TEXT, calls INTEGER DEFAULT 0, tokens_in INTEGER DEFAULT 0,
  tokens_out INTEGER DEFAULT 0, PRIMARY KEY (month, model));
CREATE TABLE IF NOT EXISTS api_keys (   -- clés d'accès à l'API et au serveur MCP (seule l'empreinte est conservée)
  id TEXT PRIMARY KEY, name TEXT, prefix TEXT, hash TEXT UNIQUE, scopes TEXT, channels TEXT, accounts TEXT DEFAULT '[]', folders TEXT DEFAULT '[]',
  send_mode TEXT DEFAULT 'approval', daily_send_cap INTEGER DEFAULT 20, ai_daily_cap INTEGER DEFAULT 30, allowed_domains TEXT DEFAULT '[]',
  expires TEXT DEFAULT '', created TEXT, last_used TEXT DEFAULT '', revoked INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS api_log (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, key_id TEXT, key_name TEXT, channel TEXT, action TEXT,
  target TEXT, status TEXT, detail TEXT);
CREATE TABLE IF NOT EXISTS api_idem (key_id TEXT, idem TEXT, response TEXT, ts TEXT, PRIMARY KEY (key_id, idem));
CREATE TABLE IF NOT EXISTS usage_log (day TEXT, model TEXT, feature TEXT, calls INTEGER DEFAULT 0, tokens_in INTEGER DEFAULT 0,
  tokens_out INTEGER DEFAULT 0, PRIMARY KEY (day, model, feature));
CREATE TABLE IF NOT EXISTS corrections (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, sender TEXT, domain TEXT, subject TEXT, old_cat TEXT, new_cat TEXT);
CREATE TABLE IF NOT EXISTS templates (
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, subject TEXT DEFAULT '', html TEXT DEFAULT '', updated TEXT
);
CREATE TABLE IF NOT EXISTS drafts (   -- brouillons enregistrés automatiquement (locaux)
  id TEXT PRIMARY KEY, account TEXT, is_new INTEGER, reply_to TEXT,
  to_addrs TEXT, cc TEXT, subject TEXT, html TEXT, updated TEXT, bcc TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS contacts (   -- carnet d'adresses : contacts repérés dans les mails, importés d'Exchange ou ajoutés à la main
  email TEXT PRIMARY KEY, name TEXT DEFAULT '', source TEXT DEFAULT 'mail',
  n_to INTEGER DEFAULT 0, n_from INTEGER DEFAULT 0, last_at TEXT DEFAULT '', favorite INTEGER DEFAULT 0, hidden INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS groups (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, favorite INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS group_members (group_id INTEGER, email TEXT, PRIMARY KEY (group_id, email));
CREATE TABLE IF NOT EXISTS outbox (   -- messages en attente d'envoi : délai d'annulation ou envoi programmé
  id TEXT PRIMARY KEY, account TEXT, to_addrs TEXT, cc TEXT, subject TEXT, html TEXT, body TEXT, draft_id TEXT,
  reply_to TEXT, send_at TEXT, status TEXT DEFAULT 'pending', error TEXT DEFAULT '', created TEXT, followup_days INTEGER DEFAULT 0, bcc TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS outbox_files (outbox_id TEXT, name TEXT, mime TEXT, data BLOB);
CREATE TABLE IF NOT EXISTS followups (   -- « me relancer si pas de réponse »
  id INTEGER PRIMARY KEY AUTOINCREMENT, account TEXT, to_addrs TEXT, subject TEXT, sent_at TEXT, due_at TEXT,
  status TEXT DEFAULT 'wait', ckey TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS snoozes (   -- conversations reportées : masquées jusqu'à l'heure dite, puis « à revoir »
  ckey TEXT PRIMARY KEY, account TEXT, until TEXT, status TEXT DEFAULT 'wait'
);
CREATE TABLE IF NOT EXISTS tasks (
  id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT, due TEXT DEFAULT '', mid TEXT DEFAULT '', done INTEGER DEFAULT 0, created TEXT
);
CREATE TABLE IF NOT EXISTS rules (   -- règles de tri écrites en langage naturel, interprétées une fois
  id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT, spec TEXT, active INTEGER DEFAULT 1, hits INTEGER DEFAULT 0, created TEXT
);
CREATE TABLE IF NOT EXISTS draft_files (draft_id TEXT, name TEXT, mime TEXT, data BLOB);
CREATE TABLE IF NOT EXISTS draft_gone (id INTEGER PRIMARY KEY AUTOINCREMENT, account TEXT, ref TEXT, folder TEXT DEFAULT '');  -- copies de brouillons à supprimer sur le serveur
CREATE TABLE IF NOT EXISTS folders (
  account TEXT NOT NULL, key TEXT NOT NULL, name TEXT, path TEXT, role TEXT DEFAULT '',
  sync INTEGER DEFAULT 0,        -- 1 = dossier synchronisé
  state TEXT,                    -- état de synchro incrémentale (EWS) ou UIDVALIDITY (IMAP)
  PRIMARY KEY (account, key)
);
"""


_shared: dict = {"conn": None, "id": None, "checked": 0.0}  # connexion gardée ouverte, toujours sous _lock
_kv_cache: dict[str, tuple[float, str | None]] = {}  # lectures de réglages : l'affichage d'une liste en demande des centaines
KV_TTL = 5.0  # secondes ; le cache est aussi vidé dès qu'une écriture a lieu


def _file_id() -> tuple:
    """Identité du fichier de la base : si on le remplace (réparation, chiffrement), la connexion gardée doit être rouverte."""
    try:
        st = DB_PATH.stat()
        return str(DB_PATH), st.st_ino, st.st_dev
    except OSError:
        return str(DB_PATH), None, None


def close_shared() -> None:
    """Ferme la connexion gardée ouverte (avant de déplacer ou remplacer le fichier : Windows refuse sinon ; à l'arrêt pour nettoyer le journal)."""
    with _lock:
        _kv_cache.clear()
        c, _shared["conn"], _shared["id"] = _shared["conn"], None, None
        if c is not None:
            try:
                c.close()
            except Exception:
                pass


atexit.register(close_shared)


@contextmanager
def conn():
    """Une seule connexion, gardée ouverte : en ouvrir une à chaque requête coûte ~30 ms sous Windows (l'antivirus analyse les fichiers
    temporaires de la base recréés à chaque fois), soit des dizaines de secondes de perdues par minute. Le verrou _lock les sérialise."""
    t = time.perf_counter()
    with _lock:
        perf.record("base : attente du verrou", time.perf_counter() - t)  # long = un autre traitement garde la base
        t = time.perf_counter()
        now = time.monotonic()
        # l'identité du fichier est revérifiée au plus toutes les 2 s : un appel système par requête coûte cher sous Windows (antivirus)
        if _shared["conn"] is not None and (_shared["id"][0] != str(DB_PATH) or (now - _shared["checked"] > 2 and _shared["id"] != _file_id())):
            close_shared()
        if _shared["conn"] is None:
            _shared["conn"] = vault.connect(DB_PATH, timeout=30, check_same_thread=False)  # chiffrée ou non, selon le fichier
            _shared["id"] = _file_id()
            _kv_cache.clear()
        _shared["checked"] = now
        c = _shared["conn"]
        changes = getattr(c, "total_changes", 0)
        try:
            yield c
            c.commit()
            if getattr(c, "total_changes", 0) != changes:
                _kv_cache.clear()
        except BaseException:
            try:
                c.rollback()
            except Exception:
                close_shared()  # connexion inutilisable : la prochaine utilisation en rouvre une
            raise
        finally:
            perf.record("base : une utilisation", time.perf_counter() - t)


def exclusive(fn):
    """Exécute fn() pendant qu'aucune autre partie de l'appli n'utilise la base (changement de chiffrement)."""
    with _lock:
        close_shared()
        return fn()


def msg_id(account: str, folder: str, uid: str) -> str:
    return f"{account}|{folder}|{uid}"


SCHEMA_VERSION = 2
# migrations numérotées (PRAGMA user_version) : chacune ne s'exécute qu'une fois, dans l'ordre. Les colonnes ajoutées au fil du temps
# restent gérées plus bas, de façon idempotente, pour les bases créées avant la numérotation.
MIGRATIONS = {
    1: ["CREATE INDEX IF NOT EXISTS idx_msg_folder_read ON messages(folder, is_read)",
        "CREATE INDEX IF NOT EXISTS idx_msg_folder_date ON messages(folder, date DESC)",
        "CREATE INDEX IF NOT EXISTS idx_msg_category ON messages(category)",
        "CREATE INDEX IF NOT EXISTS idx_msg_sender ON messages(lower(sender))"],
    2: ["CREATE INDEX IF NOT EXISTS idx_log_key ON api_log(key_id, action, ts)"],
}


def _migrate(c) -> None:
    v = c.execute("PRAGMA user_version").fetchone()[0]
    for n in sorted(MIGRATIONS):
        if n > v:
            for sql in MIGRATIONS[n]:
                c.execute(sql)
            c.execute(f"PRAGMA user_version={n}")


def init() -> None:
    with conn() as c:
        c.execute("PRAGMA journal_mode=WAL")  # écritures plus sûres en cas d'arrêt brutal, lectures qui n'attendent pas les écritures
        c.executescript(SCHEMA)
        cols = {r["name"] for r in c.execute("PRAGMA table_info(messages)")}
        if "folder" not in cols:
            c.execute("ALTER TABLE messages ADD COLUMN folder TEXT DEFAULT 'INBOX'")
        for col in ("cc_addrs", "att_json", "conv_id", "ckey", "body_html", "unsub", "meeting", "rules_done", "bcc_addrs", "auth"):
            if col not in cols:
                c.execute(f"ALTER TABLE messages ADD COLUMN {col} TEXT")
        have_t = {r["name"] for r in c.execute("PRAGMA table_info(templates)")}
        for col in ("to_addrs", "cc", "bcc"):  # destinataires mémorisés avec le modèle
            if col not in have_t:
                c.execute(f"ALTER TABLE templates ADD COLUMN {col} TEXT DEFAULT ''")
        for tbl in ("drafts", "outbox"):
            have = {r["name"] for r in c.execute(f"PRAGMA table_info({tbl})")}
            if "bcc" not in have:
                c.execute(f"ALTER TABLE {tbl} ADD COLUMN bcc TEXT DEFAULT ''")
            if "importance" not in have:
                c.execute(f"ALTER TABLE {tbl} ADD COLUMN importance INTEGER DEFAULT 1")
            for col in ("source", "api_key") + (("api_note", "server_ref", "server_at", "server_folder") if tbl == "drafts" else ()):  # origine : « api » quand un programme ou un assistant l'a créé ; copie sur le serveur
                if col not in have:
                    c.execute(f"ALTER TABLE {tbl} ADD COLUMN {col} TEXT DEFAULT ''")
        if "importance" not in cols:  # importance fixée par l'expéditeur (0 basse, 1 normale, 2 haute)
            c.execute("ALTER TABLE messages ADD COLUMN importance INTEGER DEFAULT 1")
        if "verb" not in cols:
            c.execute("ALTER TABLE messages ADD COLUMN verb INTEGER DEFAULT 0")
            c.execute("UPDATE folders SET state=NULL")  # relecture complète une fois : récupère les marques « répondu / transféré » du serveur
        if "flagged" not in cols:
            c.execute("ALTER TABLE messages ADD COLUMN flagged INTEGER DEFAULT 0")
            c.execute("UPDATE folders SET state=NULL")  # relecture complète une fois : récupère les drapeaux déjà posés sur le serveur
        c.execute("UPDATE messages SET ckey=id WHERE ckey IS NULL")  # en attendant son identifiant de conversation, un mail est seul
        c.execute("CREATE INDEX IF NOT EXISTS idx_msg_ckey ON messages(ckey)")
        # anciens identifiants « compte:uid » -> « compte|dossier|uid » (les analyses IA sont conservées)
        for r in c.execute("SELECT id, account, uid, folder FROM messages WHERE id NOT LIKE '%|%'").fetchall():
            new = msg_id(r["account"], r["folder"] or "INBOX", r["uid"])
            c.execute("UPDATE messages SET id=? WHERE id=?", (new, r["id"]))
            c.execute("UPDATE fts SET id=? WHERE id=?", (new, r["id"]))
        # chaque compte a au moins sa boîte de réception, synchronisée
        for (acc,) in c.execute("SELECT DISTINCT account FROM messages").fetchall():
            c.execute("INSERT OR IGNORE INTO folders(account,key,name,path,role,sync) VALUES(?,?,?,?,?,1)",
                      (acc, "INBOX", "Boîte de réception", "Boîte de réception", "inbox"))
        _migrate(c)


# ---------- sauvegarde automatique de la base ----------
BACKUP_KEEP = 5


def backups_dir():
    from .config import DATA_DIR
    d = DATA_DIR / "backups"
    d.mkdir(exist_ok=True)
    try:
        d.chmod(0o700)
    except OSError:
        pass
    return d


def backup_now(keep: int = BACKUP_KEEP) -> str:
    """Copie cohérente du fichier de la base (chiffrée si elle l'est : même clé) dans le dossier « backups », en gardant les `keep` dernières."""
    import shutil
    with _lock:  # personne n'écrit pendant la copie
        with conn() as c:
            c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        dst = backups_dir() / f"mail-{datetime.now():%Y%m%d-%H%M%S}.db"
        shutil.copy2(DB_PATH, dst)
        try:
            dst.chmod(0o600)
        except OSError:
            pass
    old = sorted(backups_dir().glob("mail-*.db"))[:-keep] if keep else []
    for f in old:
        f.unlink(missing_ok=True)
    kv_set("db_backup_at", datetime.now().astimezone().isoformat())
    return str(dst)


def is_corruption(exc: BaseException) -> bool:
    """Fichier de base abîmé ou illisible (SQLite ou SQLCipher), par opposition à une clé de chiffrement introuvable (RuntimeError)."""
    import sqlite3
    return isinstance(exc, sqlite3.DatabaseError) or type(exc).__name__ == "DatabaseError"


def _opens_fine(path) -> bool:
    try:
        c = vault.connect(path, timeout=5)
        try:
            return c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        finally:
            c.close()
    except Exception:
        return False


def recover() -> str:
    """Base abîmée : le fichier est mis de côté (jamais supprimé), puis la copie automatique la plus récente qui passe le contrôle d'intégrité
    est remise en place. Sans copie valable, on repart d'une base vide : les mails se relisent depuis les serveurs.
    Retourne « sauvegarde <nom> » ou « vide »."""
    import shutil
    close_shared()
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    for suffix in ("", "-wal", "-shm"):
        f = DB_PATH.with_name(DB_PATH.name + suffix)
        if f.exists():
            f.replace(DB_PATH.with_name(f"{DB_PATH.name}.abimee-{stamp}{suffix}"))
    for bak in sorted(backups_dir().glob("mail-*.db"), reverse=True):
        if _opens_fine(bak):
            shutil.copy2(bak, DB_PATH)
            return f"sauvegarde {bak.name}"
    return "vide"


def backup_due(hours: int = 24) -> bool:
    if kv_get("db_backup") == "0":
        return False
    last = kv_get("db_backup_at")
    if not last:
        return True
    try:
        return (datetime.now().astimezone() - datetime.fromisoformat(last)).total_seconds() > hours * 3600
    except ValueError:
        return True


# ---------- messages ----------
def upsert(account: str, folder: str, m) -> bool:
    """Ajoute le message, ou met à jour son état lu/objet s'il existe. Retourne True s'il est nouveau."""
    mid = msg_id(account, folder, m.uid)
    with conn() as c:
        att = json.dumps(m.attachments) if m.attachments is not None else None
        if c.execute("SELECT 1 FROM messages WHERE id=?", (mid,)).fetchone():
            c.execute("UPDATE messages SET is_read=?, subject=?, flagged=?, verb=MAX(COALESCE(verb,0), ?), importance=? WHERE id=?",
                      (int(m.is_read), m.subject, int(m.flagged), int(m.verb or 0), int(m.importance), mid))
            if m.meeting:
                c.execute("UPDATE messages SET meeting=? WHERE id=?", (m.meeting, mid))
            if m.conv:
                c.execute("UPDATE messages SET conv_id=?, ckey=? WHERE id=? AND conv_id IS NULL", (m.conv, f"{account}|{m.conv}", mid))
            if m.html is not None:
                c.execute("UPDATE messages SET body_html=? WHERE id=? AND body_html IS NULL", (m.html, mid))
            if att is not None:  # complète les anciens messages dont on ne connaissait pas la copie ni les pièces jointes
                c.execute("UPDATE messages SET cc_addrs=?, att_json=?, to_addrs=? WHERE id=? AND att_json IS NULL",
                          (m.cc, att, m.to, mid))
            return False
        c.execute(
            "INSERT INTO messages(id,account,uid,folder,subject,sender,sender_name,to_addrs,cc_addrs,att_json,date,body,is_read,has_att,conv_id,ckey,body_html,flagged,meeting,verb,importance)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (mid, account, m.uid, folder, m.subject, m.sender, m.sender_name, m.to, m.cc, att, m.date, m.body,
             int(m.is_read), int(m.has_att), m.conv, f"{account}|{m.conv}" if m.conv else mid, m.html, int(m.flagged), m.meeting or None, int(m.verb or 0), int(m.importance)))
        c.execute("INSERT INTO fts(id,subject,sender,body) VALUES(?,?,?,?)",
                  (mid, m.subject, f"{m.sender_name} {m.sender}", m.body))
        return True


def set_importance(account: str, folder: str, uids: list[str], level: int) -> int:
    """Pose l'importance (0 basse, 2 haute) sur des messages déjà copiés (rattrapage des mails reçus avant que Velora lise cette marque)."""
    n = 0
    with conn() as c:
        for i in range(0, len(uids), 400):
            part = uids[i:i + 400]
            n += c.execute(f"UPDATE messages SET importance=? WHERE account=? AND folder=? AND uid IN ({','.join('?' * len(part))}) AND COALESCE(importance,1)<>?",
                           (level, account, folder, *part, level)).rowcount
    return n


def known(account: str, folder: str) -> dict[str, tuple[int, str]]:
    """uid -> (lu, date, drapeau) des messages déjà présents localement pour ce dossier."""
    with conn() as c:
        return {r["uid"]: (r["is_read"], r["date"], r["flagged"] or 0) for r in c.execute(
            "SELECT uid, is_read, date, flagged FROM messages WHERE account=? AND folder=?", (account, folder))}


def delete_messages(account: str, folder: str, uids: list[str]) -> None:
    with conn() as c:
        for uid in uids:
            mid = msg_id(account, folder, uid)
            c.execute("DELETE FROM messages WHERE id=?", (mid,))
            c.execute("DELETE FROM fts WHERE id=?", (mid,))


def uids_without_conv(account: str, folder: str, limit: int = 300) -> list[str]:
    with conn() as c:
        return [r["uid"] for r in c.execute(
            "SELECT uid FROM messages WHERE account=? AND folder=? AND conv_id IS NULL ORDER BY date DESC LIMIT ?",
            (account, folder, limit))]


def set_conv(account: str, folder: str, uid: str, conv: str | None) -> None:
    """conv=None : le serveur n'a pas su répondre ; on marque « sans fil » pour ne pas réessayer sans fin."""
    mid = msg_id(account, folder, uid)
    with conn() as c:
        if conv:
            c.execute("UPDATE messages SET conv_id=?, ckey=? WHERE id=?", (conv, f"{account}|{conv}", mid))
        else:
            c.execute("UPDATE messages SET conv_id='-' WHERE id=?", (mid,))


def thread_rows(ckey: str) -> list[dict]:
    """Tous les messages d'un fil (tous dossiers suivis), du plus ancien au plus récent."""
    with conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM messages WHERE ckey=? ORDER BY date ASC", (ckey,))]


def set_details(mid: str, to: str, cc: str, attachments: list, html: str | None = None, unsub: str | None = None, bcc: str | None = None,
                auth: str | None = None) -> None:
    with conn() as c:
        c.execute("UPDATE messages SET to_addrs=?, cc_addrs=?, att_json=?, has_att=? WHERE id=?",
                  (to, cc, json.dumps(attachments), int(bool(attachments)), mid))
        if html is not None:
            c.execute("UPDATE messages SET body_html=? WHERE id=?", (html, mid))
        if unsub is not None:
            c.execute("UPDATE messages SET unsub=? WHERE id=?", (unsub, mid))
        if bcc is not None:
            c.execute("UPDATE messages SET bcc_addrs=? WHERE id=?", (bcc, mid))
        if auth is not None:
            c.execute("UPDATE messages SET auth=? WHERE id=?", (auth, mid))


def set_body(mid: str, text: str) -> None:
    """Corps texte récupéré après coup (message enregistré sans contenu) : liste, recherche et IA en profitent."""
    with conn() as c:
        c.execute("UPDATE messages SET body=? WHERE id=?", (text, mid))
        c.execute("UPDATE fts SET body=? WHERE id=?", (text, mid))


def delete_by_id(mid: str) -> None:
    with conn() as c:
        c.execute("DELETE FROM messages WHERE id=?", (mid,))
        c.execute("DELETE FROM fts WHERE id=?", (mid,))


def set_read(account: str, folder: str, uid: str, read: bool) -> None:
    with conn() as c:
        c.execute("UPDATE messages SET is_read=? WHERE id=?", (int(read), msg_id(account, folder, uid)))


def set_read_by_id(mid: str, read: bool) -> None:
    with conn() as c:
        c.execute("UPDATE messages SET is_read=? WHERE id=?", (int(read), mid))


# ---------- dossiers ----------
def folders(account: str | None = None) -> list[dict]:
    with conn() as c:
        sql = "SELECT * FROM folders" + (" WHERE account=?" if account else "") + " ORDER BY account, (key='INBOX') DESC, path"
        return [dict(r) for r in c.execute(sql, (account,) if account else ())]


def save_folder_list(account: str, infos: list) -> None:
    """Met à jour la liste des dossiers du serveur en conservant le choix « synchroniser » et l'état."""
    with conn() as c:
        keys = []
        for f in infos:
            keys.append(f.key)
            c.execute(
                "INSERT INTO folders(account,key,name,path,role,sync) VALUES(?,?,?,?,?,?) "
                "ON CONFLICT(account,key) DO UPDATE SET name=excluded.name, path=excluded.path, role=excluded.role",
                (account, f.key, f.name, f.path, f.role, 1 if f.key == "INBOX" else 0))
        if keys:  # dossiers supprimés côté serveur
            marks = ",".join("?" * len(keys))
            c.execute(f"DELETE FROM folders WHERE account=? AND key NOT IN ({marks})", (account, *keys))


def set_folder_sync(account: str, keys: set[str]) -> None:
    with conn() as c:
        for r in c.execute("SELECT key FROM folders WHERE account=?", (account,)).fetchall():
            on = r["key"] == "INBOX" or r["key"] in keys
            c.execute("UPDATE folders SET sync=? WHERE account=? AND key=?", (int(on), account, r["key"]))


def purge_folder(account: str, key: str) -> None:
    """Retire de l'appli les mails copiés d'un dossier décoché. Le serveur n'est jamais touché."""
    with conn() as c:
        for r in c.execute("SELECT id FROM messages WHERE account=? AND folder=?", (account, key)).fetchall():
            c.execute("DELETE FROM fts WHERE id=?", (r["id"],))
        c.execute("DELETE FROM messages WHERE account=? AND folder=?", (account, key))
        c.execute("UPDATE folders SET state=NULL WHERE account=? AND key=?", (account, key))


def set_folder_state(account: str, key: str, state: str | None) -> None:
    with conn() as c:
        c.execute("UPDATE folders SET state=? WHERE account=? AND key=?", (state, account, key))


# ---------- divers ----------
def kv_get(k: str) -> str | None:
    hit = _kv_cache.get(k)
    if hit and time.monotonic() - hit[0] < KV_TTL:
        return hit[1]
    with conn() as c:
        r = c.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
        v = r["v"] if r else None
        _kv_cache[k] = (time.monotonic(), v)  # sous le verrou : une écriture concurrente ne peut pas glisser une valeur périmée
        return v


def kv_set(k: str, v: str) -> None:
    with conn() as c:
        c.execute("INSERT INTO kv(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, v))


def record_usage(model: str, tokens_in: int, tokens_out: int, feature: str = "") -> None:
    with conn() as c:
        c.execute(
            "INSERT INTO usage_log(day,model,feature,calls,tokens_in,tokens_out) VALUES(?,?,?,1,?,?) "
            "ON CONFLICT(day,model,feature) DO UPDATE SET calls=calls+1, tokens_in=tokens_in+excluded.tokens_in, "
            "tokens_out=tokens_out+excluded.tokens_out",
            (datetime.now().strftime("%Y-%m-%d"), model, feature, tokens_in, tokens_out))
        c.execute(
            "INSERT INTO usage(month,model,calls,tokens_in,tokens_out) VALUES(?,?,1,?,?) "
            "ON CONFLICT(month,model) DO UPDATE SET calls=calls+1, tokens_in=tokens_in+excluded.tokens_in, "
            "tokens_out=tokens_out+excluded.tokens_out",
            (datetime.now().strftime("%Y-%m"), model, tokens_in, tokens_out))


# ---------- modèles de réponse ----------
def templates_list() -> list[dict]:
    with conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM templates ORDER BY name COLLATE NOCASE")]


def template_save(tid: int | None, name: str, subject: str, html: str, to: str = "", cc: str = "", bcc: str = "") -> int:
    """`to`, `cc`, `bcc` : adresses mémorisées avec le modèle (ajoutées au message à l'utilisation, sans écraser celles déjà saisies)."""
    now = datetime.now().isoformat()
    with conn() as c:
        if tid:
            c.execute("UPDATE templates SET name=?, subject=?, html=?, to_addrs=?, cc=?, bcc=?, updated=? WHERE id=?",
                      (name, subject, html, to, cc, bcc, now, tid))
            return tid
        return c.execute("INSERT INTO templates(name,subject,html,to_addrs,cc,bcc,updated) VALUES(?,?,?,?,?,?,?)",
                         (name, subject, html, to, cc, bcc, now)).lastrowid


def template_delete(tid: int) -> None:
    with conn() as c:
        c.execute("DELETE FROM templates WHERE id=?", (tid,))


# ---------- brouillons ----------
def draft_save(d: dict, files: list | None = None) -> None:
    """Enregistre (ou met à jour) un brouillon. `files` : None = pièces jointes inchangées, sinon liste (nom, octets, type)."""
    with conn() as c:
        c.execute(
            "INSERT INTO drafts(id,account,is_new,reply_to,to_addrs,cc,bcc,subject,html,updated,importance) VALUES(?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET account=excluded.account, is_new=excluded.is_new, reply_to=excluded.reply_to, "
            "to_addrs=excluded.to_addrs, cc=excluded.cc, bcc=excluded.bcc, subject=excluded.subject, html=excluded.html, updated=excluded.updated, "
            "importance=excluded.importance",
            (d["id"], d["account"], int(d["is_new"]), d.get("reply_to"), d["to"], d["cc"], d.get("bcc", ""), d["subject"], d["html"],
             datetime.now().astimezone().isoformat(), int(d.get("importance", 1))))
        if files is not None:
            c.execute("DELETE FROM draft_files WHERE draft_id=?", (d["id"],))
            for name, data, mime in files:
                c.execute("INSERT INTO draft_files(draft_id,name,mime,data) VALUES(?,?,?,?)", (d["id"], name, mime, data))


def draft_mark_api(did: str, key_id: str, note: str = "") -> None:
    with conn() as c:
        c.execute("UPDATE drafts SET source='api', api_key=?, api_note=? WHERE id=?", (key_id, note, did))


# un brouillon est « sur le serveur » quand sa copie existe dans le dossier Brouillons de la boîte ET que ce dossier est suivi (donc visible)
_ON_SERVER = ("server_ref IS NOT NULL AND server_ref<>'' AND EXISTS (SELECT 1 FROM folders f WHERE f.account=drafts.account AND f.key=drafts.server_folder AND f.sync=1)")


def drafts_list(local_only: bool = False) -> list[dict]:
    """Tous les brouillons ; `local_only` : seulement ceux qu'on ne voit pas ailleurs (pas encore copiés sur le serveur, ou créés par l'API)."""
    with conn() as c:
        where = f" WHERE NOT ({_ON_SERVER})" if local_only else ""
        return [dict(r) for r in c.execute(f"SELECT * FROM drafts{where} ORDER BY updated DESC")]


def drafts_count() -> int:
    """Brouillons qu'on ne trouve que dans la vue « Brouillons » (les autres sont dans le dossier Brouillons de leur boîte)."""
    with conn() as c:
        return c.execute(f"SELECT COUNT(*) FROM drafts WHERE NOT ({_ON_SERVER})").fetchone()[0]


def drafts_to_push(limit: int = 5) -> list[dict]:
    """Brouillons à copier (ou recopier) sur le serveur : modifiés depuis la dernière copie ; ceux des assistants (API) restent locaux."""
    with conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM drafts WHERE COALESCE(source,'')<>'api' AND (server_at IS NULL OR server_at='' OR updated>server_at) ORDER BY updated LIMIT ?", (limit,))]


def draft_set_server(did: str, ref: str, at: str, folder: str) -> None:
    with conn() as c:
        c.execute("UPDATE drafts SET server_ref=?, server_at=?, server_folder=? WHERE id=?", (ref, at, folder, did))


def draft_by_ref(account: str, ref: str) -> dict | None:
    with conn() as c:
        r = c.execute("SELECT * FROM drafts WHERE account=? AND server_ref=?", (account, ref)).fetchone()
    return dict(r) if r else None


def draft_gone_add(account: str, ref: str, folder: str) -> None:
    with conn() as c:
        c.execute("INSERT INTO draft_gone(account, ref, folder) VALUES(?,?,?)", (account, ref, folder))


def drafts_gone() -> list[dict]:
    with conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM draft_gone ORDER BY id")]


def draft_gone_done(gid: int) -> None:
    with conn() as c:
        c.execute("DELETE FROM draft_gone WHERE id=?", (gid,))


def drafts_in_sync() -> list[dict]:
    """Brouillons dont la copie serveur est à jour (aucune modification depuis) : candidats au nettoyage si la copie a disparu du serveur."""
    with conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM drafts WHERE server_ref<>'' AND server_ref IS NOT NULL AND updated=server_at")]


def draft_load(did: str) -> tuple[dict | None, list]:
    with conn() as c:
        r = c.execute("SELECT * FROM drafts WHERE id=?", (did,)).fetchone()
        files = [(f["name"], bytes(f["data"]), f["mime"]) for f in c.execute("SELECT * FROM draft_files WHERE draft_id=?", (did,))]
    return (dict(r) if r else None), files


def draft_delete(did: str, keep_server: bool = False) -> None:
    """Supprime le brouillon ; sa copie sur le serveur est mise en file pour être supprimée aussi (au prochain passage, même sans réseau maintenant)."""
    with conn() as c:
        r = c.execute("SELECT account, server_ref, server_folder FROM drafts WHERE id=?", (did,)).fetchone()
        if r and r["server_ref"] and not keep_server:
            c.execute("INSERT INTO draft_gone(account, ref, folder) VALUES(?,?,?)", (r["account"], r["server_ref"], r["server_folder"] or ""))
        c.execute("DELETE FROM drafts WHERE id=?", (did,))
        c.execute("DELETE FROM draft_files WHERE draft_id=?", (did,))


def pin_keys() -> set[str]:
    """Conversations épinglées : celles qui ont au moins un message avec drapeau dans la boîte de réception."""
    with conn() as c:
        return {r[0] for r in c.execute("SELECT DISTINCT ckey FROM messages WHERE flagged=1 AND folder='INBOX'")}


def set_verb(mid: str, verb: int) -> None:
    with conn() as c:
        c.execute("UPDATE messages SET verb=? WHERE id=?", (int(verb), mid))


def set_flagged_by_id(mid: str, on: bool) -> None:
    with conn() as c:
        c.execute("UPDATE messages SET flagged=? WHERE id=?", (int(on), mid))


def set_flagged(account: str, folder: str, uid: str, on: bool) -> None:
    set_flagged_by_id(msg_id(account, folder, uid), on)


# ---------- carnet d'adresses ----------
def _norm(email: str) -> str:
    return (email or "").strip().strip("<>").lower()


def contacts_harvest(my_emails: set[str]) -> int:
    """Repère les correspondants dans les mails synchronisés : qui m'écrit, à qui j'écris (nombre et date du dernier échange)."""
    mine = {_norm(e) for e in my_emails}
    stats: dict[str, dict] = {}

    def see(email: str, name: str, field: str, date: str) -> None:
        e = _norm(email)
        if not e or "@" not in e or e in mine:
            return
        st = stats.setdefault(e, {"name": "", "n_to": 0, "n_from": 0, "last": ""})
        st[field] += 1
        if name and not st["name"]:
            st["name"] = name.strip()
        if date > st["last"]:
            st["last"] = date

    with conn() as c:  # lecture rapide : le verrou de la base n'est pas gardé pendant le calcul (l'interface ne doit jamais attendre)
        rows = c.execute("SELECT sender, sender_name, to_addrs, cc_addrs, date FROM messages").fetchall()
    for r in rows:
        if _norm(r["sender"]) in mine:
            for a in f"{r['to_addrs'] or ''},{r['cc_addrs'] or ''}".replace(";", ",").split(","):
                see(a, "", "n_to", r["date"] or "")
        else:
            see(r["sender"], r["sender_name"] or "", "n_from", r["date"] or "")
    with conn() as c:
        for e, st in stats.items():
            c.execute(
                "INSERT INTO contacts(email,name,source,n_to,n_from,last_at) VALUES(?,?,'mail',?,?,?) "
                "ON CONFLICT(email) DO UPDATE SET n_to=excluded.n_to, n_from=excluded.n_from, last_at=excluded.last_at, "
                "name=CASE WHEN contacts.name='' THEN excluded.name ELSE contacts.name END",
                (e, st["name"], st["n_to"], st["n_from"], st["last"]))
    return len(stats)


def contacts_import(items: list[tuple[str, str]], source: str) -> int:
    """Contacts venant d'un carnet externe (Exchange…) : le nom du carnet fait foi quand l'entrée n'en avait pas, ou venait des mails."""
    n = 0
    with conn() as c:
        for email, name in items:
            e = _norm(email)
            if not e or "@" not in e:
                continue
            c.execute(
                "INSERT INTO contacts(email,name,source) VALUES(?,?,?) ON CONFLICT(email) DO UPDATE SET "
                "name=CASE WHEN contacts.source IN ('mail') OR contacts.name='' THEN excluded.name ELSE contacts.name END, "
                "source=CASE WHEN contacts.source='mail' THEN excluded.source ELSE contacts.source END, hidden=0",
                (e, (name or "").strip(), source))
            n += 1
    return n


def contact_save(email: str, name: str, source: str = "manual") -> None:
    e = _norm(email)
    with conn() as c:
        c.execute("INSERT INTO contacts(email,name,source) VALUES(?,?,?) ON CONFLICT(email) DO UPDATE SET name=excluded.name, hidden=0",
                  (e, name.strip(), source))


def contact_delete(email: str) -> None:
    """Un contact repéré dans les mails est masqué (il reviendrait sinon à la prochaine collecte) ; un contact ajouté à la main est supprimé."""
    e = _norm(email)
    with conn() as c:
        c.execute("DELETE FROM contacts WHERE email=? AND source='manual'", (e,))
        c.execute("UPDATE contacts SET hidden=1, favorite=0 WHERE email=?", (e,))
        c.execute("DELETE FROM group_members WHERE email=?", (e,))


def contact_favorite(email: str, on: bool) -> None:
    with conn() as c:
        c.execute("UPDATE contacts SET favorite=? WHERE email=?", (int(on), _norm(email)))


def contacts_list(q: str = "", limit: int = 4000) -> list[dict]:
    """Favoris d'abord, puis les correspondants les plus fréquents et récents."""
    q = (q or "").strip().lower()
    sql = "SELECT * FROM contacts WHERE hidden=0"
    args: list = []
    if q:
        sql += " AND (lower(email) LIKE ? OR lower(name) LIKE ?)"
        args += [f"%{q}%", f"%{q}%"]
    sql += " ORDER BY favorite DESC, (n_to*3 + n_from) DESC, last_at DESC LIMIT ?"
    with conn() as c:
        return [dict(r) for r in c.execute(sql, (*args, limit))]


def groups_list() -> list[dict]:
    with conn() as c:
        rows = [dict(r) for r in c.execute("SELECT * FROM groups ORDER BY favorite DESC, lower(name)")]
        for g in rows:
            g["members"] = [r[0] for r in c.execute("SELECT email FROM group_members WHERE group_id=? ORDER BY email", (g["id"],))]
    return rows


def group_save(gid: int | None, name: str) -> int:
    with conn() as c:
        if gid:
            c.execute("UPDATE groups SET name=? WHERE id=?", (name.strip(), gid))
            return gid
        return c.execute("INSERT INTO groups(name) VALUES(?)", (name.strip(),)).lastrowid


def group_delete(gid: int) -> None:
    with conn() as c:
        c.execute("DELETE FROM groups WHERE id=?", (gid,))
        c.execute("DELETE FROM group_members WHERE group_id=?", (gid,))


def group_favorite(gid: int, on: bool) -> None:
    with conn() as c:
        c.execute("UPDATE groups SET favorite=? WHERE id=?", (int(on), gid))


def group_set_member(gid: int, email: str, on: bool) -> None:
    with conn() as c:
        if on:
            c.execute("INSERT OR IGNORE INTO group_members(group_id,email) VALUES(?,?)", (gid, _norm(email)))
        else:
            c.execute("DELETE FROM group_members WHERE group_id=? AND email=?", (gid, _norm(email)))


def group_import(name: str, members: list[tuple[str, str]]) -> bool:
    """Liste de distribution d'un carnet externe -> groupe local (créé une seule fois ; ensuite le groupe est à vous)."""
    with conn() as c:
        if c.execute("SELECT 1 FROM groups WHERE lower(name)=lower(?)", (name.strip(),)).fetchone():
            return False
        gid = c.execute("INSERT INTO groups(name) VALUES(?)", (name.strip(),)).lastrowid
        for email, _ in members:
            c.execute("INSERT OR IGNORE INTO group_members(group_id,email) VALUES(?,?)", (gid, _norm(email)))
    contacts_import(members, "exchange")
    return True


# ---------- file d'envoi ----------
def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def outbox_add(row: dict, files: list | None = None) -> None:
    with conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO outbox(id,account,to_addrs,cc,bcc,subject,html,body,draft_id,reply_to,send_at,status,error,created,followup_days,source,api_key,importance) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,'pending','',?,?,?,?,?)",
            (row["id"], row["account"], row["to"], row["cc"], row.get("bcc", ""), row["subject"], row["html"], row["body"], row.get("draft_id"),
             row.get("reply_to"), row["send_at"], _now_iso(), int(row.get("followup_days") or 0), row.get("source", ""), row.get("api_key", ""),
             int(row.get("importance", 1))))
        c.execute("DELETE FROM outbox_files WHERE outbox_id=?", (row["id"],))
        for name, data, mime in files or []:
            c.execute("INSERT INTO outbox_files(outbox_id,name,mime,data) VALUES(?,?,?,?)", (row["id"], name, mime, data))


def outbox_list() -> list[dict]:
    with conn() as c:
        rows = [dict(r) for r in c.execute("SELECT * FROM outbox ORDER BY send_at")]
        for r in rows:
            r["n_files"] = c.execute("SELECT COUNT(*) FROM outbox_files WHERE outbox_id=?", (r["id"],)).fetchone()[0]
    return rows


def outbox_get(oid: str) -> dict | None:
    with conn() as c:
        r = c.execute("SELECT * FROM outbox WHERE id=?", (oid,)).fetchone()
    return dict(r) if r else None


def outbox_files(oid: str) -> list[tuple[str, bytes, str]]:
    with conn() as c:
        return [(r["name"], bytes(r["data"]), r["mime"]) for r in c.execute(
            "SELECT name,data,mime FROM outbox_files WHERE outbox_id=?", (oid,))]


def outbox_due() -> list[dict]:
    with conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM outbox WHERE status='pending' AND send_at<=? ORDER BY send_at", (_now_iso(),))]


def outbox_claim(oid: str) -> bool:
    """Un seul envoi à la fois pour un même message (statut « en cours »)."""
    with conn() as c:
        return c.execute("UPDATE outbox SET status='sending' WHERE id=? AND status='pending'", (oid,)).rowcount == 1


def outbox_mark(oid: str, status: str, error: str = "", send_at: str | None = None) -> None:
    with conn() as c:
        c.execute("UPDATE outbox SET status=?, error=?, send_at=COALESCE(?, send_at) WHERE id=?", (status, error, send_at, oid))


UNCERTAIN_NOTE = "Envoi interrompu : l'application s'est arrêtée pendant l'envoi. Vérifiez dans le dossier « Envoyés » si le message est parti avant de le renvoyer."


def outbox_recover() -> tuple[int, int]:
    """À l'ouverture de l'appli : un message resté « en cours d'envoi » est d'état incertain (parti ou non, on ne peut pas le savoir) : on ne le
    renvoie JAMAIS tout seul, on le signale. Un message déjà noté « parti » est simplement retiré de la file. Retourne (incertains, nettoyés)."""
    with conn() as c:
        done = c.execute("SELECT id FROM outbox WHERE status='sent'").fetchall()
        for r in done:
            c.execute("DELETE FROM outbox_files WHERE outbox_id=?", (r["id"],))
        c.execute("DELETE FROM outbox WHERE status='sent'")
        n = c.execute("UPDATE outbox SET status='uncertain', error=? WHERE status='sending'", (UNCERTAIN_NOTE,)).rowcount
    return n, len(done)


def outbox_delete(oid: str) -> None:
    with conn() as c:
        c.execute("DELETE FROM outbox WHERE id=?", (oid,))
        c.execute("DELETE FROM outbox_files WHERE outbox_id=?", (oid,))


def outbox_count() -> int:
    with conn() as c:
        return c.execute("SELECT COUNT(*) FROM outbox").fetchone()[0]


def followup_add(account: str, to: str, subject: str, due_at: str) -> None:
    with conn() as c:
        c.execute("INSERT INTO followups(account,to_addrs,subject,sent_at,due_at) VALUES(?,?,?,?,?)",
                  (account, to, subject, _now_iso(), due_at))


# ---------- messages reportés ----------
def snooze_set(ckey: str, account: str, until_iso: str) -> None:
    with conn() as c:
        c.execute("INSERT OR REPLACE INTO snoozes(ckey,account,until,status) VALUES(?,?,?,'wait')", (ckey, account, until_iso))


def snooze_clear(ckey: str) -> None:
    with conn() as c:
        c.execute("DELETE FROM snoozes WHERE ckey=?", (ckey,))


def snooze_keys(status: str = "wait") -> set[str]:
    with conn() as c:
        return {r[0] for r in c.execute("SELECT ckey FROM snoozes WHERE status=?", (status,))}


def snooze_wake() -> list[str]:
    """Les reports arrivés à échéance repassent « à revoir » ; retourne leurs conversations."""
    with conn() as c:
        keys = [r[0] for r in c.execute("SELECT ckey FROM snoozes WHERE status='wait' AND until<=?", (_now_iso(),))]
        for k in keys:
            c.execute("UPDATE snoozes SET status='woke' WHERE ckey=?", (k,))
    return keys


def snoozes_list(status: str = "wait") -> list[dict]:
    with conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM snoozes WHERE status=? ORDER BY until", (status,))]


# ---------- relances ----------
def followups_refresh() -> int:
    """Relances échues : sans réponse du destinataire depuis l'envoi -> « à relancer » ; avec réponse -> terminée."""
    now = _now_iso()
    with conn() as c:
        rows = [dict(r) for r in c.execute("SELECT * FROM followups WHERE status IN ('wait','due') AND due_at<=?", (now,))]
        for f in rows:
            wanted = {a.strip().lower() for a in (f["to_addrs"] or "").replace(";", ",").split(",") if a.strip()}
            replied = any((r[0] or "").lower() in wanted for r in c.execute(
                "SELECT sender FROM messages WHERE folder='INBOX' AND date>?", (f["sent_at"],)))
            c.execute("UPDATE followups SET status=? WHERE id=?", ("done" if replied else "due", f["id"]))
        return c.execute("SELECT COUNT(*) FROM followups WHERE status='due'").fetchone()[0]


def followups_due() -> list[dict]:
    with conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM followups WHERE status='due' ORDER BY due_at")]


def followup_set(fid: int, status: str, due_at: str | None = None) -> None:
    with conn() as c:
        c.execute("UPDATE followups SET status=?, due_at=COALESCE(?, due_at) WHERE id=?", (status, due_at, fid))


# ---------- tâches ----------
def task_add(text: str, due: str = "", mid: str = "") -> int:
    with conn() as c:
        return c.execute("INSERT INTO tasks(text,due,mid,created) VALUES(?,?,?,?)", (text.strip(), due, mid, _now_iso())).lastrowid


def tasks_list(include_done: bool = False) -> list[dict]:
    with conn() as c:
        sql = "SELECT * FROM tasks" + ("" if include_done else " WHERE done=0") + " ORDER BY done, (due='') , due, created"
        return [dict(r) for r in c.execute(sql)]


def task_done(tid: int, on: bool) -> None:
    with conn() as c:
        c.execute("UPDATE tasks SET done=? WHERE id=?", (int(on), tid))


def task_delete(tid: int) -> None:
    with conn() as c:
        c.execute("DELETE FROM tasks WHERE id=?", (tid,))


def tasks_open_count() -> int:
    with conn() as c:
        return c.execute("SELECT COUNT(*) FROM tasks WHERE done=0").fetchone()[0]


# ---------- API et serveur MCP ----------
def api_keys_list() -> list[dict]:
    with conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM api_keys ORDER BY created DESC")]


def api_key_by_hash(h: str) -> dict | None:
    with conn() as c:
        r = c.execute("SELECT * FROM api_keys WHERE hash=?", (h,)).fetchone()
    return dict(r) if r else None


def api_key_save(row: dict) -> None:
    cols = ["id", "name", "prefix", "hash", "scopes", "channels", "accounts", "folders", "send_mode", "daily_send_cap", "ai_daily_cap",
            "allowed_domains", "expires", "created"]
    with conn() as c:
        c.execute(f"INSERT OR REPLACE INTO api_keys({','.join(cols)}) VALUES({','.join('?' * len(cols))})", [row[k] for k in cols])


def api_key_delete(kid: str) -> None:
    with conn() as c:
        c.execute("DELETE FROM api_keys WHERE id=?", (kid,))


def api_key_touch(kid: str) -> None:
    with conn() as c:
        c.execute("UPDATE api_keys SET last_used=? WHERE id=?", (datetime.now().astimezone().isoformat(), kid))


def api_log_add(key_id: str, key_name: str, channel: str, action: str, target: str = "", status: str = "ok", detail: str = "") -> None:
    from datetime import timezone
    with conn() as c:
        c.execute("INSERT INTO api_log(ts,key_id,key_name,channel,action,target,status,detail) VALUES(?,?,?,?,?,?,?,?)",
                  (datetime.now(timezone.utc).isoformat(), key_id, key_name, channel, action, target[:200], status, detail[:300]))
        c.execute("DELETE FROM api_log WHERE id < (SELECT MAX(id) FROM api_log) - 5000")


def api_log_list(limit: int = 100, key_id: str = "") -> list[dict]:
    with conn() as c:
        if key_id:
            rows = c.execute("SELECT * FROM api_log WHERE key_id=? ORDER BY id DESC LIMIT ?", (key_id, limit))
        else:
            rows = c.execute("SELECT * FROM api_log ORDER BY id DESC LIMIT ?", (limit,))
        return [dict(r) for r in rows]


def api_log_count(key_id: str, action: str, since_iso: str, exact: bool = False) -> int:
    with conn() as c:
        return c.execute("SELECT COUNT(*) FROM api_log WHERE key_id=? AND action " + ("=?" if exact else "LIKE ?") + " AND status='ok' AND ts>=?",
                         (key_id, action if exact else action + "%", since_iso)).fetchone()[0]


def api_idem_claim(key_id: str, idem: str) -> tuple[str, str]:
    """Réserve une clé d'idempotence : ('new', '') si elle est à nous, ('done', réponse) si déjà traitée, ('busy', '') si une requête identique est en cours."""
    with conn() as c:
        if c.execute("INSERT OR IGNORE INTO api_idem(key_id,idem,response,ts) VALUES(?,?,NULL,?)", (key_id, idem, datetime.now().isoformat())).rowcount:
            return "new", ""
        r = c.execute("SELECT response FROM api_idem WHERE key_id=? AND idem=?", (key_id, idem)).fetchone()
    return ("done", r[0]) if r and r[0] else ("busy", "")


def api_idem_release(key_id: str, idem: str) -> None:
    with conn() as c:
        c.execute("DELETE FROM api_idem WHERE key_id=? AND idem=? AND response IS NULL", (key_id, idem))


def api_idem_set(key_id: str, idem: str, response: str) -> None:
    with conn() as c:
        c.execute("INSERT OR REPLACE INTO api_idem(key_id,idem,response,ts) VALUES(?,?,?,?)", (key_id, idem, response, datetime.now().isoformat()))
        c.execute("DELETE FROM api_idem WHERE ts < ?", ((datetime.now().replace(microsecond=0) - timedelta(days=3)).isoformat(),))
