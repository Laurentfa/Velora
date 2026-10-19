"""Reports, relances et tâches."""
from datetime import datetime, timedelta, timezone

from nicegui import background_tasks, run, ui

from mailia import ai, compose, config, db, sync

from mailia.ui_common import JOURS, MOIS_COURTS, label_of


class TasksMixin:
    # ---------- reports, relances, tâches ----------
    @staticmethod
    def snooze_options() -> list[tuple[str, datetime]]:
        now = datetime.now().astimezone()
        at = lambda d, h: d.replace(hour=h, minute=0, second=0, microsecond=0)
        out = []
        if now.hour < 13:
            out.append(("Cet après-midi", at(now, 14)))
        if now.hour < 17:
            out.append(("Ce soir", at(now, 18)))
        out.append(("Demain matin", at(now + timedelta(days=1), 8)))
        out.append(("Lundi matin", at(now + timedelta(days=(7 - now.weekday()) % 7 or 7), 8)))
        out.append(("Dans une semaine", at(now + timedelta(days=7), 8)))
        seen, uniq = set(), []
        for label, d in out:
            if d not in seen:
                seen.add(d)
                uniq.append((label, d))
        return uniq

    @staticmethod
    def fr_when(dt: datetime) -> str:
        dt = dt.astimezone()
        return f"{JOURS[dt.weekday()]} {dt.day} {MOIS_COURTS[dt.month - 1]} {dt:%H:%M}"

    async def snooze(self, m: dict, until: datetime) -> None:
        db.snooze_set(m["ckey"], m["account"], until.astimezone(timezone.utc).isoformat())
        ui.notify(f"Reporté : il reviendra {self.fr_when(until)}", type="positive")
        await self._after_swipe(m, removed=True)

    async def snooze_dialog(self, m: dict) -> None:
        default = self.snooze_options()[0][1] if self.snooze_options() else datetime.now().astimezone() + timedelta(days=1)
        with self.host, ui.dialog() as dlg, ui.card().style("width:460px;max-width:96vw"):
            ui.label("Reporter ce message").classes("text-h6")
            ui.label("Il disparaît de la boîte de réception et revient, non lu, à l'heure choisie.").classes("text-caption text-grey")
            when = ui.input("Date et heure", value=default.strftime("%Y-%m-%dT%H:%M")).props("outlined dense stack-label type=datetime-local").classes("w-full")
            with ui.row().classes("justify-end w-full gap-2"):
                ui.button("Annuler", on_click=lambda: dlg.submit(None)).props("flat no-caps")
                ui.button("Reporter", on_click=lambda: dlg.submit(when.value)).props("unelevated no-caps color=primary")
        v = await dlg
        if not v:
            return
        try:
            until = datetime.fromisoformat(v).astimezone()
        except ValueError:
            return ui.notify("Date invalide.", type="warning")
        if until <= datetime.now().astimezone():
            return ui.notify("Cette heure est déjà passée.", type="warning")
        await self.snooze(m, until)

    def snoozed_row(self, r: dict) -> None:
        until = datetime.fromisoformat(r["until"]).astimezone()
        with ui.element("div").classes("mrow read").on("click", lambda _, i=r["id"]: self.open(i)):
            ui.element("div").classes("dot")
            with ui.element("div").classes("rb"):
                with ui.element("div").classes("top"):
                    ui.label(r["sender_name"] or r["sender"]).classes("from")
                ui.label(r["subject"]).classes("subj")
                with ui.element("div").classes("badges"):
                    ui.label(f"Reviendra {self.fr_when(until)}").classes("badge rep")
                    ui.label(label_of(r["account"])).classes("badge acc")
                with ui.row().classes("gap-1 q-mt-xs"):
                    ui.button("Le revoir maintenant", icon="alarm_on", on_click=lambda _, k=r["ckey"]: self.unsnooze(k)).props(
                        "flat dense no-caps size=sm color=primary").on("click.stop", lambda _, k=r["ckey"]: self.unsnooze(k))

    async def unsnooze(self, ckey: str) -> None:
        db.snooze_clear(ckey)
        ui.notify("Remis dans la boîte de réception")
        await self.reload(force=True)

    def followup_row(self, f: dict) -> None:
        sent = datetime.fromisoformat(f["sent_at"]).astimezone()
        with ui.element("div").classes("mrow read"):
            ui.element("div").classes("dot")
            with ui.element("div").classes("rb"):
                ui.label(f["to_addrs"]).classes("from")
                ui.label(f["subject"] or "(sans objet)").classes("subj")
                with ui.element("div").classes("badges"):
                    ui.label(f"Envoyé {self.fr_when(sent)} · sans réponse").classes("badge rep")
                    ui.label(label_of(f["account"])).classes("badge acc")
                with ui.row().classes("gap-1 q-mt-xs"):
                    ui.button("Relancer", icon="reply", on_click=lambda _, f=f: self.relancer(f)).props("flat dense no-caps size=sm color=primary")
                    ui.button("Dans 2 jours", icon="schedule", on_click=lambda _, f=f: self.followup_later(f)).props("flat dense no-caps size=sm")
                    ui.button("Ignorer", icon="close", on_click=lambda _, f=f: self.followup_close(f)).props("flat dense no-caps size=sm color=grey-7")

    async def relancer(self, f: dict) -> None:
        self.new_message()
        if not self.new:
            return
        subject = f["subject"] or ""
        self.draft.update(importance=1, account=f["account"] if f["account"] in sync.ACCOUNTS else self.draft["account"], to=f["to_addrs"],
                          subject=subject if subject.lower().startswith("relance") else "Relance : " + subject,
                          html=compose.from_text("Bonjour,\n\nJe me permets de revenir vers vous au sujet de mon message ci-dessous, resté sans réponse. "
                                                 "Pourriez-vous me tenir informé ?\n\nMerci d'avance.", account=self.draft["account"]))
        db.followup_set(f["id"], "done")
        self.reader.refresh()
        await self.reload(force=True)

    async def followup_later(self, f: dict) -> None:
        db.followup_set(f["id"], "wait", (datetime.now(timezone.utc) + timedelta(days=2)).isoformat())
        await self.reload(force=True)

    async def followup_close(self, f: dict) -> None:
        db.followup_set(f["id"], "done")
        await self.reload(force=True)

    def tasks_view(self) -> None:
        def add() -> None:
            if txt.value and txt.value.strip():
                db.task_add(txt.value, f"{due.value}T00:00:00" if due.value else "", "")
                self.refresh_after_task()

        with ui.element("div").style("padding:10px 14px;width:100%"):
            with ui.row().classes("w-full items-center no-wrap gap-2"):
                txt = ui.input(placeholder="Nouvelle tâche…").props("outlined dense").classes("col")
                txt.on("keydown.enter", add)
                due = ui.input().props("outlined dense type=date").style("width:150px")
                ui.button(icon="add", on_click=add).props("unelevated round dense color=primary").tooltip("Ajouter")
            ui.switch("Afficher les tâches terminées", value=self.show_done,
                      on_change=lambda e: (setattr(self, "show_done", bool(e.value)), self.refresh_after_task())).props("dense")
        if not self.tasks:
            ui.label("Aucune tâche. Créez-en une ici, ou depuis un message avec le bouton ✓ de la barre d'actions.").classes("empty")
        today = datetime.now().astimezone().date()
        for t in self.tasks:
            with ui.element("div").classes("mrow read").style("cursor:default"):
                with ui.row().classes("w-full items-center no-wrap gap-2"):
                    ui.checkbox(value=bool(t["done"]), on_change=lambda e, t=t: (db.task_done(t["id"], bool(e.value)), self.refresh_after_task())).props("dense")
                    with ui.column().classes("gap-0 col").style("min-width:0"):
                        ui.label(t["text"]).style("font-weight:600;" + ("text-decoration:line-through;opacity:.55" if t["done"] else ""))
                        if t["due"]:
                            d = datetime.fromisoformat(t["due"]).date()
                            late = d < today and not t["done"]
                            ui.label(("En retard · " if late else "Pour le ") + f"{JOURS[d.weekday()]} {d.day} {MOIS_COURTS[d.month - 1]}"
                                     ).classes("badge" + (" p3" if late else " rep")).style("align-self:flex-start")
                    if t["mid"]:
                        ui.button(icon="mail_outline", on_click=lambda _, i=t["mid"]: self.open(i)).props("flat dense round size=sm").tooltip("Ouvrir le message")
                    ui.button(icon="delete_outline", on_click=lambda _, t=t: (db.task_delete(t["id"]), self.refresh_after_task())).props(
                        "flat dense round size=sm color=negative").tooltip("Supprimer")

    def refresh_after_task(self) -> None:
        background_tasks.create(self.reload(force=True))  # garde une référence à la tâche (sinon elle peut être ramassée avant la fin)

    async def task_dialog(self, m: dict) -> None:
        with self.host, ui.dialog() as dlg, ui.card().style("width:520px;max-width:96vw"):
            ui.label("Nouvelle tâche").classes("text-h6")
            title = ui.input("Tâche", value=(m["subject"] or "")[:120]).props("outlined dense stack-label").classes("w-full")
            due = ui.input("Pour le (facultatif)").props("outlined dense stack-label type=date").classes("w-full")
            if config.ai_enabled():
                async def suggest() -> None:
                    ui.notify("L'IA lit le message…")
                    try:
                        r = await run.io_bound(ai.suggest_task, m["id"])
                    except ai.AIError as e:
                        return ui.notify(str(e), type="negative")
                    title.set_value(r.get("title") or title.value)
                    due.set_value(r.get("due") or "")
                ui.button("✦ Proposer avec l'IA", on_click=suggest).props("flat no-caps dense color=primary")
            with ui.row().classes("justify-end w-full gap-2"):
                ui.button("Annuler", on_click=lambda: dlg.submit(None)).props("flat no-caps")
                ui.button("Créer", on_click=lambda: dlg.submit((title.value, due.value))).props("unelevated no-caps color=primary")
        r = await dlg
        if r and (r[0] or "").strip():
            db.task_add(r[0], f"{r[1]}T00:00:00" if r[1] else "", m["id"])
            ui.notify("Tâche créée", type="positive")
            self.side.refresh()
