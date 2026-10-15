"""Règles de tri en langage naturel : « les mails de Gestis sans question vont dans Archive ».

L'IA ne sert qu'une fois, à interpréter la phrase en une règle structurée que l'on vous montre avant de l'enregistrer. Ensuite la règle
s'applique sans IA, aux nouveaux mails seulement. Actions possibles : marquer comme lu, déplacer vers un dossier existant, épingler.
Jamais de suppression."""
import json
import logging
import re
from datetime import datetime, timezone

from . import ai, config, db

log = logging.getLogger("rules")
CATEGORIES = {"action", "info", "newsletter", "notification", "personnel"}
CAT_FR = {"action": "à traiter", "info": "info", "newsletter": "newsletter", "notification": "notification", "personnel": "personnel"}
MAX_AGE_DAYS = 2  # une règle ne touche jamais l'arriéré : seulement les mails récents


# ---------- stockage ----------
def list_rules() -> list[dict]:
    with db.conn() as c:
        rows = [dict(r) for r in c.execute("SELECT * FROM rules ORDER BY id")]
    for r in rows:
        r["spec"] = json.loads(r["spec"])
    return rows


def save(text: str, spec: dict) -> int:
    with db.conn() as c:
        return c.execute("INSERT INTO rules(text,spec,active,hits,created) VALUES(?,?,1,0,?)",
                         (text.strip(), json.dumps(spec, ensure_ascii=False), datetime.now(timezone.utc).isoformat())).lastrowid


def set_active(rid: int, on: bool) -> None:
    with db.conn() as c:
        c.execute("UPDATE rules SET active=? WHERE id=?", (int(on), rid))


def delete(rid: int) -> None:
    with db.conn() as c:
        c.execute("DELETE FROM rules WHERE id=?", (rid,))


def _hit(rid: int) -> None:
    with db.conn() as c:
        c.execute("UPDATE rules SET hits=hits+1 WHERE id=?", (rid,))


# ---------- interprétation (IA) ----------
def _folder_names() -> list[str]:
    names = {f["name"] for f in db.folders() if f["role"] != "inbox"}
    return sorted(names, key=str.lower)


def interpret(text: str) -> dict:
    folders = _folder_names()
    system = f"""Tu transformes une règle de tri d'e-mails, écrite en français, en une règle structurée. {ai._UNTRUSTED}
Réponds UNIQUEMENT par un objet JSON :
{{"if": {{"from_contains": ["mots ou adresses de l'expéditeur"], "subject_contains": ["mots de l'objet"], "body_contains": ["mots du texte"],
         "category": "" ou "action" ou "info" ou "newsletter" ou "notification" ou "personnel", "has_attachment": true ou false ou null}},
 "then": {{"mark_read": true ou false, "move_to": "nom EXACT d'un dossier de la liste, sinon chaîne vide", "pin": true ou false}},
 "note": "remarque courte si quelque chose n'a pas pu être traduit, sinon chaîne vide"}}
Dossiers existants : {json.dumps(folders, ensure_ascii=False)}.
Catégories : action = demande une action de ‹‹PRENOM›› ; info = à lire ; newsletter = publicité ; notification = message automatique ; personnel = privé.
Ne mets dans "if" que ce que la règle exprime, avec au moins une condition ; prévois au moins une action ; ne supprime jamais un mail.
Si la règle cite un dossier absent de la liste, laisse move_to vide et explique-le dans "note"."""
    raw = ai._ask(system, f"Règle : {text.strip()}", model=config.model("ASK"), max_tokens=500, feature="interpret")
    try:
        d = json.loads(raw[raw.index("{"):raw.rindex("}") + 1])
    except (ValueError, TypeError):
        raise ai.AIError("Je n'ai pas compris cette règle. Reformulez-la plus simplement.")
    lst = lambda v: [str(x).strip().lower() for x in (v or []) if str(x).strip()][:8]
    cond = d.get("if") or {}
    act = d.get("then") or {}
    spec = {
        "if": {"from_contains": lst(cond.get("from_contains")), "subject_contains": lst(cond.get("subject_contains")),
               "body_contains": lst(cond.get("body_contains")),
               "category": cond.get("category") if cond.get("category") in CATEGORIES else "",
               "has_attachment": cond.get("has_attachment") if isinstance(cond.get("has_attachment"), bool) else None},
        "then": {"mark_read": bool(act.get("mark_read")),
                 "move_to": act.get("move_to") if act.get("move_to") in folders else "", "pin": bool(act.get("pin"))},
        "note": str(d.get("note") or "")[:300],
    }
    c = spec["if"]
    if not (c["from_contains"] or c["subject_contains"] or c["body_contains"] or c["category"] or c["has_attachment"] is not None):
        raise ai.AIError("Je n'ai trouvé aucune condition dans cette règle (expéditeur, objet, contenu…). Précisez-la.")
    t = spec["then"]
    if not (t["mark_read"] or t["move_to"] or t["pin"]):
        raise ai.AIError(spec["note"] or "Je n'ai trouvé aucune action (marquer lu, déplacer vers un dossier, épingler). Précisez-la.")
    return spec


def describe(spec: dict) -> str:
    c, t = spec["if"], spec["then"]
    conds = []
    if c["from_contains"]:
        conds.append("l'expéditeur contient " + " ou ".join(f"« {x} »" for x in c["from_contains"]))
    if c["subject_contains"]:
        conds.append("l'objet contient " + " ou ".join(f"« {x} »" for x in c["subject_contains"]))
    if c["body_contains"]:
        conds.append("le texte contient " + " ou ".join(f"« {x} »" for x in c["body_contains"]))
    if c["category"]:
        conds.append(f"l'IA l'a classé « {CAT_FR[c['category']]} »")
    if c["has_attachment"] is not None:
        conds.append("il a une pièce jointe" if c["has_attachment"] else "il n'a pas de pièce jointe")
    acts = []
    if t["mark_read"]:
        acts.append("le marquer comme lu")
    if t["move_to"]:
        acts.append(f"le déplacer vers « {t['move_to']} »")
    if t["pin"]:
        acts.append("l'épingler en haut")
    return "Si " + " ET ".join(conds) + " → " + ", ".join(acts) + "."


# ---------- application ----------
def _has(term: str, text: str) -> bool:
    """Mot court : mot entier (« but » ne doit pas trouver « contribution ») ; mot long : simple inclusion."""
    if len(term) <= 5:
        return re.search(r"(?<![a-z0-9À-ÿ])" + re.escape(term) + r"(?![a-z0-9À-ÿ])", text) is not None
    return term in text


def matches(spec: dict, m: dict) -> bool:
    c = spec["if"]
    sender = f"{m.get('sender') or ''} {m.get('sender_name') or ''}".lower()
    subject = (m.get("subject") or "").lower()
    body = (m.get("body") or "")[:3000].lower()
    if c["from_contains"] and not any(_has(t, sender) for t in c["from_contains"]):
        return False
    if c["subject_contains"] and not any(_has(t, subject) for t in c["subject_contains"]):
        return False
    if c["body_contains"] and not any(_has(t, body) for t in c["body_contains"]):
        return False
    if c["category"] and m.get("category") != c["category"]:
        return False
    if c["has_attachment"] is not None and bool(m.get("has_att")) != c["has_attachment"]:
        return False
    return True


def needs_category(spec: dict) -> bool:
    return bool(spec["if"]["category"])


def preview_count(spec: dict, limit: int = 600) -> int:
    """Combien de mails récents de la boîte de réception cette règle aurait touchés."""
    with db.conn() as c:
        rows = [dict(r) for r in c.execute(
            "SELECT sender,sender_name,subject,body,category,has_att FROM messages WHERE folder='INBOX' ORDER BY date DESC LIMIT ?", (limit,))]
    return sum(1 for m in rows if matches(spec, m))


def _dest_key(account: str, name: str) -> str | None:
    rows = [f for f in db.folders(account) if f["name"] == name]
    return rows[0]["key"] if rows else None


def apply_to(m: dict, phase: str, only: int | None = None, force: bool = False) -> int:
    """Applique à un mail (ligne de la base) les règles actives qui le concernent. phase : « sync » (à l'arrivée) ou « analysis » (après le tri IA).
    Retourne le nombre de règles appliquées."""
    from . import sync  # import tardif : sync importe ce module
    if m["folder"] != "INBOX":
        return 0
    try:
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(m["date"])).total_seconds() / 86400
    except ValueError:
        return 0
    if age > MAX_AGE_DAYS and not force:
        return 0
    done = set((m.get("rules_done") or "").split(","))
    applied = 0
    for r in list_rules():
        rid = str(r["id"])
        if only is not None and r["id"] != only:
            continue
        if not force and (not r["active"] or rid in done):
            continue
        spec = r["spec"]
        if not force and needs_category(spec) != (phase == "analysis"):
            continue
        if not matches(spec, m):
            continue
        done.add(rid)
        t = spec["then"]
        try:
            if t["mark_read"] and not m["is_read"]:
                sync.perform(m, "read")
                m["is_read"] = 1
            if t["pin"]:
                sync.set_pinned(m, True)
            if t["move_to"]:
                dest = _dest_key(m["account"], t["move_to"])
                if dest:
                    sync.perform(m, "move", dest)
            _hit(r["id"])
            applied += 1
        except Exception as e:
            log.warning("règle %s sur %s : %s", r["id"], m.get("id"), e)
    m["rules_done"] = ",".join(sorted(x for x in done if x))
    with db.conn() as c:
        c.execute("UPDATE messages SET rules_done=? WHERE id=?", (m["rules_done"], m["id"]))
    return applied


def apply_existing(rid: int, limit: int = 500) -> int:
    """À la demande de l'utilisateur : applique une règle aux mails déjà présents dans la boîte de réception."""
    from . import queries
    with db.conn() as c:
        ids = [r[0] for r in c.execute("SELECT id FROM messages WHERE folder='INBOX' ORDER BY date DESC LIMIT ?", (limit,))]
    n = 0
    for mid in ids:
        row = queries.get_message(mid)
        if row:
            n += apply_to(row, "analysis", only=rid, force=True)
    return n
