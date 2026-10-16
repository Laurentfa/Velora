"""Sauvegarde et restauration de vos données propres à l'appli : réglages, signatures, modèles, contacts, groupes, tâches, règles.

Jamais de mot de passe ni de clé d'API dans le fichier. Vos mails restent sur vos serveurs : ils se resynchronisent."""
import json
from datetime import datetime, timezone

from . import config, db

KV_PREFIXES = ("prompt:", "signature", "undo_delay", "ai_tone", "notify_urgent", "quick_replies", "ai_intents", "trusted_senders",
               "side_", "cols", "list_sort", "server_search", "folder_order:", "folder_fold", "archive:", "theme", "default_account", "user_name", "phish_safe")
ENV_KEYS = ("AI_ENABLED", "AI_UNREAD_ONLY", "AI_MAX_AGE_DAYS", "CONVERSATIONS", "SYNC_LIMIT", "AI_PROVIDER",
            *(f"{p}_{t}" for p in ("MODEL", "MODEL1", "PROVIDER") for t in ("ANALYZE", "BRIEF", "DRAFT", "ASK")))
VERSION = 1


def export_data() -> dict:
    with db.conn() as c:
        kv = {r["k"]: r["v"] for r in c.execute("SELECT k,v FROM kv") if r["k"].startswith(KV_PREFIXES)}
        rows = lambda sql: [dict(r) for r in c.execute(sql)]
        data = {
            "app": "Velora", "version": VERSION, "date": datetime.now(timezone.utc).isoformat(),
            "kv": kv,
            "env": {k: config.env(k) for k in ENV_KEYS if config.env(k)},
            "accounts": config.read_accounts(),
            "templates": rows("SELECT name,subject,html,to_addrs,cc,bcc FROM templates"),
            "contacts": rows("SELECT email,name,source,favorite,hidden FROM contacts WHERE favorite=1 OR source IN ('manual','annuaire') OR hidden=1"),
            "groups": [{**g, "members": [r[0] for r in c.execute("SELECT email FROM group_members WHERE group_id=?", (g["id"],))]}
                       for g in rows("SELECT id,name,favorite FROM groups")],
            "tasks": rows("SELECT text,due,mid,done FROM tasks"),
            "rules": rows("SELECT text,spec,active FROM rules"),
        }
    return data


def summary(data: dict) -> str:
    n = lambda k: len(data.get(k) or [])
    return (f"{n('templates')} modèle(s), {n('contacts')} contact(s) à retenir, {n('groups')} groupe(s), {n('tasks')} tâche(s), "
            f"{n('rules')} règle(s) de tri, {len(data.get('kv') or {})} réglage(s), {n('accounts')} boîte(s) (sans mot de passe)")


def import_data(data: dict) -> dict:
    """Fusionne la sauvegarde avec les données actuelles : rien n'est supprimé, ce qui existe déjà est conservé."""
    if data.get("app") not in ("Velora", "Mail IA") or "kv" not in data:  # « Mail IA » : sauvegardes faites avant le changement de nom
        raise ValueError("Ce fichier n'est pas une sauvegarde de Velora.")
    out = {"réglages": 0, "modèles": 0, "contacts": 0, "groupes": 0, "tâches": 0, "règles": 0, "boîtes": 0}
    for k, v in (data.get("kv") or {}).items():
        if k.startswith(KV_PREFIXES) and db.kv_get(k) != v:
            db.kv_set(k, v)
            out["réglages"] += 1
    env = {k: v for k, v in (data.get("env") or {}).items() if k in ENV_KEYS and v}
    if env:
        config.write_env(env)
    have = {a["key"] for a in config.read_accounts()}
    for a in data.get("accounts") or []:
        if a.get("key") and a["key"] not in have:
            config.save_account({k: v for k, v in a.items() if k != "password"}, "")
            out["boîtes"] += 1
    have_t = {t["name"].lower() for t in db.templates_list()}
    for t in data.get("templates") or []:
        if t["name"].lower() not in have_t:
            db.template_save(None, t["name"], t.get("subject") or "", t.get("html") or "", t.get("to_addrs") or "", t.get("cc") or "", t.get("bcc") or "")
            out["modèles"] += 1
    for c in data.get("contacts") or []:
        db.contact_save(c["email"], c.get("name") or "", c.get("source") or "manual")
        db.contact_favorite(c["email"], bool(c.get("favorite")))
        if c.get("hidden"):
            with db.conn() as cx:
                cx.execute("UPDATE contacts SET hidden=1 WHERE email=?", (c["email"].lower(),))
        out["contacts"] += 1
    existing = {g["name"].lower(): g for g in db.groups_list()}
    for g in data.get("groups") or []:
        cur = existing.get(g["name"].lower())
        gid = cur["id"] if cur else db.group_save(None, g["name"])
        if not cur:
            out["groupes"] += 1
        if g.get("favorite"):
            db.group_favorite(gid, True)
        for e in g.get("members") or []:
            db.group_set_member(gid, e, True)
    have_task = {t["text"].lower() for t in db.tasks_list(True)}
    for t in data.get("tasks") or []:
        if t["text"].lower() not in have_task:
            tid = db.task_add(t["text"], t.get("due") or "", t.get("mid") or "")
            if t.get("done"):
                db.task_done(tid, True)
            out["tâches"] += 1
    have_r = {r["text"].lower() for r in __import__("mailia.rules", fromlist=["list_rules"]).list_rules()}
    for r in data.get("rules") or []:
        if r["text"].lower() not in have_r:
            with db.conn() as cx:
                cx.execute("INSERT INTO rules(text,spec,active,hits,created) VALUES(?,?,?,0,?)",
                           (r["text"], r["spec"], int(r.get("active", 1)), datetime.now(timezone.utc).isoformat()))
            out["règles"] += 1
    return out


def dumps(data: dict) -> bytes:
    return json.dumps(data, ensure_ascii=False, indent=1).encode("utf-8")
