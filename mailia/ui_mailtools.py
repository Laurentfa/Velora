"""Outils autour d'un mail : désabonnement, invitations, traduction, hameçonnage, classement, questions à la boîte, raccourcis clavier."""
import json
from datetime import datetime, timedelta
from urllib.parse import unquote

from nicegui import run, ui

from mailia import ai, aipolicy, authcheck, compose, config, db, queries, security, sync, unsub

from mailia.ui_common import CAT, JOURS, MOIS_COURTS, NOLIST_VIEWS


class MailToolsMixin:
    # ---------- raccourcis clavier ----------
    SHORTCUTS = [("j / ↓", "Message suivant"), ("k / ↑", "Message précédent"), ("r", "Répondre"), ("a", "Répondre à tous"),
                 ("e", "Archiver"), ("#  ou  Suppr", "Supprimer"), ("p", "Épingler / désépingler"), ("u", "Marquer lu / non lu"),
                 ("s", "Reporter…"), ("t", "Créer une tâche"), ("c", "Nouveau message"), ("/", "Rechercher"),
                 ("Échap", "Fermer le message / annuler la sélection"), ("Ctrl/Cmd-clic", "Sélectionner plusieurs messages"),
                 ("Maj-clic", "Sélectionner une plage"), ("?", "Cette aide")]

    async def on_key(self, e) -> None:
        key = (e.args or {}).get("key", "")
        if self.new or self.compose:
            return
        if key == "?":
            return await self.shortcuts_help()
        if key == "c":
            return self.new_message()
        if key == "Escape":
            if self.picked or self.select_mode:
                return self.pick_clear()
            return self.back() if self.cur else None
        if key in ("j", "ArrowDown", "k", "ArrowUp"):
            return await self.move_selection(1 if key in ("j", "ArrowDown") else -1)
        m = self.cur
        if not m:
            return
        if key == "r":
            await self.reply()
        elif key == "a":
            await self.reply(all_=True)
        elif key == "f":
            await self.forward(m)
        elif key in ("e", "#", "Delete"):
            await self.act_and_advance("archive" if key == "e" else "delete")
        elif key == "p" and m["folder"] == "INBOX":
            await self.toggle_pin(m)
        elif key == "u":
            await self.toggle_read(m)
        elif key == "s" and m["folder"] == "INBOX":
            await self.snooze_dialog(m)
        elif key == "t":
            await self.task_dialog(m)

    def _neighbour(self, delta: int) -> dict | None:
        rows = self.rows if self.view not in NOLIST_VIEWS else []
        if not rows:
            return None
        cur = self.cur.get("ckey") if self.cur else None
        idx = next((i for i, r in enumerate(rows) if r["ckey"] == cur), -1)
        j = 0 if idx < 0 else idx + delta
        return rows[j] if 0 <= j < len(rows) else None

    async def move_selection(self, delta: int) -> None:
        nxt = self._neighbour(delta)
        if nxt:
            await self.open(nxt["id"])
            ui.run_javascript("setTimeout(() => { const r = document.querySelector('.mrow.sel'); if (r) r.scrollIntoView({block: 'nearest'}); }, 250)")

    async def act_and_advance(self, kind: str) -> None:
        """Archiver / supprimer la conversation ouverte, puis ouvrir la suivante pour enchaîner."""
        m = self.cur
        if not m:
            return
        await self.swipe_act(m, kind)  # ouvre la conversation suivante

    async def shortcuts_help(self) -> None:
        with self.host, ui.dialog() as dlg, ui.card().style("width:460px;max-width:96vw"):
            ui.label("Raccourcis clavier").classes("text-h6")
            ui.label("Actifs quand vous n'êtes pas en train d'écrire.").classes("text-caption text-grey")
            for k, d in self.SHORTCUTS:
                with ui.row().classes("w-full items-center no-wrap"):
                    ui.label(k).style("width:120px;font-family:var(--font-mono,monospace);font-weight:700;color:var(--accent)")
                    ui.label(d)
            with ui.row().classes("justify-end w-full"):
                ui.button("Fermer", on_click=dlg.close).props("flat no-caps")
        dlg.open()

    def unsub_banner(self, m: dict) -> None:
        info = json.loads(m.get("unsub") or "{}") if (m.get("unsub") or "").startswith("{") else {}
        role = sync.folder_role(m["account"], m["folder"])
        if not info or role in ("sent", "drafts"):  # dans tous les dossiers sauf ses propres messages (la boîte de réception, mais aussi les sous-dossiers, l'archive, les indésirables)
            return
        if security.assess(m)["level"]:
            return  # on ne « se désabonne » jamais d'un message suspect : cela confirmerait l'adresse à un escroc
        if role == "junk":  # un indésirable : seulement si le serveur a vérifié l'expéditeur (SPF/DKIM/DMARC) ou si on le connaît déjà ; sinon c'est sans doute du vrai spam
            try:
                auth = json.loads(m.get("auth") or "{}")
            except ValueError:
                auth = {}
            if not (authcheck.verified(auth) or security.is_known(m["sender"])):
                return
        with ui.row().classes("items-center gap-2 no-wrap q-px-sm").style("width:100%"):
            ui.icon("unsubscribe", size="18px").style("color:var(--muted)")
            ui.label("Message envoyé à une liste de diffusion." + (" Il est classé en indésirable : se désabonner arrête ces envois." if role == "junk" else "")).classes("rmeta col")
            ui.button("Se désabonner", on_click=lambda: self.unsubscribe(m, info)).props("outline dense no-caps size=sm color=primary")

    async def unsubscribe(self, m: dict, info: dict) -> None:
        sender = m["sender_name"] or m["sender"]
        if info.get("one_click"):
            how = "La demande est envoyée directement par l'application, sans ouvrir de page. Aucun message n'est envoyé depuis votre boîte."
        elif info.get("https") or info.get("link"):
            how = "La page de désabonnement de l'expéditeur s'ouvrira dans votre navigateur : vous y confirmerez."
        else:
            how = "Un message de désabonnement sera préparé : vous le relisez et vous l'envoyez vous-même."
        with self.host, ui.dialog() as dlg, ui.card().style("width:480px;max-width:96vw"):
            ui.label(f"Se désabonner de {sender} ?").classes("text-h6")
            ui.label(how).classes("text-body2")
            with ui.row().classes("justify-end w-full gap-2"):
                ui.button("Annuler", on_click=lambda: dlg.submit(False)).props("flat no-caps")
                ui.button("Se désabonner", on_click=lambda: dlg.submit(True)).props("unelevated no-caps color=primary")
        if not await dlg:
            return
        if info.get("one_click"):
            ok, detail = await run.io_bound(unsub.one_click, info["https"])
            if ok:
                ui.notify(f"Demande de désabonnement envoyée à {sender}. Vous pouvez archiver ce message.", type="positive", multi_line=True)
            else:
                ui.notify(f"Le désabonnement direct a échoué ({detail}). Ouverture de la page de l'expéditeur.", type="warning", multi_line=True)
                await self.open_link(info["https"])
        elif info.get("https") or info.get("link"):
            await self.open_link(info.get("https") or info["link"])
        elif info.get("mailto"):
            from urllib.parse import parse_qs, urlparse
            u = urlparse(info["mailto"])
            q = parse_qs(u.query)
            self.write_to([unquote(u.path)])
            if self.new:
                self.draft["subject"] = (q.get("subject") or ["Désabonnement"])[0]
                self.draft["html"] = compose.from_text((q.get("body") or ["Merci de me désabonner de cette liste de diffusion."])[0])
                self.reader.refresh()

    # ---------- agenda Exchange : invitations, rendez-vous ----------
    @staticmethod
    def when_range(start_iso: str, end_iso: str) -> str:
        if not start_iso:
            return ""
        a = datetime.fromisoformat(start_iso).astimezone()
        txt = f"{JOURS[a.weekday()]} {a.day} {MOIS_COURTS[a.month - 1]} {a:%H:%M}"
        if end_iso:
            b = datetime.fromisoformat(end_iso).astimezone()
            txt += f" – {b:%H:%M}" if b.date() == a.date() else f" – {JOURS[b.weekday()]} {b.day} {MOIS_COURTS[b.month - 1]} {b:%H:%M}"
        return txt

    def meeting_card(self, m: dict) -> None:
        raw = m.get("meeting") or ""
        if not raw.startswith("{"):
            return
        info = json.loads(raw)
        kind = info.get("kind")
        done = db.kv_get(f"meeting_done:{m['id']}")
        title = {"request": "Invitation à une réunion", "cancel": "Réunion annulée", "response": "Réponse à une invitation"}.get(kind, "Réunion")
        with ui.element("div").classes("aicard"):
            ui.label(f"📅 {title}").classes("tag")
            ui.label(m["subject"]).style("font-weight:700").classes("q-mt-xs")
            when = self.when_range(info.get("start", ""), info.get("end", ""))
            if when:
                ui.label(when)
            if info.get("location"):
                ui.label("Lieu : " + info["location"]).classes("sub")
            if kind == "request":
                if info.get("old"):
                    ui.label("Cette invitation a été mise à jour depuis : une version plus récente existe.").classes("sub")
                if done:
                    ui.label({"accept": "✓ Vous avez accepté.", "tentative": "Vous avez répondu « peut-être ».", "decline": "Vous avez décliné."}[done]).style("font-weight:600")
                elif sync.ACCOUNTS.get(m["account"]) and sync.ACCOUNTS[m["account"]].kind == "ews":
                    with ui.row().classes("gap-2 q-mt-sm"):
                        ui.button("Accepter", icon="check", on_click=lambda: self.meeting_reply(m, "accept")).props("unelevated no-caps dense color=primary")
                        ui.button("Peut-être", icon="help_outline", on_click=lambda: self.meeting_reply(m, "tentative")).props("outline no-caps dense")
                        ui.button("Refuser", icon="close", on_click=lambda: self.meeting_reply(m, "decline")).props("outline no-caps dense color=negative")

    async def meeting_reply(self, m: dict, action: str) -> None:
        label = {"accept": "Accepter", "tentative": "Répondre « peut-être »", "decline": "Refuser"}[action]
        with self.host, ui.dialog() as dlg, ui.card().style("width:480px;max-width:96vw"):
            ui.label(f"{label} cette invitation ?").classes("text-h6")
            ui.label("Votre réponse est envoyée à l'organisateur, et la réunion est ajoutée à votre agenda si vous acceptez."
                     + (" Elle est aussi ajoutée à Google Agenda (l'agenda Exchange la reçoit aussi : c'est le comportement d'Exchange)."
                        if config.calendar_backend() == "google" and action != "decline" else "")).classes("text-body2")
            with ui.row().classes("justify-end w-full gap-2"):
                ui.button("Annuler", on_click=lambda: dlg.submit(False)).props("flat no-caps")
                ui.button(label, on_click=lambda: dlg.submit(True)).props("unelevated no-caps color=primary")
        if not await dlg:
            return
        try:
            await run.io_bound(sync.respond_meeting, m, action)
        except Exception as e:
            return ui.notify(f"Réponse impossible : {e}", type="negative", multi_line=True)
        db.kv_set(f"meeting_done:{m['id']}", action)
        ui.notify("Réponse envoyée à l'organisateur", type="positive")
        self.reader.refresh()

    async def event_dialog(self, m: dict) -> None:
        start = (datetime.now() + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
        with self.host, ui.dialog() as dlg, ui.card().style("width:540px;max-width:96vw"):
            ui.label("Ajouter à l'agenda").classes("text-h6")
            title = ui.input("Titre", value=(m["subject"] or "")[:150]).props("outlined dense stack-label").classes("w-full")
            with ui.row().classes("w-full no-wrap gap-2"):
                begin = ui.input("Début", value=f"{start:%Y-%m-%dT%H:%M}").props("outlined dense stack-label type=datetime-local").classes("col")
                end = ui.input("Fin", value=f"{start + timedelta(hours=1):%Y-%m-%dT%H:%M}").props("outlined dense stack-label type=datetime-local").classes("col")
            where = ui.input("Lieu (facultatif)").props("outlined dense stack-label").classes("w-full")
            ui.label("Le rendez-vous est créé dans votre " + ("Google Agenda" if config.calendar_backend() == "google" else "agenda Exchange")
                     + ", sans invité : personne n'est prévenu.").classes("text-caption text-grey")
            if config.ai_enabled():
                async def suggest() -> None:
                    ui.notify("L'IA lit le message…")
                    try:
                        r = await run.io_bound(ai.suggest_event, m["id"])
                    except ai.AIError as e:
                        return ui.notify(str(e), type="negative")
                    title.set_value(r["title"] or title.value)
                    if r["start"]:
                        begin.set_value(r["start"])
                        end.set_value(r["end"] or r["start"])
                    where.set_value(r["location"] or "")
                ui.button("✦ Proposer avec l'IA", on_click=suggest).props("flat no-caps dense color=primary")
            with ui.row().classes("justify-end w-full gap-2"):
                ui.button("Annuler", on_click=lambda: dlg.submit(None)).props("flat no-caps")
                ui.button("Ajouter", on_click=lambda: dlg.submit((title.value, begin.value, end.value, where.value))).props("unelevated no-caps color=primary")
        r = await dlg
        if not r:
            return
        try:
            a, b = datetime.fromisoformat(r[1]), datetime.fromisoformat(r[2])
            if b <= a or not (r[0] or "").strip():
                return ui.notify("Indiquez un titre, et une fin postérieure au début.", type="warning")
            await run.io_bound(sync.create_event, m["account"], r[0].strip(), a, b, r[3] or "", f"Depuis le message : {m['subject']}")
        except Exception as e:
            return ui.notify(f"Ajout impossible : {e}", type="negative", multi_line=True)
        ui.notify("Rendez-vous ajouté à l'agenda", type="positive")

    # ---------- traduction ----------
    async def translate(self, m: dict) -> None:
        self.translations[m["id"]] = "…"
        self.reader.refresh()
        try:
            self.translations[m["id"]] = await run.io_bound(ai.translate, m["id"])
        except ai.AIError as e:
            self.translations.pop(m["id"], None)
            with self.host:
                ui.notify(str(e), type="negative")
        self.reader.refresh()

    def translation_card(self, m: dict) -> None:
        text = self.translations.get(m["id"]) or db.kv_get(f"translation:{m['id']}")
        if text:
            with ui.element("div").classes("aicard"):
                with ui.row().classes("w-full items-center no-wrap"):
                    ui.label("🌐 Traduction en français").classes("tag col")
                    ui.button(icon="close", on_click=lambda: (self.translations.pop(m["id"], None), db.kv_set(f"translation:{m['id']}", ""),
                                                              self.reader.refresh())).props("flat dense round size=xs").tooltip("Masquer")
                if text == "…":
                    ui.spinner(size="20px")
                else:
                    ui.label(text).style("white-space:pre-wrap").classes("q-mt-xs")
        elif config.ai_enabled() and ai.looks_foreign(m.get("body") or ""):
            with ui.row().classes("items-center gap-2 q-px-sm"):
                ui.icon("translate", size="18px").style("color:var(--muted)")
                ui.label("Ce message semble écrit dans une autre langue.").classes("rmeta")
                ui.button("Traduire en français", on_click=lambda: self.translate(m)).props("outline dense no-caps size=sm color=primary")

    # ---------- demander à sa boîte ----------
    async def ask_dialog(self) -> None:
        from mailia import ask
        with self.host, ui.dialog() as dlg, ui.card().classes("no-wrap").style("width:min(780px,96vw);max-width:96vw;max-height:88vh"):
            with ui.row().classes("w-full items-center no-wrap"):
                ui.label("✦ Demander à ma boîte").classes("text-h6 col").style("font-weight:700")
                ui.button(icon="close", on_click=dlg.close).props("flat round dense")
            ui.label("Posez une question en français : l'IA cherche dans vos mails et répond en citant ses sources. "
                     "Coût : environ un centime par question.").classes("text-caption text-grey")
            q = ui.input(placeholder="ex : Qu'a répondu Dupont pour la facture ?").props("outlined dense autofocus clearable").classes("w-full")
            out = ui.column().classes("w-full gap-2").style("overflow:auto")

            async def go() -> None:
                if not (q.value or "").strip():
                    return
                out.clear()
                with out:
                    with ui.row().classes("items-center gap-2"):
                        ui.spinner(size="20px")
                        ui.label("Recherche dans vos mails…").classes("rmeta")
                try:
                    r = await run.io_bound(ask.answer, q.value)
                except ai.AIError as e:
                    out.clear()
                    with out:
                        ui.label(str(e)).classes("text-negative")
                    return
                out.clear()
                with out:
                    with ui.element("div").classes("aicard"):
                        ui.label("✦ Réponse").classes("tag")
                        ui.label(r["text"]).style("white-space:pre-wrap").classes("q-mt-xs")
                    if r["sources"]:
                        async def open_source(mid: str) -> None:
                            dlg.close()
                            await self.open(mid)

                        def source(s: dict) -> None:
                            d = datetime.fromisoformat(s["date"]).astimezone()
                            with ui.element("div").classes("srcrow cursor-pointer").on("click", lambda _, i=s["id"]: open_source(i)).mark(f"source-{s['n']}"):
                                ui.label(str(s["n"])).classes("srcnum" + (" cited" if s["cited"] else ""))
                                with ui.column().classes("gap-0 col").style("min-width:0"):
                                    ui.label(s["subject"] or "(sans objet)").style("font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap")
                                    ui.label(s["sender"]).classes("rmeta").style("overflow:hidden;text-overflow:ellipsis;white-space:nowrap")
                                ui.label(f"{d.day} {MOIS_COURTS[d.month - 1]} {d.year}").classes("rmeta").style("white-space:nowrap")
                        cited = [s for s in r["sources"] if s["cited"]]
                        others = [s for s in r["sources"] if not s["cited"]]
                        ui.label("Sources citées" if cited else "Mails consultés").classes("rmeta q-mt-sm").style(
                            "text-transform:uppercase;letter-spacing:.06em;font-size:11px")
                        for s in cited or others:
                            source(s)
                        if cited and others:
                            with ui.expansion(f"Autres mails consultés ({len(others)})").props("dense expand-icon-toggle").classes("w-full").style("color:var(--muted)"):
                                for s in others:
                                    source(s)

            q.on("keydown.enter", go)
            ui.button("Demander", icon="send", on_click=go).props("unelevated no-caps color=primary")
        dlg.open()

    async def category_dialog(self, m: dict) -> None:
        """Corrige la catégorie donnée par l'IA, pour ce message ou pour tous les messages de cet expéditeur."""
        with self.host, ui.dialog() as dlg, ui.card().style("width:480px;max-width:96vw"):
            ui.label("Corriger le classement").classes("text-h6")
            ui.label(f"{m['sender_name'] or m['sender']} · {m['subject']}").classes("rmeta")
            cat = ui.select({k: v for k, v in CAT.items()}, value=m["category"] if m["category"] in CAT else "info",
                            label="Ce message est plutôt…").props("outlined dense stack-label").classes("w-full q-mt-sm")
            always = ui.checkbox(f"Toujours classer ainsi les mails de {m['sender']}", value=True)
            ui.label("L'IA n'est pas relancée : la correction est locale et se retient pour cet expéditeur.").classes("text-caption text-grey")
            with ui.row().classes("justify-end w-full gap-2"):
                ui.button("Annuler", on_click=lambda: dlg.submit(None)).props("flat no-caps")
                ui.button("Corriger", on_click=lambda: dlg.submit((cat.value, bool(always.value)))).props("unelevated no-caps color=primary")
        r = await dlg
        if not r:
            return
        category, remember = r
        noise = category in ("newsletter", "notification")
        with db.conn() as c:
            where, args = ("sender=? COLLATE NOCASE", (m["sender"],)) if remember else ("id=?", (m["id"],))
            c.execute(f"UPDATE messages SET category=?{', needs_reply=0, priority=MIN(priority,1)' if noise else ''} WHERE {where}", (category, *args))
        learned_domain = None
        if remember:
            db.kv_set("sender_cat:" + m["sender"].lower(), category)
        learned_domain = aipolicy.record_correction(m, m["category"], category) if remember else None
        ui.notify("Classement corrigé" + (" pour cet expéditeur." if remember else "."), type="positive")
        if learned_domain:
            ui.notify(f"J'ai retenu que les mails de @{learned_domain} sont « {CAT.get(category, category)} » "
                      "(modifiable dans Paramètres › Automatismes).", type="info", multi_line=True, timeout=7000)
        if self.thread:
            self.thread = queries.thread(m["ckey"])
        if self.cur and self.cur.get("id"):
            self.cur = queries.get_message(self.cur["id"]) or self.cur
        self.reader.refresh()
        await self.reload(force=True)

    def phishing_banner(self, m: dict) -> None:
        if m["folder"] != "INBOX" and not m.get("body"):
            return
        res = security.assess(m)
        self.phishing_ai_card(m, res)
        if not res["level"]:
            return
        high = res["level"] == "élevé"
        with ui.element("div").classes("phishwarn" + (" high" if high else "")):
            with ui.row().classes("items-center gap-2 no-wrap"):
                ui.icon("gpp_maybe", size="22px")
                ui.label("Ce message ressemble très probablement à une arnaque (hameçonnage)." if high
                         else "Ce message est suspect : vérifiez avant de cliquer.").style("font-weight:700")
            for r in res["reasons"][:4]:
                ui.label("• " + r).classes("rmeta").style("color:inherit;opacity:.9")
            ui.label("Ne cliquez sur aucun lien et ne répondez pas. En cas de doute, passez par le site officiel que vous tapez vous-même, "
                     "ou appelez l'organisme.").classes("rmeta").style("color:inherit;opacity:.9")
            with ui.row().classes("items-center gap-2 q-mt-xs"):
                if config.ai_enabled() and not aipolicy.is_excluded(m) and not db.kv_get(f"phish:{m['id']}"):
                    ui.button("Demander l'avis de l'IA", icon="auto_awesome", on_click=lambda: self.phishing_ai(m)).props(
                        "flat dense no-caps size=sm").style("color:inherit")
                with ui.button("Ce n'est pas une arnaque", icon="verified_user").props("flat dense no-caps size=sm").style("color:inherit"):
                    with ui.menu():
                        ui.menu_item("Pour ce message seulement", on_click=lambda: self.mark_not_suspect(m))
                        ui.menu_item(f"Pour tous les messages de {m['sender']}", on_click=lambda: self.mark_not_suspect(m, sender=True))

    def mark_not_suspect(self, m: dict, sender: bool = False) -> None:
        security.mark_safe(mid="" if sender else m["id"], sender=m["sender"] if sender else "")
        ui.notify(f"Compris : les messages de {m['sender']} ne seront plus signalés." if sender else "Compris : ce message n'est plus signalé comme suspect.",
                  type="positive")
        self.reader.refresh()
        self.rows_view.refresh()

    def phishing_ai_card(self, m: dict, res: dict) -> None:
        """Résultats techniques d'authentification de l'expéditeur, et avis de l'IA s'il a été demandé."""
        auth = security._json(m.get("auth"), {})
        verified = not res["level"] and authcheck.verified(auth)
        cached = db.kv_get(f"phish:{m['id']}") if config.ai_enabled() else ""
        op = None
        if cached:
            try:
                op = json.loads(cached)
            except ValueError:
                op = None
        if verified:  # une seule petite ligne : l'expéditeur est bien celui qu'il prétend être
            with ui.row().classes("items-center gap-1 no-wrap q-px-sm").style("opacity:.8") as vrow:
                ui.icon("verified_user", size="14px").style("color:var(--accent)")
                ui.label("Expéditeur vérifié").classes("rmeta")
            vrow.tooltip("Votre serveur de messagerie a vérifié que ce message vient bien de l'adresse affichée. "
                         "Cela ne garantit pas que son contenu soit honnête.")
        if not op:
            return
        with ui.element("div").classes("aicard"):
            if op:
                icon, color = {"sûr": ("verified_user", "var(--accent)"), "à vérifier": ("help_outline", "#b26a00"), "dangereux": ("gpp_bad", "#c62828")}[op["verdict"]]
                with ui.row().classes("items-center gap-2 no-wrap"):
                    ui.icon(icon, size="20px").style(f"color:{color}")
                    ui.label(f"✦ Avis de l'IA : {op['verdict']}").style(f"font-weight:700;color:{color}").classes("col")
                    ui.button(icon="refresh", on_click=lambda: self.phishing_ai(m, True)).props("flat round dense size=sm").tooltip("Refaire l'analyse")
                for r in op["reasons"]:
                    ui.label("• " + r).classes("sub")
                if op.get("advice"):
                    ui.label(op["advice"]).style("font-weight:600").classes("q-mt-xs")
                ui.label("Aide à la décision, pas un verdict : vérifiez par un autre moyen (téléphone, site officiel) avant toute action sensible.").classes("sub q-mt-xs")

    async def phishing_ai(self, m: dict, force: bool = False) -> None:
        ui.notify("L'IA examine le message…")
        try:
            await run.io_bound(ai.phishing_opinion, m["id"], force)
        except ai.AIError as e:
            return ui.notify(str(e), type="negative", multi_line=True)
        except Exception as e:
            return ui.notify(f"Analyse impossible : {e}", type="negative", multi_line=True)
        self.reader.refresh()
