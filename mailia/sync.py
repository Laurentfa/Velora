"""Synchronisation des boîtes (incrémentale), actions sur les mails, notifications en direct, analyse IA."""
import json
import logging
import re
import threading
import time
from datetime import datetime, timezone

from . import ai, compose, config, db, htmlmail, notify, queries, rules, security
from .textutil import html_to_text
from .config import AI_BATCH, AccountCfg, load_accounts
from .providers.base import FolderInfo
from .providers.ews import EwsProvider
from .providers.imap import ImapProvider

log = logging.getLogger("sync")


def log_failure(what: str, exc: BaseException) -> None:
    """Panne réseau attendue (Wi-Fi coupé, serveur injoignable, délai dépassé) : une seule ligne. Tout le reste garde sa trace complète."""
    if isinstance(exc, (OSError, TimeoutError, ConnectionError)) and not isinstance(exc, (PermissionError, FileNotFoundError)):
        log.warning("%s : réseau indisponible (%s)", what, str(exc)[:120] or type(exc).__name__)
    else:
        log.error("%s", what, exc_info=exc)
ACCOUNTS = load_accounts()
_providers: dict = {}
_locks: dict[str, threading.Lock] = {}
last_error: dict[str, str] = {}  # compte -> dernière erreur de synchro (vide si tout va bien) : affiché dans les paramètres
push_status: dict[str, str] = {}  # compte -> "actif" | "indisponible (…)" : affiché dans les paramètres


def reload() -> None:
    """Relit la configuration (après un changement dans les paramètres)."""
    ACCOUNTS.clear()
    ACCOUNTS.update(load_accounts())
    _providers.clear()
    ai.reset_client()


def test_account(cfg: AccountCfg) -> str:
    """Vérifie les réglages d'une boîte sans l'enregistrer. Retourne un résumé, ou lève une exception explicite."""
    if cfg.kind == "ews":
        inbox = EwsProvider(cfg).account.inbox
        return f"Connexion réussie. Boîte de réception : {inbox.total_count} mails, {inbox.unread_count} non lus."
    p = ImapProvider(cfg)
    with p._box() as mb:
        n = mb.folder.status("INBOX").get("MESSAGES", 0)
    if cfg.smtp_host:
        with p.smtp():
            pass
        return f"Réception et envoi OK. Boîte de réception : {n} mails."
    return f"Réception OK ({n} mails). Aucun serveur d'envoi indiqué : l'envoi sera impossible."


def forget_account(key: str) -> int:
    """Retire une boîte de l'appli : ses mails locaux, dossiers et réglages. Rien n'est supprimé sur le serveur."""
    with db.conn() as c:
        ids = [r["id"] for r in c.execute("SELECT id FROM messages WHERE account=?", (key,))]
        for i in ids:
            c.execute("DELETE FROM fts WHERE id=?", (i,))
        c.execute("DELETE FROM messages WHERE account=?", (key,))
        c.execute("DELETE FROM folders WHERE account=?", (key,))
        c.execute("DELETE FROM kv WHERE k IN (?,?,?)", (f"archive:{key}", f"sent_auto:{key}", f"folders_at:{key}"))
    config.remove_account(key)
    last_error.pop(key, None)
    reload()
    return len(ids)


def provider(key: str):
    if key not in _providers:
        cfg: AccountCfg = ACCOUNTS[key]
        _providers[key] = EwsProvider(cfg) if cfg.kind == "ews" else ImapProvider(cfg)
    return _providers[key]


def _lock(key: str) -> threading.Lock:
    return _locks.setdefault(key, threading.Lock())


# ---------- dossiers ----------
def refresh_folders(key: str) -> int:
    """Relit la liste des dossiers sur le serveur (le choix « synchroniser » est conservé)."""
    try:
        infos = provider(key).list_folders()
    except Exception as e:
        log_failure(f"liste des dossiers {key}", e)
        infos = [FolderInfo("INBOX", "Boîte de réception", "Boîte de réception", "inbox")]
        if db.folders(key):
            return 0
    db.save_folder_list(key, infos)
    db.kv_set(f"folders_at:{key}", datetime.now(timezone.utc).isoformat())
    return len(infos)


def archive_folder(key: str) -> str | None:
    """Dossier d'archivage du compte : réglage de l'utilisateur, sinon dossier détecté « Archive »."""
    chosen = db.kv_get(f"archive:{key}")
    if chosen:
        return chosen
    return next((f["key"] for f in db.folders(key) if f["role"] == "archive"), None)


# ---------- synchro ----------
def _apply(account: str, folder: str, delta) -> None:
    db.delete_messages(account, folder, delta.deleted)  # d'abord les suppressions, puis les ajouts
    arrived = []
    for m in delta.upserts:
        if db.upsert(account, folder, m) and folder == "INBOX" and not delta.reset:
            arrived.append(db.msg_id(account, folder, m.uid))
    for uid, read in delta.read_changes:
        db.set_read(account, folder, uid, read)
    for uid, on in delta.flag_changes:
        db.set_flagged(account, folder, uid, on)
    db.set_folder_state(account, folder, delta.new_state)
    vip_arrivals(arrived)
    for mid in arrived:  # règles de tri : seulement sur les mails qui viennent d'arriver
        row = queries.get_message(mid)
        if row:
            try:
                rules.apply_to(row, "sync")
            except Exception:
                log.exception("règles de tri")


def _backfill_importance(key: str, folder: str) -> None:
    """Une seule fois par dossier : repère les mails d'importance haute ou basse reçus avant que Velora lise cette marque (ensuite, elle est lue à l'arrivée)."""
    flag = f"imp_backfill:{key}|{folder}"
    if db.kv_get(flag):
        return
    try:
        marks = provider(key).importance_marks(folder, config.sync_limit())
    except Exception as e:
        log_failure(f"importance des mails ({key}/{folder})", e)
        return  # on réessaiera au prochain passage
    for level, uids in marks.items():
        if uids:
            db.set_importance(key, folder, uids, level)
    db.kv_set(flag, "1")


def _backfill_threads(key: str, folder: str) -> int:
    """Rattache aux conversations les messages synchronisés avant leur introduction (quelques centaines à la fois)."""
    uids = db.uids_without_conv(key, folder)
    if not uids:
        return 0
    try:
        found = provider(key).threading(folder, uids)
    except Exception as e:
        log_failure(f"fils {key}/{folder}", e)
        return 0
    for uid in uids:
        db.set_conv(key, folder, uid, found.get(uid))
    return len(found)


def sync_account(key: str) -> int:
    cfg = ACCOUNTS[key]
    if not cfg.password:
        return 0
    with _lock(key):
        stale = True
        if (at := db.kv_get(f"folders_at:{key}")):
            stale = (datetime.now(timezone.utc) - datetime.fromisoformat(at)).total_seconds() > 6 * 3600
        if stale or not db.folders(key):
            refresh_folders(key)
        if not db.kv_get(f"sent_auto:{key}"):  # les envoyés sont suivis d'office : sans eux, les conversations sont incomplètes
            sent = [f["key"] for f in db.folders(key) if f["role"] == "sent"]
            if sent:
                db.set_folder_sync(key, {f["key"] for f in db.folders(key) if f["sync"]} | set(sent))
                db.kv_set(f"sent_auto:{key}", "1")
        changed = 0
        for f in db.folders(key):
            if not f["sync"]:
                continue
            known = db.known(key, f["key"])
            delta = provider(key).sync_folder(f["key"], f["state"], known, config.sync_limit())
            _apply(key, f["key"], delta)
            changed += len(delta.upserts) + len(delta.deleted) + len(delta.read_changes) + len(delta.flag_changes)
            changed += _backfill_threads(key, f["key"])
            _backfill_importance(key, f["key"])
    db.kv_set("last_sync", datetime.now(timezone.utc).isoformat())
    return changed


# ---------- brouillons : copie dans le dossier Brouillons du serveur ----------
DRAFT_FORGET_AFTER = 180  # secondes : une copie absente du serveur 3 minutes après la dernière synchro est considérée comme supprimée ailleurs


def folder_role(account: str, key: str) -> str:
    return next((f["role"] or "" for f in db.folders(account) if f["key"] == key), "")


def drafts_folder(account: str) -> str:
    return next((f["key"] for f in db.folders(account) if f["role"] == "drafts"), "")


def _draft_has_content(d: dict) -> bool:
    top, _ = compose.split_user_part(d.get("html") or "")
    return bool(html_to_text(top).strip() or "<img" in top or (d.get("subject") or "").strip() or (d.get("to_addrs") or "").strip())


def _follow_drafts_folder(account: str, folder: str) -> None:
    """Une fois par boîte : le dossier Brouillons devient un dossier suivi, pour qu'on y retrouve ses brouillons (on peut ensuite le décocher)."""
    if db.kv_get(f"drafts_auto:{account}"):
        return
    db.set_folder_sync(account, {f["key"] for f in db.folders(account) if f["sync"]} | {folder})
    db.kv_set(f"drafts_auto:{account}", "1")


def mirror_drafts(max_items: int = 5) -> int:
    """Copie sur le serveur (dossier Brouillons de la boîte) les brouillons modifiés, et supprime les copies des brouillons envoyés ou supprimés.
    Sans réseau, rien ne se perd : tout reste en local et sera repris au passage suivant. Retourne le nombre de copies faites."""
    done = 0
    for g in db.drafts_gone():
        cfg = ACCOUNTS.get(g["account"])
        if not cfg or not cfg.password:
            db.draft_gone_done(g["id"])  # boîte retirée : plus rien à supprimer
            continue
        try:
            provider(g["account"]).delete_draft(g["ref"], g["folder"])
        except Exception as e:
            log_failure(f"suppression de la copie d'un brouillon ({g['account']})", e)
            continue
        db.draft_gone_done(g["id"])
    for d in db.drafts_to_push(max_items):
        cfg = ACCOUNTS.get(d["account"])
        if not cfg or not cfg.password or not _draft_has_content(d):
            continue
        folder = drafts_folder(d["account"])
        if not folder:
            continue  # liste des dossiers pas encore lue : au prochain passage
        _follow_drafts_folder(d["account"], folder)
        row, files = db.draft_load(d["id"])
        if not row:
            continue
        try:
            ref = provider(d["account"]).save_draft({"to": row["to_addrs"], "cc": row["cc"], "bcc": row.get("bcc") or "", "subject": row["subject"], "html": row["html"],
                                                     "importance": 1 if row.get("importance") is None else int(row["importance"])},
                                                    files, d.get("server_ref") or None, folder)
        except Exception as e:
            log_failure(f"copie d'un brouillon ({d['account']})", e)
            continue
        if db.draft_load(d["id"])[0]:  # (le brouillon a pu être supprimé ou envoyé pendant la copie : alors cette copie devient orpheline)
            db.draft_set_server(d["id"], ref, d["updated"], folder)
            done += 1
        else:
            db.draft_gone_add(d["account"], ref, folder)
    forget_vanished_drafts()
    return done


def forget_vanished_drafts() -> int:
    """Un brouillon dont la copie a disparu du serveur (supprimée dans Outlook, ou dans la liste du dossier) n'a plus de raison de rester ici.
    Prudent : seulement si le dossier est suivi, sans modification locale depuis la copie, et si la synchro est passée depuis assez longtemps."""
    from datetime import timedelta
    last = db.kv_get("last_sync")
    if not last:
        return 0
    n = 0
    for d in db.drafts_in_sync():
        folder = next((f for f in db.folders(d["account"]) if f["key"] == d["server_folder"]), None)
        if not folder or not folder["sync"]:
            continue
        try:
            if datetime.fromisoformat(last) < datetime.fromisoformat(d["server_at"]) + timedelta(seconds=DRAFT_FORGET_AFTER):
                continue
        except ValueError:
            continue
        with db.conn() as c:
            there = c.execute("SELECT 1 FROM messages WHERE account=? AND folder=? AND uid=?", (d["account"], d["server_folder"], d["server_ref"])).fetchone()
        if not there:
            db.draft_delete(d["id"], keep_server=True)
            n += 1
    return n


def load_server_draft(m: dict) -> str:
    """Un brouillon du dossier Brouillons qu'on n'a pas écrit ici (ex : commencé dans Outlook) : on le relit et on le range parmi les brouillons locaux
    pour pouvoir le modifier. Retourne l'identifiant du brouillon local (celui qui existait déjà, le cas échéant)."""
    import uuid
    known = db.draft_by_ref(m["account"], m["uid"])
    if known:
        return known["id"]
    data = provider(m["account"]).load_draft(m["uid"], m["folder"])
    did = uuid.uuid4().hex
    db.draft_save({"id": did, "account": m["account"], "is_new": True, "reply_to": None, "to": data["to"], "cc": data["cc"], "bcc": data["bcc"],
                   "subject": data["subject"], "html": data["html"], "importance": data.get("importance", 1)}, data["files"])
    row, _ = db.draft_load(did)
    db.draft_set_server(did, m["uid"], row["updated"], m["folder"])  # la copie du serveur est déjà à jour : pas de recopie tant qu'on ne modifie rien
    return did


# ---------- recherche sur le serveur ----------
def server_search_on() -> bool:
    return db.kv_get("server_search") != "0"


def clean_query(text: str) -> str:
    """Les mots cherchés, sans les signes (guillemets, deux-points, parenthèses…) que le serveur lirait comme des opérateurs de recherche."""
    return " ".join(re.findall(r"[\w'@.\-]+", text or ""))


def search_scope(account: str, folder: str, everywhere: bool = False) -> list[str]:
    """Dossiers à interroger : celui qu'on regarde et ses sous-dossiers (comme Apple Mail), ou tous sauf corbeille, indésirables et brouillons."""
    rows = db.folders(account)
    skip = {"trash", "junk", "drafts"}
    if everywhere:
        return [f["key"] for f in rows if f["role"] not in skip][:40]
    cur = next((f for f in rows if f["key"] == (folder or "INBOX")), None)
    if cur is None:
        return [folder or "INBOX"]
    prefixes = (cur["path"] + "/", cur["key"] + "/", cur["key"] + ".")
    sub = [f["key"] for f in rows if f["key"] != cur["key"] and f["role"] not in skip and (f["path"].startswith(prefixes) or f["key"].startswith(prefixes))]
    return ([cur["key"]] + sub)[:40]


def search_remote(text: str, accounts: list[str], folder: str = "INBOX", everywhere: bool = False, per_folder: int = 100, cap: int = 300) -> set[str]:
    """Cherche sur les serveurs des boîtes (en plus de la copie locale) et copie ici les mails trouvés qu'on n'avait pas, sans leur texte :
    il est chargé à l'ouverture, comme pour tout mail synchronisé sans contenu. Retourne les identifiants de tous les mails trouvés.
    Sans réseau ou en cas d'erreur d'un serveur, on garde simplement la recherche locale."""
    text = clean_query(text)
    ids: set[str] = set()
    if len(text) < 3:
        return ids
    for key in accounts:
        cfg = ACCOUNTS.get(key)
        if not cfg or not cfg.password:
            continue
        try:
            prov = provider(key)
            if not hasattr(prov, "search"):
                continue
            found = prov.search(text, search_scope(key, folder, everywhere), per_folder)
        except Exception as e:
            log_failure(f"recherche sur le serveur ({key})", e)
            continue
        found = sorted(found, key=lambda fm: fm[1].date, reverse=True)[:cap]
        known: dict[str, dict] = {}
        added: set[str] = set()
        for fkey, m in found:
            if fkey not in known:
                known[fkey] = db.known(key, fkey)
            if m.uid not in known[fkey]:  # déjà là : surtout ne pas l'écraser par une version sans texte
                db.upsert(key, fkey, m)
                added.add(fkey)
            ids.add(db.msg_id(key, fkey, m.uid))
        for fkey in added:
            _backfill_threads(key, fkey)
    return ids


def _notify_if_urgent(mid: str, d: dict) -> None:
    """Notification pour un mail urgent tout juste arrivé (pas pour l'arriéré analysé au premier lancement, ni pour un mail suspect)."""
    try:
        if d.get("priority") != 3 or not notify.enabled():
            return
        m = queries.get_message(mid)
        if not m or m["folder"] != "INBOX" or m["is_read"]:
            return
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(m["date"])).total_seconds()
        if age > 6 * 3600 or security.quick_row(m):
            return
        notify.once(f"urgent:{mid}", f"Mail urgent · {m['sender_name'] or m['sender']}", f"{m['subject']} — {d.get('summary', '')}")
    except Exception:
        log.exception("notification")


def analyze_pending() -> int:
    if not config.ai_enabled():
        return 0
    if db.kv_get("requeue_bodyless_v1") != "1":  # une seule fois : les résumés faits sans le texte du message sont refaits
        n = queries.requeue_bodyless_summaries()
        db.kv_set("requeue_bodyless_v1", "1")
        if n:
            log.info("%d résumé(s) fait(s) sans le texte du message remis en file d'analyse", n)
    ai_accounts = [k for k, c in ACCOUNTS.items() if c.ai_enabled]
    if not ai_accounts:
        return 0
    marks = ",".join("?" * len(ai_accounts))
    cond, cargs = queries.ai_pending_clause()
    with db.conn() as c:
        rows = c.execute(
            f"SELECT id FROM messages WHERE {cond} AND account IN ({marks}) ORDER BY date DESC LIMIT ?",
            (*cargs, *ai_accounts, AI_BATCH)).fetchall()
    done = 0
    for r in rows:
        try:
            row = queries.get_message(r["id"])
            if row and needs_details(row):  # un mail reçu par la synchro incrémentale peut n'avoir encore ni corps ni destinataires
                try:
                    ensure_details(row)
                except Exception:
                    log.warning("analyse %s : contenu non chargé, reportée", r["id"], exc_info=True)
                    continue  # surtout pas d'analyse sur un mail vide : le résumé serait faux
            d = ai.analyze_and_store(r["id"])
            done += 1
            row = queries.get_message(r["id"])
            if row:
                rules.apply_to(row, "analysis")  # les règles qui s'appuient sur le tri de l'IA
                if queries.get_message(r["id"]):
                    _notify_if_urgent(r["id"], d)
        except ai.AIError as e:
            log.warning("analyse %s : %s", r["id"], e)
            break  # clé absente / quota : inutile d'insister ce cycle
    return done


_last_run: dict[str, float] = {}


def run_cycle(force: bool = False) -> dict:
    """Synchronise les boîtes dont l'intervalle est écoulé (toutes si `force`), puis analyse les nouveaux mails."""
    report = {"synced": {}, "analyzed": 0, "errors": {}}
    now = time.monotonic()
    for key, cfg in ACCOUNTS.items():
        if not force and now - _last_run.get(key, -1e9) < config.SYNC_INTERVAL[cfg.kind]:
            continue
        _last_run[key] = now
        try:
            report["synced"][key] = sync_account(key)
            last_error.pop(key, None)
        except Exception as e:  # une boîte en panne ne doit pas bloquer l'autre
            if last_error.get(key) != str(e):  # on ne journalise qu'une fois la même panne (le cycle revient toutes les 20 s)
                log.warning("synchro %s : %s", key, e)
            last_error[key] = str(e)
            report["errors"][key] = str(e)[:200]
    try:
        _contacts_upkeep(any(report["synced"].values()))
    except Exception:
        log.exception("carnet d'adresses")
    report["analyzed"] = analyze_pending()
    return report


def _contacts_upkeep(changed: bool) -> None:
    """Carnet d'adresses : relevé des correspondants quand de nouveaux mails arrivent, contacts Exchange toutes les 6 heures."""
    if changed or not db.kv_get("contacts_at"):
        db.contacts_harvest({c.email for c in ACCOUNTS.values()})
        db.kv_set("contacts_at", datetime.now(timezone.utc).isoformat())
    at = db.kv_get("contacts_exchange_at")
    if not at or (datetime.now(timezone.utc) - datetime.fromisoformat(at)).total_seconds() > 6 * 3600:
        refresh_contacts(with_exchange=True)
        db.kv_set("contacts_exchange_at", datetime.now(timezone.utc).isoformat())


# ---------- actions sur un message (répercutées sur le serveur) ----------
def perform(m: dict, kind: str, dest: str | None = None) -> None:
    """kind : read | unread | delete | archive | move. Les mails supprimés vont à la corbeille du serveur."""
    key, folder, uid = m["account"], m["folder"], m["uid"]
    p = provider(key)
    if kind in ("read", "unread"):
        p.set_read(folder, uid, kind == "read")
        db.set_read_by_id(m["id"], kind == "read")
    elif kind == "delete":
        p.delete(folder, uid)
        db.delete_by_id(m["id"])
    elif kind in ("archive", "move"):
        dest = archive_folder(key) if kind == "archive" else dest
        if not dest:
            raise RuntimeError("Aucun dossier d'archivage défini : choisissez-le dans Paramètres, ou utilisez « Déplacer ».")
        if dest == folder:
            raise RuntimeError("Le message est déjà dans ce dossier.")
        p.move(folder, uid, dest)
        db.delete_by_id(m["id"])  # il réapparaît tout seul s'il atterrit dans un dossier synchronisé
    else:
        raise ValueError(kind)


def _dry() -> bool:
    return config.env("MAILIA_DRY_RUN") == "1"


def respond_meeting(m: dict, action: str) -> None:
    """Répond à l'organisateur (par la boîte Exchange qui a reçu l'invitation) ; avec Google Agenda choisi, ajoute aussi le rendez-vous
    à Google si l'invitation est acceptée ou « peut-être »."""
    if _dry():
        log.warning("RÉPONSE À INVITATION SIMULÉE : %s %s", action, m["subject"])
        return
    provider(m["account"]).respond_meeting(m["folder"], m["uid"], action)
    if action != "decline" and config.calendar_backend() == "google":
        info = json.loads(m.get("meeting") or "{}")
        if info.get("start") and info.get("end"):
            from . import gcal
            gcal.create_event(m["subject"], datetime.fromisoformat(info["start"]), datetime.fromisoformat(info["end"]),
                              info.get("location") or "", "Invitation reçue par e-mail", tentative=(action == "tentative"))


def create_event(account: str, title: str, start, end, location: str = "", body: str = "", backend: str = "") -> None:
    if _dry():
        log.warning("ÉVÉNEMENT SIMULÉ : %s %s -> %s", title, start, end)
        return
    if (backend or config.calendar_backend()) == "google":
        from . import gcal
        gcal.create_event(title, start, end, location, body)
        return
    provider(account).create_event(title, start, end, location, body)


def exchange_rules(account: str) -> list[dict]:
    """Règles de boîte de réception définies sur le serveur Exchange (lecture seule)."""
    return provider(account).rules_get()


def oof_get(account: str) -> dict:
    return provider(account).oof_get()


def oof_set(account: str, **kw) -> None:
    if _dry():
        log.warning("ABSENCE SIMULÉE : %s", kw.get("state"))
        return
    provider(account).oof_set(**kw)


def refresh_contacts(with_exchange: bool = False) -> dict:
    """Met à jour le carnet : correspondants repérés dans les mails, puis (sur demande) contacts Exchange."""
    out = {"mail": db.contacts_harvest({c.email for c in ACCOUNTS.values()}), "exchange": 0, "groups": 0, "errors": []}
    if with_exchange:
        for key, cfg in ACCOUNTS.items():
            if cfg.kind != "ews" or not cfg.password:
                continue
            try:
                people, lists = provider(key).contacts()
                out["exchange"] += db.contacts_import(people, "exchange")
                out["groups"] += sum(db.group_import(name, members) for name, members in lists)
            except Exception as e:
                out["errors"].append(f"{cfg.label} : {e}")
    return out


def directory_search(query: str) -> list[tuple[str, str]]:
    """Cherche dans l'annuaire d'entreprise des boîtes Exchange actives."""
    found: list[tuple[str, str]] = []
    for key, cfg in ACCOUNTS.items():
        if cfg.kind == "ews" and cfg.password:
            found += provider(key).directory(query)
    return list({e.lower(): (e, n) for e, n in found}.values())


def set_pinned(m: dict, on: bool) -> None:
    """Épingler = poser un drapeau sur le dernier message de la conversation (boîte de réception) ; désépingler = l'ôter
    de tous ceux qui en ont un. Le serveur d'abord : en cas d'échec rien ne change en local."""
    rows = [x for x in db.thread_rows(m["ckey"]) if x["folder"] == "INBOX"] or [m]
    targets = [x for x in rows if x["flagged"]] if not on else [max(rows, key=lambda x: x["date"])]
    for x in targets:
        provider(x["account"]).set_flag(x["folder"], x["uid"], on)
        db.set_flagged_by_id(x["id"], on)


_empty_checked: set[str] = set()  # messages réellement vides côté serveur : inutile de les redemander à chaque ouverture


def needs_details(m: dict) -> bool:
    if m.get("att_json") is None or m.get("body_html") is None or m.get("unsub") is None or m.get("auth") is None \
            or (m.get("bcc_addrs") is None and m.get("folder") != "INBOX"):  # une copie cachée ne se voit que sur les mails envoyés
        return True
    return not (m.get("body") or m.get("body_html")) and m["id"] not in _empty_checked


def ensure_details(m: dict) -> dict:
    """Charge copie, pièces jointes et contenu d'un message enregistré de façon incomplète (ancien, ou reçu sans corps)."""
    if not needs_details(m):
        return m
    d = provider(m["account"]).details(m["folder"], m["uid"])
    db.set_details(m["id"], d["to"], d["cc"], d["attachments"], d.get("html", ""), d.get("unsub", ""), d.get("bcc", ""),
                   json.dumps(d.get("auth") or {}))
    if not m.get("body"):
        text = d.get("text") or html_to_text(d.get("html", ""))
        if text.strip():
            db.set_body(m["id"], text)
            with db.conn() as c:  # analysé avant que le texte n'arrive : le résumé était faux, l'IA le refait avec le vrai contenu
                c.execute("UPDATE messages SET ai_at=NULL WHERE id=? AND ai_at IS NOT NULL", (m["id"],))
        else:
            _empty_checked.add(m["id"])
    return queries.get_message(m["id"]) or m


def inline_images(m: dict, cids: list[str]) -> dict[str, str]:
    """cid -> URL data: des images incrustées d'un message (lues sur le serveur à la demande)."""
    import base64
    got = provider(m["account"]).inline_images(m["folder"], m["uid"], cids)
    return {cid: f"data:{mime};base64,{base64.b64encode(data).decode()}" for cid, (data, mime) in got.items()}


MAX_ATTACH_BYTES = 25 * 1024 * 1024


def thread_ref(account: str, reply_to: str | None) -> tuple | None:
    """(« reply » ou « forward », dossier, uid) du message d'origine d'une réponse ou d'un transfert, pour que Exchange le range dans sa conversation."""
    if not reply_to:
        return None
    kind = "forward" if reply_to.startswith("fwd:") else "reply"
    m = queries.get_message(reply_to.removeprefix("fwd:"))
    if not m or m["account"] != account:
        return None
    return kind, m["folder"], m["uid"]


def send_mail(account: str, to: str, subject: str, body: str, cc: str = "", attachments: list | None = None,
              html: str | None = None, bcc: str = "", reply_to: str | None = None, importance: int = 1) -> None:
    """Envoie un message (réponse ou nouveau) avec contrôles élémentaires avant d'appeler le serveur. `importance` : 0 basse, 1 normale, 2 haute."""
    addrs = [a.strip() for a in (to + "," + cc + "," + bcc).replace(";", ",").split(",") if a.strip()]
    bad = [a for a in addrs if not re.fullmatch(r"[^@\s<>]+@[^@\s<>]+\.[^@\s<>]+", a)]
    if not [a for a in to.replace(";", ",").split(",") if a.strip()]:
        raise ValueError("Indiquez au moins un destinataire.")
    if bad:
        raise ValueError(f"Adresse invalide : {bad[0]}")
    inline: list = []
    if html:  # message mis en forme : HTML nettoyé + images incorporées + version texte de secours
        html, inline = htmlmail.extract_inline(compose.embed_signature_images(htmlmail.sanitize_outgoing(htmlmail.autolink(html)), account))
        html = htmlmail.wrap_email(html)
        body = html_to_text(html)
    size = sum(len(d) for _, d, _ in attachments or []) + sum(len(i[1]) for i in inline)
    if size > MAX_ATTACH_BYTES:
        raise ValueError("Pièces jointes et images trop volumineuses (25 Mo au maximum au total).")
    if config.env("MAILIA_DRY_RUN") == "1":  # essais : rien ne part, on garde seulement une trace
        log.warning("ENVOI SIMULÉ (MAILIA_DRY_RUN) : %s -> %s « %s »", account, to, subject)
        sent = json.loads(db.kv_get("dryrun_sent") or "[]")
        sent.append({"account": account, "to": to, "cc": cc, "bcc": bcc, "subject": subject, "files": [a[0] for a in attachments or []], "importance": importance})
        db.kv_set("dryrun_sent", json.dumps(sent[-50:]))
        return
    # l'objet part tel quel, espaces finaux compris : Exchange range le message dans la conversation de l'original d'après l'objet exact
    # (« Document mutuelle␠ » et « TR: Document mutuelle » seraient deux conversations différentes)
    provider(account).send(to, subject if subject.strip() else "(sans objet)", body, cc, attachments, html, inline, bcc, ref=thread_ref(account, reply_to),
                           importance=importance)


def digest_upkeep() -> None:
    """Résumé quotidien (si activé) : une notification à l'heure choisie, une fois par jour."""
    if db.kv_get("digest_on") != "1":
        return
    now = datetime.now()
    try:
        hour = int(db.kv_get("digest_hour") or 8)
    except ValueError:
        hour = 8
    if now.hour < hour or (db.kv_get("digest_weekdays") == "1" and now.weekday() >= 5):
        return
    today = now.strftime("%Y-%m-%d")
    if db.kv_get("digest_last") == today:
        return
    db.kv_set("digest_last", today)  # d'abord : même en cas d'erreur, pas de répétition toutes les 30 s
    try:
        title, text = queries.digest_text(db.kv_get("digest_ai") == "1")
        notify.send(title, text)
    except Exception:
        log.exception("résumé quotidien")


def vip_arrivals(ids: list[str]) -> None:
    """Expéditeurs prioritaires : le mail passe en urgent tout de suite (avant même l'analyse) et déclenche une notification."""
    from . import aipolicy
    for mid in ids:
        m = queries.get_message(mid)
        if not m or not aipolicy.is_vip(m["sender"]):
            continue
        with db.conn() as c:
            c.execute("UPDATE messages SET priority=3 WHERE id=?", (mid,))
        try:
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(m["date"])).total_seconds()
            if not m["is_read"] and age < 6 * 3600 and not security.quick_row(m):
                notify.once(f"vip:{mid}", f"Prioritaire · {m['sender_name'] or m['sender']}", m["subject"] or "")
        except Exception:
            log.exception("notification prioritaire")


def reminders_upkeep() -> dict:
    """Réveille les messages reportés (ils redeviennent non lus, « à revoir »), met à jour les relances sans réponse."""
    woke = db.snooze_wake()
    for ck in woke:
        rows = [r for r in db.thread_rows(ck) if r["folder"] == "INBOX"]
        if rows:
            last = rows[-1]
            notify.once(f"woke:{ck}:{datetime.now(timezone.utc):%Y%m%d%H%M}", "Message à revoir", f"{last['sender_name'] or last['sender']} — {last['subject']}")
            try:
                perform(last, "unread")
            except Exception as e:
                log.warning("réveil %s : %s", ck, e)
                db.set_read_by_id(last["id"], False)
    digest_upkeep()
    due = db.followups_refresh()
    for f in db.followups_due():
        notify.once(f"followup:{f['id']}", "À relancer : pas de réponse", f"{f['to_addrs']} — {f['subject']}")
    return {"woke": len(woke), "followups": due}


def mark_answered(reply_to: str | None) -> None:
    """Après un envoi réussi : « répondu » ou « transféré » sur le message d'origine (marque locale, et sur le serveur Exchange pour qu'Outlook l'affiche).
    Ne fait jamais échouer l'envoi."""
    if not reply_to or _dry():
        return
    try:
        verb = 2 if reply_to.startswith("fwd:") else 1
        m = queries.get_message(reply_to.removeprefix("fwd:"))
        if not m:
            return
        db.set_verb(m["id"], verb)
        mark = getattr(provider(m["account"]), "mark_verb", None)
        if mark:
            mark(m["folder"], m["uid"], verb)
    except Exception:
        log.warning("marque « répondu / transféré » non posée", exc_info=True)


def send_due() -> int:
    """Envoie les messages de la file dont l'heure est venue (fin du délai d'annulation ou envoi programmé)."""
    from datetime import datetime, timedelta, timezone
    n = 0
    for row in db.outbox_due():
        if not db.outbox_claim(row["id"]):
            continue
        try:
            send_mail(row["account"], row["to_addrs"], row["subject"], row["body"], row["cc"], db.outbox_files(row["id"]), row["html"], row["bcc"] or "",
                      reply_to=row["reply_to"], importance=1 if row.get("importance") is None else int(row["importance"]))
        except Exception as e:
            log.warning("envoi %s : %s", row["id"], e)
            db.outbox_mark(row["id"], "error", str(e)[:300])
            continue
        mark_answered(row["reply_to"])
        db.outbox_mark(row["id"], "sent")  # parti : noté tout de suite, pour qu'un arrêt brutal juste après ne le fasse jamais renvoyer
        if row["followup_days"]:
            due = (datetime.now(timezone.utc) + timedelta(days=row["followup_days"])).isoformat()
            db.followup_add(row["account"], row["to_addrs"], row["subject"], due)
        db.outbox_delete(row["id"])
        n += 1
    return n


def perform_many(msgs: list[dict], kind: str, dest: str | None = None) -> None:
    """Applique une action à plusieurs messages (une conversation). Un message déjà rangé à destination est ignoré ;
    une erreur sur l'un n'empêche pas les autres, et la première est levée à la fin."""
    first: Exception | None = None
    for m in msgs:
        if kind in ("archive", "move") and m["folder"] == (archive_folder(m["account"]) if kind == "archive" else dest):
            continue
        try:
            perform(m, kind, dest)
        except Exception as e:
            first = first or e
    if first:
        raise first


def fetch_attachment(m: dict, att: dict) -> tuple[bytes, str]:
    return provider(m["account"]).attachment(m["folder"], m["uid"], att["id"])


# ---------- notifications en direct ----------
def _push_loop(key: str) -> None:
    backoff = 30
    while True:
        cfg = ACCOUNTS.get(key)
        if not cfg or not cfg.password:
            push_status[key] = "non configuré"
            time.sleep(30)
            continue
        keys = [f["key"] for f in db.folders(key) if f["sync"]] or ["INBOX"]
        try:
            push_status[key] = "actif"
            if provider(key).wait_for_changes(keys if cfg.kind == "ews" else ["INBOX"], 280):
                sync_account(key)
            backoff = 30
        except Exception as e:
            push_status[key] = f"indisponible ({str(e)[:70]}) — synchro périodique seulement"
            log.warning("notifications %s : %s", key, e)
            time.sleep(backoff)
            backoff = min(backoff * 2, 900)


def start_push() -> None:
    """Un fil d'écoute par boîte : un nouveau mail déclenche une synchro immédiate (sans attendre l'intervalle)."""
    if not config.PUSH:
        for key in ACCOUNTS:
            push_status[key] = "interrogation régulière"
        return
    for key in list(ACCOUNTS):
        threading.Thread(target=_push_loop, args=(key,), daemon=True, name=f"push-{key}").start()
