"""Rédaction : éditeur, brouillons enregistrés automatiquement, modèles, carnet d'adresses."""
import json
import re
import uuid
from datetime import datetime

from nicegui import run, ui

from mailia import ai, compose, config, contacts_ui, db, htmlmail, queries, sync
from mailia.textutil import html_to_text

from mailia.ui_common import ago, label_of, log, make_editor


class DraftsMixin:
    def draft_row(self, dr: dict) -> None:
        top, _ = compose.split_user_part(dr["html"] or "")
        snippet = " ".join(html_to_text(top).split())[:140]
        with ui.element("div").classes("mrow read").on("click", lambda _, i=dr["id"]: self.resume_draft(i)):
            ui.element("div").classes("dot")
            with ui.element("div").classes("rb"):
                with ui.element("div").classes("top"):
                    ui.label(dr["to_addrs"] or "(sans destinataire)").classes("from")
                    with ui.element("div").classes("right"):
                        ui.label(ago(dr["updated"])).classes("date")
                        ui.button(icon="delete_outline", on_click=lambda _, i=dr["id"]: self.delete_draft(i)).props(
                            "flat dense round size=sm").on("click.stop", lambda _, i=dr["id"]: self.delete_draft(i)).tooltip("Supprimer ce brouillon")
                ui.label(dr["subject"] or "(sans objet)").classes("subj")
                if snippet:
                    ui.label(snippet).classes("sum")
                with ui.element("div").classes("badges"):
                    ui.label(("Transfert" if dr["reply_to"].startswith("fwd:") else "Réponse") if dr["reply_to"] else "Nouveau message").classes("badge")
                    ui.label(label_of(dr["account"] or "")).classes("badge acc")
                    if dr.get("source") == "api":
                        ui.label(dr.get("api_note") or "Créé par l'API").classes("badge rep")

    async def delete_draft(self, did: str) -> None:
        db.draft_delete(did)
        if self.draft.get("id") == did:
            self.draft["id"] = ""
        ui.notify("Brouillon supprimé")
        await self.reload(force=True)

    def composer(self) -> None:
        d = self.draft
        with ui.element("div").classes("composer"):
            if self.new:
                accounts = {k: f"{c.label} ({c.email})" for k, c in sync.ACCOUNTS.items() if c.password}
                ui.select(accounts, label="De", on_change=lambda e: self.account_changed(e.value)).bind_value(d, "account").props("outlined dense").classes("w-full q-mb-sm")
            if config.ai_enabled():
                with ui.row().classes("w-full items-center no-wrap q-mb-sm gap-2"):
                    ui.input("Décrivez le mail à rédiger" if self.new else "Dites à l'IA quoi répondre (facultatif)",
                             placeholder="ex : relancer Mme Martin pour le devis" if self.new else "ex : accepter mais proposer jeudi…"
                             ).bind_value(d, "ins").props("outlined dense stack-label").classes("col").on("keydown.enter", self.gen_draft)
                    self.gen_btn = ui.button("✦ Rédiger" if not d.get("gen") else "✦ Régénérer", on_click=self.gen_draft).props("unelevated no-caps color=primary")
            opts = contacts_ui.recipient_options()
            to_sel, add_to = contacts_ui.recipients_field("À", d, "to", opts)
            self.rcpt_add = {"to": add_to}
            favs = contacts_ui.favorites()
            if favs:
                with ui.row().classes("items-center gap-1 q-mt-xs").style("flex-wrap:wrap"):
                    ui.label("Favoris :").classes("rmeta")
                    for label, kind, ref in favs[:14]:
                        ui.button(("⭐ " if kind == "contact" else "") + label,
                                  on_click=lambda _, k=kind, r=ref: add_to(contacts_ui.favorite_addresses(k, r))).props(
                            "outline dense no-caps size=sm color=primary")
            cc_field, add_cc = contacts_ui.recipients_field("Cc", d, "cc", opts)
            cc_field.classes("q-mt-sm")
            bcc_field, add_bcc = contacts_ui.recipients_field("Cci (copie cachée)", d, "bcc", opts)
            bcc_field.classes("q-mt-sm")
            bcc_field.set_visibility(bool(d.get("bcc")))
            self.rcpt_add.update(cc=add_cc, bcc=add_bcc)
            self.bcc_field = bcc_field
            self.subject_input = ui.input("Objet").bind_value(d, "subject").props("outlined dense").classes("w-full q-mt-sm")
            with ui.row().classes("gap-1 q-mt-xs"):
                ui.button("Cci", icon="visibility_off", on_click=lambda: bcc_field.set_visibility(not bcc_field.visible)).props(
                    "flat dense no-caps size=sm").tooltip("Ajouter des destinataires en copie cachée (invisibles pour les autres)")
                ui.button("Image", icon="image", on_click=self.insert_image).props("flat dense no-caps size=sm").tooltip("Insérer une image dans le message")
                ui.button("Ma signature", icon="draw", on_click=self.insert_signature).props("flat dense no-caps size=sm").tooltip("Insérer ma signature à l'endroit du curseur")
                with ui.button("Modèles", icon="description").props("flat dense no-caps size=sm"):
                    with ui.menu():
                        tpls = db.templates_list()
                        for t in tpls:
                            ui.menu_item(t["name"], on_click=lambda _, t=t: self.use_template(t))
                        if not tpls:
                            ui.menu_item("Aucun modèle pour l'instant").props("disable")
                        ui.separator()
                        ui.menu_item("Enregistrer mon message comme modèle…", on_click=self.save_as_template)
                        ui.menu_item("Gérer mes modèles…", on_click=self.manage_templates)
                if config.ai_enabled():
                    with ui.button("Améliorer", icon="auto_fix_high").props("flat dense no-caps size=sm").tooltip("Corriger ou réécrire avec l'IA"):
                        with ui.menu():
                            for mode, (label, _) in ai.REWRITE_MODES.items():
                                ui.menu_item(label, on_click=lambda _, m=mode: self.improve(m))
                ui.space()
                self.save_label = ui.label("").classes("rmeta")
            up = ui.upload(on_upload=self.on_upload, multiple=True, auto_upload=True,
                           max_file_size=25 * 1024 * 1024).props("hide-upload-btn").style("position:absolute;left:-9999px;width:1px;height:1px;overflow:hidden")
            ui.run_javascript(f"window.__mailiaUp = {up.id}")  # cible des fichiers déposés sur la rédaction
            with ui.row().classes("q-mt-sm items-center gap-2"):
                ui.button("Joindre un fichier (25 Mo max au total)", icon="attach_file",
                          on_click=None).props("flat no-caps dense").on(
                    "click", js_handler=f"() => getElement({up.id}).$refs.qRef.pickFiles()")
                ui.button("Scanner un document", icon="document_scanner", on_click=self.scan_dialog).props("flat no-caps dense").tooltip(
                    "Numériser avec un scanner du réseau ou avec votre téléphone, et joindre le résultat en PDF")
            self.files_view()
            with ui.row().classes("q-mt-sm"):
                ui.button("Envoyer", on_click=self.send).props("unelevated no-caps color=primary")
                ui.button("Annuler", on_click=self.cancel_compose).props("flat no-caps")
            # la barre d'actions (et le bouton Envoyer) est au-dessus du texte : on n'a pas à descendre en bas d'un long message
            self.editor = make_editor(d["html"], lambda e: d.__setitem__("html", e.value or ""))
            self.editor.classes("q-mt-sm")

    def account_changed(self, account: str) -> None:
        """Nouvelle boîte d'expédition : la signature suit (si elle a la sienne)."""
        d = self.draft
        new_html = compose.swap_signature(d["html"] or "", account)
        if new_html != (d["html"] or "") and getattr(self, "editor", None) is not None:
            d["html"] = new_html
            self.editor.set_value(new_html)

    async def open_contacts(self) -> None:
        await contacts_ui.contacts_dialog(self.host, self.write_to, self.contacts_closed)

    def contacts_closed(self) -> None:
        """Le carnet a pu changer (groupes, favoris) : l'éditeur ouvert en dessous reprend ses suggestions et sa barre « Favoris »."""
        if self.new or self.compose:
            self.reader.refresh()

    def write_to(self, addresses: list[str]) -> None:
        """Depuis le carnet : nouveau message déjà adressé."""
        self.new_message()
        if self.new:
            self.draft["to"] = ", ".join(addresses)
            self.reader.refresh()

    def new_message(self) -> None:
        accounts = [k for k, c in sync.ACCOUNTS.items() if c.password]
        if not accounts:
            return ui.notify("Aucun compte configuré (⚙ Paramètres).", type="warning")
        self.draft.update(account=self.account if self.account in accounts else accounts[0], to="", cc="", bcc="", subject="",
                          body="", ins="", tone="neutre", files=[], quote=False, html=compose.initial_html(account=self.account if self.account in accounts else accounts[0]), gen=False)
        self.new, self.cur, self.compose = True, None, True
        self.draft.update(id=uuid.uuid4().hex, reply_to=None)
        self._saved_sig = self._files_sig = None
        self.root.classes(add="reading")
        self.reader.refresh()
        self.rows_view.refresh()

    async def forward(self, m: dict | None = None) -> None:
        """Transférer : nouveau message avec l'original cité en dessous et ses pièces jointes reprises."""
        m = m or self.cur
        if not m:
            return
        if self.compose or self.new:
            await self._save_draft_now()
        try:
            m = await run.io_bound(lambda: sync.ensure_details(queries.get_message(m["id"])))
            atts = json.loads(m["att_json"]) if m.get("att_json") else []
            cids = htmlmail.cid_refs(htmlmail.sanitize(m["body_html"])) if m.get("body_html") else []
            if cids:  # images incorporées dans le texte (photo envoyée depuis un iPhone, logo…) : elles doivent suivre le transfert
                got = await run.io_bound(sync.inline_images, m, cids)
                body = m["body_html"]
                for cid, uri in got.items():
                    body = re.sub(r"cid:(%3C)?" + re.escape(cid) + r"(%3E)?", lambda _: uri, body, flags=re.I)
                m = {**m, "body_html": body}
            files = []
            for a in atts:
                if a.get("inline"):
                    continue
                data, name = await run.io_bound(sync.fetch_attachment, m, a)
                files.append((name or a.get("name") or "piece-jointe", data, a.get("type") or "application/octet-stream"))
        except Exception as e:
            return ui.notify(f"Impossible de préparer le transfert : {e}", type="negative", multi_line=True)
        self.new_message()
        if not self.new:
            return
        acct = m["account"] if m["account"] in sync.ACCOUNTS and sync.ACCOUNTS[m["account"]].password else self.draft["account"]
        subject = m["subject"] or ""
        # « TR: » sans espace avant les deux-points : Exchange ne reconnaît pas « TR : » et ouvrirait une autre conversation
        self.draft.update(account=acct, subject=subject if re.match(r"(?i)\s*(tr|fwd?)\s*:", subject) else "TR: " + subject, files=files,
                          html=compose.initial_html(reply_to=m, account=acct, images=True), reply_to="fwd:" + m["id"])  # « fwd: » : à l'envoi, l'original est marqué « transféré »
        self.files_view.refresh()
        self.reader.refresh()

    def open_compose(self, all_: bool = False, text: str = "") -> None:
        m = self.cur
        me = sync.ACCOUNTS[m["account"]].email.lower()
        mine = m["sender"].lower() == me  # on répond à un message qu'on a envoyé : les destinataires restent les mêmes
        first = (m["to_addrs"] if mine else m["sender"])
        others = lambda s: [a.strip() for a in (s or "").split(",") if a.strip() and a.strip().lower() not in (me, m["sender"].lower())]
        if all_:
            self.draft.update(to=first if mine else ", ".join([m["sender"], *others(m["to_addrs"])]), cc=", ".join(others(m["cc_addrs"])), bcc="")
        else:
            self.draft.update(to=first, cc="", bcc="")
        html = (compose.fill_template(htmlmail.text_to_html(text), m) + compose.sig_block(m["account"]) + compose.quote_html(m)) if text else compose.initial_html(reply_to=m, account=m["account"])
        self.draft.update(html=html, gen=False)
        if not self.compose:  # nouvelle réponse : nouveau brouillon
            self.draft.update(id=uuid.uuid4().hex, reply_to=m["id"])
            self._saved_sig = self._files_sig = None
        self.compose = True
        self.reader.refresh()

    # ---------- brouillons enregistrés automatiquement ----------
    def _has_content(self) -> bool:
        d = self.draft
        top, _ = compose.split_user_part(d["html"] or "")
        return bool(html_to_text(top).strip() or "<img" in top or d["subject"].strip() or d["to"].strip() or d["files"])

    async def _save_draft_now(self) -> bool:
        d = self.draft
        if not (self.compose or self.new) or not d.get("id") or not self._has_content():
            return False
        sig = (d["account"] if self.new else (self.cur or {}).get("account", ""), d["to"], d["cc"], d.get("bcc", ""), d["subject"], d["html"])
        files_sig = tuple((f[0], len(f[1])) for f in d["files"])
        if sig == self._saved_sig and files_sig == self._files_sig:
            return False
        account = d["account"] if self.new else (self.cur or {}).get("account", "")
        row = {"id": d["id"], "account": account, "is_new": self.new, "reply_to": d.get("reply_to"),
               "to": d["to"], "cc": d["cc"], "bcc": d.get("bcc", ""), "subject": d["subject"], "html": d["html"]}
        await run.io_bound(db.draft_save, row, list(d["files"]) if files_sig != self._files_sig else None)
        self._saved_sig, self._files_sig = sig, files_sig
        if self.save_label is not None:
            try:
                self.save_label.set_text(f"Brouillon enregistré à {datetime.now():%H:%M}")
            except Exception:
                pass
        return True

    async def autosave(self) -> None:
        if self.compose or self.new:
            try:
                await self._save_draft_now()
            except Exception:
                log.exception("enregistrement du brouillon")

    async def cancel_compose(self) -> None:
        if self._has_content():
            with self.host, ui.dialog() as dlg, ui.card().classes("w-[460px] max-w-full"):
                ui.label("Que faire de ce message ?").classes("text-h6")
                ui.label("Vous pouvez le garder dans « Brouillons » pour le reprendre plus tard.").classes("text-body2")
                with ui.row().classes("justify-end w-full"):
                    ui.button("Continuer à écrire", on_click=lambda: dlg.submit("stay")).props("flat no-caps")
                    ui.button("Supprimer", on_click=lambda: dlg.submit("delete")).props("flat no-caps color=negative")
                    ui.button("Garder en brouillon", on_click=lambda: dlg.submit("keep")).props("unelevated no-caps color=primary")
            choice = await dlg
            if choice in (None, "stay"):
                return
            if choice == "keep":
                await self._save_draft_now()
                ui.notify("Brouillon conservé", type="positive")
            else:
                db.draft_delete(self.draft["id"])
        elif self.draft.get("id"):
            db.draft_delete(self.draft["id"])
        self.close_compose()
        await self.reload(force=True)

    async def resume_draft(self, did: str) -> None:
        row, files = db.draft_load(did)
        if not row:
            return
        if row["reply_to"] and queries.get_message(row["reply_to"]):
            await self.open(row["reply_to"])
            self.compose = True
        else:
            self.new_message()
        self.draft.update(id=did, to=row["to_addrs"] or "", cc=row["cc"] or "", bcc=row.get("bcc") or "", subject=row["subject"] or "", html=row["html"] or "",
                          files=files, gen=False, reply_to=row["reply_to"])
        if row["account"] in sync.ACCOUNTS and self.new:
            self.draft["account"] = row["account"]
        self._saved_sig = self._files_sig = None
        self.root.classes(add="reading")
        self.reader.refresh()

    # ---------- modèles de réponse ----------
    def use_template(self, t: dict) -> None:
        m = self.cur if (not self.new and self.cur) else None
        added = []
        for key, label in (("to", "À"), ("cc", "Cc"), ("bcc", "Cci")):  # fusion : les destinataires déjà saisis sont conservés, ceux du modèle s'ajoutent
            wanted = contacts_ui.split_addrs(t.get("to_addrs" if key == "to" else key) or "")
            fresh = [a for a in wanted if a not in contacts_ui.split_addrs(self.draft.get(key) or "")]
            if fresh and getattr(self, "rcpt_add", None) and key in self.rcpt_add:
                self.rcpt_add[key](fresh)
                added.append(f"{label} : {', '.join(fresh)}")
                if key == "bcc" and getattr(self, "bcc_field", None) is not None:
                    self.bcc_field.set_visibility(True)  # une copie cachée ne doit jamais s'ajouter à l'insu de l'utilisateur
        if added:
            ui.notify("Destinataires ajoutés — " + " · ".join(added), type="info", multi_line=True)
        ui.run_javascript(f"mailiaInsert({json.dumps(self.editor.html_id)}, {json.dumps(compose.fill_template(t['html'], m))})")
        if t["subject"] and not self.draft["subject"].strip():
            filled = re.sub(r"<[^>]+>", "", compose.fill_template(t["subject"], m))
            self.draft["subject"] = filled
            if self.subject_input is not None:
                self.subject_input.set_value(filled)

    async def save_as_template(self) -> None:
        top, _ = compose.split_user_part(self.draft["html"] or "")
        if not html_to_text(top).strip():
            return ui.notify("Écrivez d'abord le message à enregistrer comme modèle.", type="warning")
        with self.host, ui.dialog() as dlg, ui.card().style("width:560px;max-width:96vw"):
            ui.label("Enregistrer comme modèle").classes("text-h6")
            name = ui.input("Nom du modèle", value=self.draft["subject"] or "").props("outlined dense stack-label autofocus").classes("w-full")
            ui.label("Astuce : écrivez {{prenom}}, {{nom}}, {{date}} ou {{objet}} dans le texte : ils seront remplacés à l'utilisation.").classes("text-caption text-grey")
            rc = {k: contacts_ui.split_addrs(self.draft.get(k) or "") for k in ("to", "cc", "bcc")}
            keep = ui.checkbox("Mémoriser aussi les destinataires", value=True) if any(rc.values()) else None
            if keep:
                ui.label(" · ".join(f"{lbl} : {', '.join(rc[k])}" for k, lbl in (("to", "À"), ("cc", "Cc"), ("bcc", "Cci")) if rc[k])).classes("text-caption text-grey")
            with ui.row().classes("justify-end w-full"):
                ui.button("Annuler", on_click=lambda: dlg.submit(None)).props("flat no-caps")
                ui.button("Enregistrer", on_click=lambda: dlg.submit(name.value)).props("unelevated no-caps color=primary")
        label = await dlg
        if label and label.strip():
            use = bool(keep and keep.value)
            db.template_save(None, label.strip(), self.draft["subject"] or "", htmlmail.sanitize_outgoing(top),
                             ", ".join(rc["to"]) if use else "", ", ".join(rc["cc"]) if use else "", ", ".join(rc["bcc"]) if use else "")
            ui.notify("Modèle enregistré" + (" avec ses destinataires" if use else ""), type="positive")
            self.reader.refresh()

    async def manage_templates(self) -> None:
        with self.host, ui.dialog() as dlg, ui.card().style("width:820px;max-width:96vw"):
            ui.label("Mes modèles de réponse").classes("text-h6")
            ui.label("Variables utilisables : {{prenom}} {{nom}} {{date}} {{objet}} (selon le message auquel vous répondez).").classes("text-caption text-grey")

            @ui.refreshable
            def listing() -> None:
                tpls = db.templates_list()
                if not tpls:
                    ui.label("Aucun modèle.").classes("text-caption")
                for t in tpls:
                    with ui.row().classes("w-full items-center no-wrap gap-2").style("border:1px solid var(--line);border-radius:12px;padding:6px 10px"):
                        with ui.column().classes("gap-0 col").style("min-width:0"):
                            ui.label(t["name"]).style("font-weight:600")
                            ui.label(" ".join(html_to_text(t["html"]).split())[:90]).classes("rmeta").style("white-space:nowrap;overflow:hidden;text-overflow:ellipsis")
                            who = " · ".join(f"{lbl} : {t.get(k)}" for k, lbl in (("to_addrs", "À"), ("cc", "Cc"), ("bcc", "Cci")) if (t.get(k) or "").strip())
                            if who:
                                ui.label("✉ " + who).classes("rmeta").style("white-space:nowrap;overflow:hidden;text-overflow:ellipsis;color:var(--accent)")
                        ui.button(icon="edit", on_click=lambda _, t=t: edit(t)).props("flat dense round")
                        ui.button(icon="delete_outline", on_click=lambda _, t=t: drop(t)).props("flat dense round color=negative")

            def drop(t: dict) -> None:
                db.template_delete(t["id"])
                listing.refresh()

            async def edit(t: dict | None) -> None:
                with ui.dialog() as ed, ui.card().style("width:880px;max-width:96vw"):
                    ui.label("Modifier le modèle" if t else "Nouveau modèle").classes("text-h6")
                    name = ui.input("Nom du modèle", value=(t or {}).get("name", "")).props("outlined dense stack-label").classes("w-full")
                    subj = ui.input("Objet (facultatif)", value=(t or {}).get("subject", "")).props("outlined dense stack-label").classes("w-full")
                    rc = {"to": (t or {}).get("to_addrs", "") or "", "cc": (t or {}).get("cc", "") or "", "bcc": (t or {}).get("bcc", "") or ""}
                    opts = contacts_ui.recipient_options()  # mêmes suggestions (contacts, groupes, favoris) que dans la rédaction d'un message
                    contacts_ui.recipients_field("À (facultatif)", rc, "to", opts)
                    with ui.row().classes("w-full no-wrap gap-2"):
                        with ui.column().classes("col"):
                            contacts_ui.recipients_field("Cc", rc, "cc", opts)
                        with ui.column().classes("col"):
                            contacts_ui.recipients_field("Cci", rc, "bcc", opts)
                    ui.label("Ces adresses s'ajoutent à celles du message quand vous utilisez le modèle, sans les remplacer.").classes("text-caption text-grey")
                    box = {"html": (t or {}).get("html", "<p><br></p>")}
                    make_editor(box["html"], lambda e: box.__setitem__("html", e.value or ""), "200px")
                    with ui.row().classes("justify-end w-full"):
                        ui.button("Annuler", on_click=lambda: ed.submit(False)).props("flat no-caps")
                        ui.button("Enregistrer", on_click=lambda: ed.submit(True)).props("unelevated no-caps color=primary")
                if await ed and name.value.strip():
                    clean = lambda v: ", ".join(contacts_ui.split_addrs(v or ""))  # adresses valides seulement, sans doublons
                    db.template_save((t or {}).get("id"), name.value.strip(), subj.value or "", htmlmail.sanitize_outgoing(box["html"]),
                                     clean(rc["to"]), clean(rc["cc"]), clean(rc["bcc"]))
                    listing.refresh()

            listing()
            with ui.row().classes("justify-between w-full"):
                ui.button("Nouveau modèle", icon="add", on_click=lambda: edit(None)).props("outline no-caps dense")
                ui.button("Fermer", on_click=dlg.close).props("flat no-caps")
        await dlg
        self.reader.refresh()

    def close_compose(self) -> None:
        self.compose = False
        self.draft["files"] = []
        if self.new:
            self.new = False
            self.root.classes(remove="reading")
        self.reader.refresh()
