"""Sauvegarde et restauration de vos données propres à l'appli : réglages, boîtes mail, signatures, modèles, contacts, groupes, tâches, règles, modules.

Jamais de mot de passe ni de clé d'API dans le fichier. Vos mails restent sur vos serveurs : ils se resynchronisent.
Sert aussi à installer Velora sur un autre ordinateur sans tout ressaisir."""
import base64
import json
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from . import config, db, modules

KV_PREFIXES = ("prompt:", "signature", "undo_delay", "ai_tone", "notify_urgent", "quick_replies", "ai_intents", "trusted_senders",
               "side_", "cols", "list_sort", "server_search", "folder_order:", "folder_fold", "archive:", "theme", "mail_scale", "thread_order", "default_account", "user_name", "phish_safe",
               "ai_fallback", "ai_exclude", "ai_budget_", "onemin_allowance", "onemin_daily_cap", "credits_alert", "apple_writer", "brief_off", "list_density", "update_auto",
               "vip_senders", "safe_senders", "sender_cat:", "domain_cat:", "drafts_auto:", "modules_repo", "module:", "weather_on", "weather_place", "feed_on", "feed_url", "ai_share:")
SECRET = re.compile(r"(pass|pwd|secret|token|api_?key)", re.I)  # jamais dans le fichier, quelle que soit la clé
SKIP_SUFFIX = (":src",)  # dossier d'origine d'un module sur l'autre machine : n'a aucun sens ici
# Réglages d'une boîte repris d'un fichier : une liste fermée. Surtout pas « tls_insecure » (un fichier ne doit pas pouvoir désactiver la vérification des certificats), ni
# « password » ; les mots de passe se saisissent sur cet ordinateur.
ACCOUNT_KEYS = {"key", "label", "kind", "email", "login", "host", "port", "smtp_host", "smtp_port", "tls", "enabled", "ai"}
_MODULE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
ENV_KEYS = ("AI_ENABLED", "AI_UNREAD_ONLY", "AI_MAX_AGE_DAYS", "AI_BODY_LIMIT", "CONVERSATIONS", "SYNC_LIMIT", "AI_PROVIDER",
            *(f"{p}_{t}" for p in ("MODEL", "MODEL1", "PROVIDER") for t in ("ANALYZE", "BRIEF", "DRAFT", "ASK")))
VERSION = 1


def exportable(key: str) -> bool:
    return key.startswith(KV_PREFIXES) and not key.endswith(SKIP_SUFFIX) and not SECRET.search(key)


def _export_modules() -> list[dict]:
    """Les modules installés, fichiers compris (le code des modules reste dans le fichier de sauvegarde, que vous gardez chez vous)."""
    out = []
    if modules.DIR.is_dir():
        for d in sorted(modules.DIR.iterdir()):
            if not (d.is_dir() and (d / "module.json").is_file()):
                continue
            files = {f.relative_to(d).as_posix(): base64.b64encode(f.read_bytes()).decode("ascii") for f in sorted(d.rglob("*"))
                     if f.is_file() and "__pycache__" not in f.parts and f.suffix != ".pyc"}
            out.append({"id": d.name, "version": _version(d), "files": files})
    return out


def _version(folder: Path) -> str:
    try:
        return str(json.loads((folder / "module.json").read_text(encoding="utf-8")).get("version", "0"))
    except (OSError, ValueError):
        return "0"


def _import_modules(items: list) -> tuple[int, int]:
    """(ajoutés, mis à jour). Un module déjà installé n'est remplacé que par une version plus récente ; des chemins inattendus font refuser le module."""
    added = updated = 0
    for m in items or []:
        mid = str((m or {}).get("id") or "")
        if not _MODULE_ID.match(mid) or not isinstance(m.get("files"), dict) or "module.json" not in m["files"]:
            continue
        tmp = Path(tempfile.mkdtemp(prefix="velora-module-"))
        try:
            root = tmp / mid
            for rel, b64 in m["files"].items():
                parts = Path(rel).parts
                if rel.startswith(("/", "\\")) or ".." in parts or ":" in rel or not parts:
                    raise ValueError(f"Module « {mid} » refusé : chemin inattendu.")
                target = root.joinpath(*parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(base64.b64decode(b64))
            installed = modules.DIR / mid
            if not installed.exists():
                modules.import_module(root)
                added += 1
            elif modules._vtuple(_version(root)) > modules._vtuple(_version(installed)):
                modules.import_module(root, replace=True)
                updated += 1
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    return added, updated


def export_data() -> dict:
    with db.conn() as c:
        kv = {r["k"]: r["v"] for r in c.execute("SELECT k,v FROM kv") if exportable(r["k"]) and r["v"] is not None}
        rows = lambda sql: [dict(r) for r in c.execute(sql)]
        data = {
            "app": "Velora", "version": VERSION, "date": datetime.now(timezone.utc).isoformat(),
            "kv": kv,
            "env": {k: config.env(k) for k in ENV_KEYS if config.env(k)},
            "accounts": [{k: v for k, v in a.items() if k in ACCOUNT_KEYS} for a in config.read_accounts()],
            "modules": _export_modules(),
            "from": config.machine_label(),
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
            f"{n('rules')} règle(s) de tri, {len(data.get('kv') or {})} réglage(s), {n('accounts')} boîte(s) (sans mot de passe), {n('modules')} module(s)")


def import_data(data: dict) -> dict:
    """Fusionne la sauvegarde avec les données actuelles : rien n'est supprimé, ce qui existe déjà est conservé."""
    if data.get("app") not in ("Velora", "Mail IA") or "kv" not in data:  # « Mail IA » : sauvegardes faites avant le changement de nom
        raise ValueError("Ce fichier n'est pas une sauvegarde de Velora.")
    out = {"réglages": 0, "modèles": 0, "contacts": 0, "groupes": 0, "tâches": 0, "règles": 0, "boîtes": 0, "modules": 0, "modules mis à jour": 0}
    for k, v in (data.get("kv") or {}).items():
        if isinstance(v, str) and exportable(k) and db.kv_get(k) != v:
            db.kv_set(k, v)
            out["réglages"] += 1
    env = {k: v for k, v in (data.get("env") or {}).items() if k in ENV_KEYS and v}
    if env:
        config.write_env(env)
    have = {a["key"] for a in config.read_accounts()}
    for a in data.get("accounts") or []:
        if a.get("key") and a["key"] not in have:
            config.save_account({k: v for k, v in a.items() if k in ACCOUNT_KEYS}, "")
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
    out["modules"], out["modules mis à jour"] = _import_modules(data.get("modules"))
    return out


def dumps(data: dict) -> bytes:
    return json.dumps(data, ensure_ascii=False, indent=1).encode("utf-8")
