"""Envoi : fenêtre d'envoi, programmation, annulation, file d'attente."""
from datetime import datetime, timedelta, timezone

from nicegui import ui

from mailia import checks, compose, db, i18n, outgoing, settings_ui, sync
from mailia.textutil import html_to_text

from mailia.ui_common import JOURS, MOIS_COURTS, label_of


class OutboxMixin:
    @staticmethod
    def undo_delay() -> int:
        return outgoing.undo_delay()

    @staticmethod
    def when_options() -> dict[str, datetime]:
        """Heures proposées pour un envoi programmé (heure locale)."""
        now = datetime.now().astimezone()
        at8 = lambda d: d.replace(hour=8, minute=0, second=0, microsecond=0)
        tomorrow = at8(now + timedelta(days=1))
        monday = at8(now + timedelta(days=(7 - now.weekday()) % 7 or 7))
        return {"1h": now + timedelta(hours=1), "tom": tomorrow, "mon": monday}

    async def send(self) -> None:
        d, m = self.draft, self.cur
        account = d["account"] if self.new else m["account"]
        plain = html_to_text(d["html"] or "").strip()
        if not d["to"].strip() or not (plain or "<img" in (d["html"] or "")):
            return ui.notify("Destinataire et message requis", type="warning")
        n, delay = len(d["files"]), self.undo_delay()
        opts = self.when_options()
        fr = lambda dt: f"{JOURS[dt.weekday()]} {dt.day} {MOIS_COURTS[dt.month - 1]} {i18n.at()} {dt:%H:%M}"
        with self.host, ui.dialog() as dlg, ui.card().style("width:540px;max-width:96vw"):
            ui.label("Envoyer ce message").classes("text-h6")
            warns = checks.send_warnings(d["html"] or "", d["subject"], d["to"], d["cc"], d.get("bcc", ""), n, bool(d.get("reply_to")),
                                       {c.email.lower() for c in sync.ACCOUNTS.values()})
            for w in warns:
                with ui.row().classes("items-center no-wrap gap-2 q-pa-sm q-mt-xs w-full").style(
                        "background:#fff4e0;border:1px solid #f3c77a;border-radius:10px;color:#6b4300"):
                    ui.icon("warning_amber", size="20px").style("color:#c77800")
                    ui.label(w).classes("text-body2")
            ui.label(f"À : {d['to']}" + (f" · Cc : {d['cc']}" if d["cc"].strip() else "") + (f" · Cci : {d['bcc']}" if d.get("bcc", "").strip() else "") + f" · depuis {label_of(account)}"
                     + (f" · {n} pièce(s) jointe(s)" if n else "")).classes("text-body2")
            when = ui.select({"now": f"Maintenant (vous pourrez annuler pendant {delay} s)" if delay else "Maintenant",
                              "1h": f"Dans 1 heure ({opts['1h']:%H:%M})", "tom": f"Demain à 8 h ({fr(opts['tom'])})",
                              "mon": f"Lundi à 8 h ({fr(opts['mon'])})", "custom": "Choisir la date et l'heure…"},
                             value="now", label="Quand ?").props("outlined dense stack-label").classes("w-full q-mt-sm")
            custom = ui.input("Date et heure d'envoi").props("outlined dense stack-label type=datetime-local").classes("w-full")
            custom.bind_visibility_from(when, "value", backward=lambda v: v == "custom")
            hint = db.kv_get(f"api_send_at:{d.get('id')}") if d.get("id") else ""
            if hint:  # une clé d'API a demandé un envoi à cette heure : proposée d'office, vous gardez la main
                try:
                    want = datetime.fromisoformat(hint.replace("Z", "+00:00"))
                    want = (want if want.tzinfo else want.astimezone()).astimezone()
                    if want > datetime.now().astimezone():
                        when.set_value("custom")
                        custom.set_value(want.strftime("%Y-%m-%dT%H:%M"))
                except ValueError:
                    pass
            follow = ui.select({0: "Non", 2: "Dans 2 jours", 3: "Dans 3 jours", 5: "Dans 5 jours", 7: "Dans 1 semaine", 14: "Dans 2 semaines"},
                               value=0, label="Me rappeler si je n'ai pas de réponse").props("outlined dense stack-label").classes("w-full")
            def confirm() -> None:
                dlg.submit({"when": when.value, "custom": custom.value, "follow": int(follow.value or 0)})

            with ui.row().classes("justify-end w-full gap-2 q-mt-sm"):
                ui.button("Annuler", on_click=lambda: dlg.submit(None)).props("flat no-caps")
                go = ui.button(on_click=confirm).props("unelevated no-caps color=primary")
                go.bind_text_from(when, "value", backward=lambda v: ("Envoyer quand même" if warns else "Envoyer") if v == "now" else "Programmer l'envoi")
        choice = await dlg
        if not choice:
            return
        now = datetime.now().astimezone()
        if choice["when"] == "now":
            at = now + timedelta(seconds=delay)
        elif choice["when"] == "custom":
            try:
                at = datetime.fromisoformat(choice["custom"]).astimezone()
            except (ValueError, TypeError):
                return ui.notify("Choisissez une date et une heure valides.", type="warning")
            if at <= now:
                return ui.notify("Cette heure est déjà passée.", type="warning")
        else:
            at = opts[choice["when"]]
        oid = outgoing.queue(account, d["to"], d["cc"], d.get("bcc", ""), d["subject"], d["html"], d["files"], at, draft_id=d.get("id"),
                             reply_to=d.get("reply_to"), followup_days=choice["follow"])
        self.close_compose()
        self._watch[oid] = d["subject"] or "(sans objet)"
        with self.host:  # le bouton cliqué a disparu avec l'éditeur : on repart d'un élément stable
            if choice["when"] == "now":
                if delay:
                    self.show_undo(oid, delay)
                else:
                    ui.notify("Envoi en cours…", type="positive")
            else:
                ui.notify(f"Message programmé : {fr(at)}", type="positive")
        await self.reload(force=True)

    def show_undo(self, oid: str, delay: int) -> None:
        """Barre « Envoi dans N s · Annuler » : le message n'est pas encore parti."""
        left = {"n": delay}
        bar = ui.element("div").classes("undobar")

        async def cancel() -> None:
            timer.cancel()
            bar.delete()
            await self.undo_send(oid)

        with bar:
            label = ui.label(f"Envoi dans {delay} s…")
            ui.button("Annuler l'envoi", on_click=cancel).props("flat dense no-caps color=white")

        def tick() -> None:
            left["n"] -= 1
            if left["n"] <= 0:
                timer.cancel()
                bar.delete()
            else:
                label.set_text(f"Envoi dans {left['n']} s…")

        timer = ui.timer(1, tick)

    async def undo_send(self, oid: str) -> None:
        """Rappelle un message pas encore parti : il redevient un brouillon modifiable."""
        row = db.outbox_get(oid)
        if not row or row["status"] != "pending":
            return ui.notify("Trop tard : le message est déjà parti.", type="warning")
        self._watch.pop(oid, None)
        await self.outbox_to_draft(oid)

    async def outbox_to_draft(self, oid: str) -> None:
        did = outgoing.to_draft(oid)
        if not did:
            return
        await self.resume_draft(did)
        await self.reload(force=True)

    async def outbox_now(self, oid: str) -> None:
        db.outbox_mark(oid, "pending", send_at=datetime.now(timezone.utc).isoformat())
        ui.notify("Envoi en cours…")
        await self.reload(force=True)

    async def outbox_drop(self, oid: str) -> None:
        self._watch.pop(oid, None)
        db.outbox_delete(oid)
        ui.notify("Message supprimé")
        await self.reload(force=True)

    def outbox_row(self, r: dict) -> None:
        top, _ = compose.split_user_part(r["html"] or "")
        snippet = " ".join(html_to_text(top).split())[:140]
        at = datetime.fromisoformat(r["send_at"]).astimezone()
        left = (at - datetime.now().astimezone()).total_seconds()
        status = ("Échec : " + (r["error"] or "erreur d'envoi")) if r["status"] == "error" else \
            "État incertain : vérifiez dans « Envoyés »" if r["status"] == "uncertain" else \
            "Envoi en cours…" if r["status"] == "sending" or left <= 0 else \
            f"Part dans {int(left)} s" if left < 90 else f"Programmé : {JOURS[at.weekday()]} {at.day} {MOIS_COURTS[at.month - 1]} {i18n.at()} {at:%H:%M}"
        with ui.element("div").classes("mrow read"):
            ui.element("div").classes("dot")
            with ui.element("div").classes("rb"):
                with ui.element("div").classes("top"):
                    ui.label(r["to_addrs"] or "(sans destinataire)").classes("from")
                ui.label(r["subject"] or "(sans objet)").classes("subj")
                if snippet:
                    ui.label(snippet).classes("sum")
                with ui.element("div").classes("badges"):
                    ui.label(status).classes("badge" + (" p3" if r["status"] in ("error", "uncertain") else " rep"))
                    ui.label(label_of(r["account"] or "")).classes("badge acc")
                with ui.row().classes("gap-1 q-mt-xs"):
                    if r["status"] == "uncertain":
                        ui.label(r["error"] or db.UNCERTAIN_NOTE).classes("rmeta")
                    if r["status"] in ("pending", "error", "uncertain"):
                        ui.button("Envoyer maintenant" if r["status"] == "pending" else "Renvoyer quand même" if r["status"] == "uncertain" else "Réessayer", icon="send",
                                  on_click=lambda _, i=r["id"]: self.outbox_now(i)).props("flat dense no-caps size=sm color=primary")
                        ui.button("Modifier", icon="edit", on_click=lambda _, i=r["id"]: self.outbox_to_draft(i)).props("flat dense no-caps size=sm")
                        ui.button("Supprimer", icon="delete_outline", on_click=lambda _, i=r["id"]: self.outbox_drop(i)).props(
                            "flat dense no-caps size=sm color=negative")

    async def watch_outbox(self) -> None:
        """Annonce « envoyé » (ou l'échec) pour les messages mis en file depuis cette fenêtre."""
        if not self._watch:
            return
        rows = {r["id"]: r for r in db.outbox_list()}
        for oid, subject in list(self._watch.items()):
            r = rows.get(oid)
            if r is None:
                self._watch.pop(oid)
                ui.notify(f"Message envoyé : {subject}", type="positive")
                alert = settings_ui.send_alert()
                if alert:
                    ui.notify(settings_ui.alert_text(alert), type="warning", multi_line=True, timeout=12000)
                await self.reload(force=True)
            elif r["status"] == "error":
                self._watch.pop(oid)
                alert = settings_ui.send_alert()
                note = f" {settings_ui.alert_text(alert)}" if alert and alert["kind"] == "uncertain" else ""
                ui.notify(f"Échec d'envoi de « {subject} » : {r['error']}. Il reste dans « En attente d'envoi ».{note}", type="negative", multi_line=True, timeout=15000)
        if self.view == "outbox":
            await self.reload(force=True)
