"""Vue « Règles de tri » : les règles de boîte de réception du serveur Exchange (appliquées même quand l'appli est fermée), qu'on peut lire, tester sur ses mails,
activer, réordonner, modifier, créer et supprimer. Toute écriture passe par un plan montré avant confirmation, une sauvegarde et une relecture de contrôle
(voir server_rules.py). Aperçus, suggestions et activité se calculent sur la base locale : aucune requête de plus vers le serveur."""
import json
import re
from datetime import datetime, timedelta, timezone

from nicegui import background_tasks, run, ui

from mailia import ai, config, db, sync
from mailia import rules as rules_mod
from mailia import server_rules as sr
from mailia.ui_common import log

COND_CHOICES = {"sender_contains": "L'expéditeur contient", "subject_contains": "L'objet contient", "subject_or_body_contains": "L'objet ou le corps contient",
                "body_contains": "Le corps contient", "sent_only_to_me": "Envoyé uniquement à moi", "sent_to_me": "M'est adressé (dans À)",
                "sent_cc_me": "Je suis en copie", "has_attachment": "A une pièce jointe"}


def split_words(text: str) -> list[str]:
    return [w.strip() for w in re.split(r"[;\n]", text or "") if w.strip()]


def local_recent(account: str, days: int = 90, limit: int = 4000) -> list[dict]:
    """Mails récents de la base locale (boîte de réception d'abord), pour les aperçus et les suggestions."""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    with db.conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT id, folder, subject, sender, sender_name, body, has_att, category, date, is_read FROM messages WHERE account=? AND date>=? ORDER BY date DESC LIMIT ?",
            (account, since, limit))]


def activity(account: str, rules: list[dict]) -> dict:
    """{id de règle: nombre de mails arrivés ces 30 jours dans le dossier de destination} ; None si ce dossier n'est pas synchronisé."""
    since = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    synced = {f["key"] for f in db.folders(account) if f["sync"]}
    out = {}
    with db.conn() as c:
        for r in rules:
            fid = next((a.get("folder_id") for a in r["actions"] if a["type"] == "move_to_folder"), None)
            out[r["id"]] = c.execute("SELECT COUNT(*) FROM messages WHERE account=? AND folder=? AND date>=?", (account, fid, since)).fetchone()[0] if fid in synced else None
    return out


def suggestions(account: str, rules: list[dict], limit: int = 6) -> list[dict]:
    """Expéditeurs fréquents de la boîte de réception qu'aucune règle ne couvre encore : [{"sender", "count", "unread", "kind"}]."""
    covered = " ".join(v.lower() for r in rules for c in r["conditions"] if c["type"] in ("sender_contains", "received_from") for v in c["values"])
    with db.conn() as c:
        rows = c.execute("SELECT lower(sender) s, COUNT(*) n, SUM(1-is_read) u, MAX(category) cat FROM messages WHERE account=? AND folder='INBOX' AND sender<>'' "
                         "GROUP BY lower(sender) HAVING n>=5 ORDER BY n DESC LIMIT 60", (account,)).fetchall()
    out = []
    for s, n, u, cat in rows:
        domain = s.split("@")[-1]
        if s in covered or (domain and domain in covered):
            continue
        out.append({"sender": s, "count": n, "unread": u or 0, "kind": cat or ""})
        if len(out) >= limit:
            break
    return out


class ServerRulesMixin:
    srules_acct = ""

    def srules_accounts(self) -> dict:
        return {k: c.label for k, c in sync.ACCOUNTS.items() if c.kind == "ews" and c.password}

    def _srules_client(self, acct: str):
        return sync.provider(acct).rules_client()

    # ---------- la vue ----------
    def server_rules_view(self) -> None:
        accts = self.srules_accounts()
        if not accts:
            ui.label("Cette vue concerne les boîtes Exchange : aucune n'est configurée.").classes("empty")
            return
        if self.srules_acct not in accts:
            self.srules_acct = next(iter(accts))
        with ui.element("div").style("padding:10px 14px;width:100%"):
            ui.label("Règles appliquées par le serveur, même quand Velora est fermé. Chaque changement est montré avant d'être enregistré, et une sauvegarde est faite.").classes("rmeta")
            with ui.row().classes("w-full items-center no-wrap gap-2 q-mt-sm"):
                if len(accts) > 1:
                    ui.select(accts, value=self.srules_acct, on_change=lambda e: (setattr(self, "srules_acct", e.value), self.reload_srules())).props("outlined dense").classes("col")
                else:
                    ui.element("div").classes("col")
                ui.button(icon="refresh", on_click=lambda: self.reload_srules()).props("flat dense round size=sm").tooltip("Relire les règles sur le serveur").mark("srules-refresh")
                ui.button("Nouvelle règle", icon="add", on_click=lambda: self.srules_edit(None)).props("unelevated no-caps dense color=primary").mark("srules-new")
        self._srules_box = ui.column().classes("w-full gap-2").style("padding:0 14px 14px")
        with self._srules_box:
            ui.spinner(size="22px")
        background_tasks.create(self._srules_load())

    def reload_srules(self) -> None:
        box = getattr(self, "_srules_box", None)
        if box is not None:
            box.clear()
            with box:
                ui.spinner(size="22px")
        background_tasks.create(self._srules_load())

    async def _srules_load(self) -> None:
        acct = self.srules_acct

        def fetch():
            cl = self._srules_client(acct)
            folders = cl.folders()
            return folders, cl.inbox_rules(folders)
        try:
            folders, live = await run.io_bound(fetch)
        except Exception as e:  # noqa: BLE001
            log.warning("règles du serveur : %s", e)
            return self._srules_message(f"Lecture impossible : {str(e)[:200]}")
        self._srules = {"acct": acct, "folders": folders, "live": live}
        try:
            act = await run.io_bound(activity, acct, live)
            sug = await run.io_bound(suggestions, acct, live)
            self._srules_render(act, sug)
        except Exception:  # noqa: BLE001 — la vue a pu être quittée entre-temps
            pass

    def _srules_message(self, text: str) -> None:
        try:
            self._srules_box.clear()
            with self._srules_box:
                ui.label(text).classes("text-negative")
        except Exception:  # noqa: BLE001
            pass

    def _srules_render(self, act: dict, sug: list[dict]) -> None:
        st = self._srules
        box = self._srules_box
        box.clear()
        live = st["live"]
        with box:
            if sug:
                with ui.expansion(f"Suggestions ({len(sug)})", icon="lightbulb").classes("w-full").props("dense"):
                    ui.label("Expéditeurs qui reviennent souvent dans votre boîte de réception, sans règle pour l'instant.").classes("rmeta")
                    for s in sug:
                        with ui.row().classes("w-full items-center no-wrap gap-2"):
                            ui.label(f"{s['sender']} · {s['count']} mails" + (f" · {s['unread']} non lus" if s["unread"] else "")).classes("col").style("overflow:hidden;text-overflow:ellipsis")
                            ui.button("Créer la règle", on_click=lambda _, s=s: self.srules_edit(None, prefill={"sender": s["sender"]})).props("flat dense no-caps size=sm color=primary")
            if not live:
                ui.label("Aucune règle sur ce serveur. Créez-en une avec « Nouvelle règle ».").classes("rmeta")
            n = len(live)
            for i, r in enumerate(live):
                with ui.card().classes("w-full").props("flat bordered"):
                    with ui.row().classes("items-center no-wrap gap-2 w-full"):
                        ui.label(str(r["order"])).classes("rmeta").style("min-width:18px")
                        ui.label(r["name"]).style("font-weight:600").classes("col")
                        ui.switch(value=r["enabled"], on_change=lambda e, r=r: self.srules_toggle(r, bool(e.value))).props("dense").mark("srules-toggle").tooltip(
                            "Activer ou désactiver cette règle")
                    ui.label(sr.describe(r)).classes("text-body2")
                    count = act.get(r["id"])
                    if any(a["type"] == "move_to_folder" for a in r["actions"]):
                        if count is None:
                            ui.label("Activité : dossier de destination non synchronisé.").classes("rmeta")
                        elif count == 0:
                            ui.label("Aucun mail arrivé dans ce dossier ces 30 jours : la règle ne sert peut-être plus.").classes("rmeta").style("color:var(--warn,#c77700)")
                        else:
                            ui.label(f"{count} mails arrivés dans ce dossier ces 30 jours.").classes("rmeta")
                    if r.get("not_supported"):
                        with ui.row().classes("items-center no-wrap gap-1"):
                            ui.icon("computer", size="16px").style("color:var(--warn,#c77700)")
                            ui.label("Règle propre au poste : le serveur ne l'applique pas, elle ne fonctionne que dans Outlook sur l'ordinateur où elle est créée.").classes("rmeta").style("color:var(--warn,#c77700)")
                    if not sr.editable(r):
                        ui.label("Cette règle comporte des critères ou des actions que Velora ne sait pas modifier : vous pouvez la renommer, l'activer, la déplacer ou la supprimer.").classes("rmeta")
                    with ui.row().classes("items-center gap-1"):
                        ui.button(icon="arrow_upward", on_click=lambda _, i=i: self.srules_move(i, -1)).props("flat dense round size=sm").tooltip("Monter").set_enabled(i > 0)
                        ui.button(icon="arrow_downward", on_click=lambda _, i=i: self.srules_move(i, 1)).props("flat dense round size=sm").tooltip("Descendre").set_enabled(i < n - 1)
                        ui.button(icon="science", on_click=lambda _, r=r: self.srules_test(r)).props("flat dense round size=sm").tooltip("Tester sur mes mails").mark("srules-test")
                        ui.button(icon="edit", on_click=lambda _, r=r: self.srules_edit(r)).props("flat dense round size=sm").tooltip("Modifier").mark("srules-edit")
                        ui.button(icon="content_copy", on_click=lambda _, r=r: self.srules_duplicate(r)).props("flat dense round size=sm").tooltip("Dupliquer")
                        ui.button(icon="delete_outline", on_click=lambda _, r=r: self.srules_delete(r)).props("flat dense round size=sm color=negative").tooltip("Supprimer").mark("srules-delete")

    # ---------- actions : chacune demande un plan, puis confirmation, sauvegarde, envoi, contrôle ----------
    def _srules_desired(self) -> list[dict]:
        return sr.desired_from(self._srules["live"])

    async def srules_toggle(self, rule: dict, on: bool) -> None:
        if bool(rule["enabled"]) == on:
            return
        desired = self._srules_desired()
        next(d for d in desired if d["id"] == rule["id"])["enabled"] = on
        await self._srules_propose(desired, confirm_needed=False)

    async def srules_move(self, i: int, step: int) -> None:
        desired = self._srules_desired()
        j = i + step
        if 0 <= j < len(desired):
            desired[i], desired[j] = desired[j], desired[i]
            await self._srules_propose(desired, confirm_needed=False)

    async def srules_delete(self, rule: dict) -> None:
        await self._srules_propose([d for d in self._srules_desired() if d["id"] != rule["id"]])

    async def srules_duplicate(self, rule: dict) -> None:
        if not sr.editable(rule):
            return ui.notify("Cette règle comporte des critères que Velora ne sait pas recréer : impossible de la dupliquer.", type="warning")
        desired = self._srules_desired()
        copy = json.loads(json.dumps(next(d for d in desired if d["id"] == rule["id"])))
        copy["id"], copy["name"], copy["enabled"] = None, copy["name"] + " (copie)", False
        desired.append(copy)
        await self._srules_propose(desired)

    async def _srules_propose(self, desired: list[dict], confirm_needed: bool = True) -> bool:
        acct = self.srules_acct
        client = self._srules_client(acct)
        try:
            res = await run.io_bound(sr.make_plan, client, desired)
        except sr.RulesError as e:
            ui.notify(str(e), type="negative", multi_line=True)
            return False
        except Exception as e:  # noqa: BLE001
            ui.notify(f"Impossible de préparer la modification : {e}", type="negative", multi_line=True)
            return False
        if not res["plan_id"]:
            ui.notify("Rien à changer.")
            return True
        if confirm_needed and not await self._srules_confirm(res):
            return False
        try:
            out = await run.io_bound(sr.apply_plan, client, res["plan_id"], True, self.srules_accounts().get(acct, acct))
        except Exception as e:  # noqa: BLE001
            ui.notify(f"Modification refusée : {e}", type="negative", multi_line=True)
            self.reload_srules()
            return False
        ui.notify("Règles enregistrées sur le serveur" + ("" if out["verified"] else " (la relecture diffère légèrement : vérifiez la liste)") + f" · sauvegarde : {out['backup']}",
                  type="positive" if out["verified"] else "warning", multi_line=True)
        self.reload_srules()
        return True

    async def _srules_confirm(self, res: dict) -> bool:
        with self.host, ui.dialog() as dlg, ui.card().style("width:min(640px,94vw)"):
            ui.label("Enregistrer sur le serveur Exchange ?").classes("text-h6")
            for s in res["summary"]:
                ui.label("• " + s["text"]).classes("text-body2")
            for w in res["warnings"]:
                ui.label("⚠ " + w).classes("text-caption").style("color:var(--warn,#c77700)")
            ui.label("Une sauvegarde des règles actuelles est faite avant l'envoi, puis le serveur est relu pour contrôle.").classes("rmeta")
            with ui.row().classes("justify-end w-full gap-2"):
                ui.button("Annuler", on_click=lambda: dlg.submit(False)).props("flat no-caps")
                ui.button("Enregistrer", on_click=lambda: dlg.submit(True)).props("unelevated no-caps color=primary").mark("srules-confirm")
        ok = await dlg
        dlg.delete()
        return bool(ok)

    # ---------- test sur les mails de la base locale ----------
    async def srules_test(self, rule: dict, draft: dict | None = None) -> None:
        r = draft or rule
        rows = await run.io_bound(local_recent, self.srules_acct)
        hits = [m for m in rows if sr.matches_local(r, m)]
        untested = [c["type"] for c in r["conditions"] if c["type"] not in ("sender_contains", "subject_contains", "body_contains", "subject_or_body_contains", "has_attachment")]
        with self.host, ui.dialog() as dlg, ui.card().style("width:min(680px,94vw)"):
            ui.label(f"Test : {len(hits)} mail(s) concerné(s) sur {len(rows)} mails récents (90 jours)").classes("text-h6")
            ui.label(sr.describe(r)).classes("text-body2")
            if untested:
                ui.label("Critères non testables sur la base locale (ignorés) : " + ", ".join(sr.LABEL.get(t, t) for t in untested)).classes("rmeta")
            if not hits:
                ui.label("Aucun mail récent ne correspond.").classes("rmeta")
            for m in hits[:12]:
                with ui.row().classes("w-full no-wrap gap-2"):
                    ui.label((m["date"] or "")[:10]).classes("rmeta").style("min-width:78px")
                    ui.label(f"{m['sender_name'] or m['sender']} — {m['subject'] or ''}").style("overflow:hidden;text-overflow:ellipsis;white-space:nowrap")
            if len(hits) > 12:
                ui.label(f"… et {len(hits) - 12} autres.").classes("rmeta")
            ui.label("Aperçu sur les mails déjà reçus : le serveur, lui, ne traite que les nouveaux mails à leur arrivée.").classes("rmeta")
            with ui.row().classes("justify-end w-full"):
                ui.button("Fermer", on_click=dlg.close).props("flat no-caps")
        await dlg
        dlg.delete()

    # ---------- création et modification ----------
    async def srules_from_mail(self, m: dict) -> None:
        """Depuis un message : « toujours ranger les mails de cet expéditeur… »."""
        if m["account"] not in self.srules_accounts():
            return ui.notify("Les règles du serveur ne concernent que les boîtes Exchange.", type="warning")
        self.srules_acct = m["account"]
        await self.srules_edit(None, prefill={"sender": (m["sender"] or "").lower()}, load=True)

    async def srules_edit(self, rule: dict | None, prefill: dict | None = None, load: bool = False) -> None:
        if load or not getattr(self, "_srules", None) or self._srules.get("acct") != self.srules_acct:
            try:
                cl = self._srules_client(self.srules_acct)
                folders = await run.io_bound(cl.folders)
                live = await run.io_bound(cl.inbox_rules, folders)
            except Exception as e:  # noqa: BLE001
                return ui.notify(f"Lecture des règles impossible : {e}", type="negative", multi_line=True)
            self._srules = {"acct": self.srules_acct, "folders": folders, "live": live}
        folders = self._srules["folders"]
        base = next((d for d in self._srules_desired() if rule and d["id"] == rule["id"]), None)
        can_edit = base is None or sr.editable(base)
        sender = (prefill or {}).get("sender", "")
        form = {"name": base["name"] if base else (sender.split("@")[-1] if sender else ""), "enabled": base["enabled"] if base else True,
                "stop": base["stop_processing"] if base else False,
                "conds": [{"type": c["type"], "text": "; ".join(c["values"])} for c in base["conditions"]] if base else ([{"type": "sender_contains", "text": sender}] if sender else [{"type": "sender_contains", "text": ""}]),
                "excs": [{"type": c["type"], "text": "; ".join(c["values"])} for c in (base.get("exceptions") or [])] if base else [],
                "move": next((a["folder_id"] for a in base["actions"] if a["type"] == "move_to_folder"), "") if base else "",
                "read": any(a["type"] == "mark_read" for a in base["actions"]) if base else False,
                "delete": any(a["type"] == "delete" for a in base["actions"]) if base else False}
        paths = dict(sorted(folders.items(), key=lambda kv: kv[1].lower()))

        def build_rule() -> dict:
            acts = []
            if form["move"]:
                acts.append({"type": "move_to_folder", "folder_id": form["move"], "value": folders.get(form["move"], "")})
            if form["read"]:
                acts.append({"type": "mark_read"})
            if form["delete"]:
                acts.append({"type": "delete"})
            conds = [{"type": c["type"], "values": split_words(c["text"]) if c["type"] in sr.WORD_CONDITIONS else ["true"]} for c in form["conds"]]
            excs = [{"type": c["type"], "values": split_words(c["text"]) if c["type"] in sr.WORD_CONDITIONS else ["true"]} for c in form["excs"]]
            return {"id": base["id"] if base else None, "name": form["name"], "enabled": form["enabled"], "stop_processing": form["stop"],
                    "conditions": conds, "exceptions": excs, "actions": acts}

        with self.host, ui.dialog() as dlg, ui.card().style("width:min(680px,95vw);max-height:92vh;overflow:auto"):
            ui.label("Modifier la règle" if base else "Nouvelle règle de tri").classes("text-h6")
            if not can_edit:
                ui.label("Cette règle comporte des critères ou des actions que Velora ne sait pas modifier. Vous pouvez seulement la renommer ou l'activer.").classes("rmeta")
                ui.label(sr.describe(base)).classes("text-body2")
                ui.input("Nom", value=form["name"], on_change=lambda e: form.update(name=e.value)).props("outlined dense stack-label").classes("w-full")
                ui.switch("Règle active", value=form["enabled"], on_change=lambda e: form.update(enabled=bool(e.value))).props("dense")
            else:
                if config.ai_enabled():
                    with ui.row().classes("w-full items-center no-wrap gap-2"):
                        nl = ui.input(placeholder="Ou décrivez-la : « les mails de Dupont avec facture vont dans Compta »").props("outlined dense").classes("col")

                        async def interpret() -> None:
                            await self._srules_interpret(nl.value, form, folders)
                            body.refresh()
                        ui.button("✦ Interpréter", on_click=interpret).props("flat dense no-caps color=primary").mark("srules-interpret")
                ui.input("Nom de la règle", value=form["name"], on_change=lambda e: form.update(name=e.value)).props("outlined dense stack-label").classes("w-full").mark("srules-name")

                @ui.refreshable
                def body() -> None:
                    ui.label("Quand le message correspond à…").classes("text-subtitle2")
                    self._srules_rows(form["conds"], body)
                    ui.button("Ajouter un critère", icon="add", on_click=lambda: (form["conds"].append({"type": "sender_contains", "text": ""}), body.refresh())).props("flat dense no-caps size=sm")
                    ui.label("…sauf si").classes("text-subtitle2")
                    self._srules_rows(form["excs"], body)
                    ui.button("Ajouter une exception", icon="add", on_click=lambda: (form["excs"].append({"type": "sender_contains", "text": ""}), body.refresh())).props("flat dense no-caps size=sm")
                    ui.label("Alors").classes("text-subtitle2")
                    ui.select({"": "— ne pas déplacer —", **paths}, value=form["move"], label="Déplacer vers le dossier", on_change=lambda e: form.update(move=e.value)).props(
                        "outlined dense stack-label").classes("w-full").mark("srules-folder")
                    ui.checkbox("Marquer comme lu", value=form["read"], on_change=lambda e: form.update(read=bool(e.value))).props("dense")
                    ui.checkbox("Mettre à la corbeille", value=form["delete"], on_change=lambda e: form.update(delete=bool(e.value))).props("dense color=negative")
                    ui.checkbox("Ne pas appliquer les règles suivantes", value=form["stop"], on_change=lambda e: form.update(stop=bool(e.value))).props("dense")
                    ui.switch("Règle active", value=form["enabled"], on_change=lambda e: form.update(enabled=bool(e.value))).props("dense")
                body()
            with ui.row().classes("justify-end w-full gap-2"):
                if can_edit:
                    ui.button("Tester sur mes mails", icon="science", on_click=lambda: self.srules_test({}, draft=sr.clean_rule(build_rule()))).props("flat no-caps dense")
                ui.button("Annuler", on_click=lambda: dlg.submit(None)).props("flat no-caps")
                def proceed() -> None:
                    if can_edit and not (build_rule()["actions"] and build_rule()["conditions"]):
                        return ui.notify("Choisissez au moins un critère et une action (déplacer, marquer comme lu ou corbeille).", type="warning")
                    dlg.submit(True)
                ui.button("Continuer", on_click=proceed).props("unelevated no-caps color=primary").mark("srules-save")
        ok = await dlg
        dlg.delete()
        if not ok:
            return
        desired = self._srules_desired()
        if base is None:
            new = build_rule()
            if not (new["name"] or "").strip():
                return ui.notify("Donnez un nom à la règle.", type="warning")
            desired.append(new)
        elif can_edit:
            desired = [build_rule() if d["id"] == base["id"] else d for d in desired]
        else:
            desired = [{**d, "name": form["name"], "enabled": form["enabled"]} if d["id"] == base["id"] else d for d in desired]
        await self._srules_propose(desired)

    def _srules_rows(self, items: list[dict], body) -> None:
        for it in list(items):
            with ui.row().classes("w-full items-center no-wrap gap-2"):
                ui.select(COND_CHOICES, value=it["type"], on_change=lambda e, it=it: (it.update(type=e.value), body.refresh())).props("outlined dense").style("min-width:210px")
                if it["type"] in sr.WORD_CONDITIONS:
                    ui.input(value=it["text"], placeholder="mots ou adresses, séparés par « ; »", on_change=lambda e, it=it: it.update(text=e.value)).props("outlined dense").classes("col").mark("srules-words")
                else:
                    ui.element("div").classes("col")
                ui.button(icon="close", on_click=lambda _, it=it: (items.remove(it), body.refresh())).props("flat dense round size=sm")

    async def _srules_interpret(self, text: str, form: dict, folders: dict) -> None:
        """Remplit le formulaire à partir d'une phrase (une seule utilisation de l'IA) ; ce qu'un serveur Exchange ne sait pas faire est signalé."""
        if not (text or "").strip():
            return ui.notify("Décrivez la règle d'abord.", type="warning")
        ui.notify("L'IA lit la règle…", timeout=1500)
        try:
            spec = await run.io_bound(rules_mod.interpret, text)
        except ai.AIError as e:
            return ui.notify(str(e), type="negative", multi_line=True)
        c, t = spec["if"], spec["then"]
        conds = ([{"type": "sender_contains", "text": "; ".join(c["from_contains"])}] if c["from_contains"] else []) \
            + ([{"type": "subject_contains", "text": "; ".join(c["subject_contains"])}] if c["subject_contains"] else []) \
            + ([{"type": "body_contains", "text": "; ".join(c["body_contains"])}] if c["body_contains"] else []) \
            + ([{"type": "has_attachment", "text": ""}] if c["has_attachment"] else [])
        notes = [spec["note"]] if spec["note"] else []
        if c["category"]:
            notes.append("« catégorie IA » n'existe pas sur le serveur : ignorée")
        if t["pin"]:
            notes.append("« épingler » n'existe pas sur le serveur : ignoré")
        if conds:
            form["conds"][:] = conds
        form["read"] = bool(t["mark_read"])
        if t["move_to"]:
            fid = next((i for i, p in folders.items() if p.split("/")[-1].lower() == t["move_to"].lower() or p.lower().endswith(t["move_to"].lower())), "")
            form["move"] = fid
            if not fid:
                notes.append(f"dossier « {t['move_to']} » introuvable sur le serveur")
        if not form["name"].strip():
            form["name"] = text.strip()[:60]
        ui.notify("Formulaire rempli : vérifiez avant de continuer." + ("  " + " · ".join(notes) if notes else ""), type="warning" if notes else "positive", multi_line=True)
