"""Réponses : réponses rapides, réponses et corrections par l'IA."""
import asyncio
import html
import json

from nicegui import run, ui

from mailia import ai, compose, db
from mailia.textutil import html_to_text


class ComposeAiMixin:
    INTENTS_DEFAULT = [
        {"label": "Accepter", "text": "Accepte la demande."},
        {"label": "Refuser poliment", "text": "Refuse poliment, en remerciant."},
        {"label": "Demander des précisions", "text": "Demande les précisions nécessaires pour traiter la demande."},
        {"label": "Accuser réception", "text": "Accuse réception et remercie, sans s'engager sur autre chose."},
    ]

    def ai_intents(self) -> list[dict]:
        raw = db.kv_get("ai_intents")
        if raw is None:
            return list(self.INTENTS_DEFAULT)
        try:
            return [q for q in json.loads(raw) if q.get("label") and q.get("text")]
        except (ValueError, TypeError, AttributeError):
            return list(self.INTENTS_DEFAULT)

    async def manage_intents(self) -> None:
        await self._edit_pairs(
            "ai_intents", "Mes réponses IA", "Un clic sur l'une d'elles demande à l'IA de rédiger un brouillon dans ce sens, que vous relisez avant d'envoyer.",
            self.ai_intents(), "Menu", "Consigne donnée à l'IA")

    @staticmethod
    def tone() -> str:
        return db.kv_get("ai_tone") or "neutre"

    async def _ok_to_replace(self) -> bool:
        """Une réponse est déjà en cours d'écriture : on ne l'écrase jamais sans demander."""
        if not self.compose:
            return True
        top, _ = compose.split_user_part(self.draft["html"] or "")
        if not html_to_text(top).strip():
            return True
        with self.host, ui.dialog() as dlg, ui.card():
            ui.label("Remplacer le texte déjà écrit dans cette réponse ?")
            with ui.row().classes("justify-end w-full"):
                ui.button("Garder mon texte", on_click=lambda: dlg.submit(False)).props("flat no-caps")
                ui.button("Remplacer", on_click=lambda: dlg.submit(True)).props("unelevated no-caps color=primary")
        return bool(await dlg)

    async def reply(self, all_: bool = False) -> None:
        if await self._ok_to_replace():
            self.open_compose(all_=all_)

    async def quick_reply(self, text: str) -> None:
        if await self._ok_to_replace():
            self.open_compose(text=text)

    async def ai_reply(self, instruction: str) -> None:
        if not await self._ok_to_replace():
            return
        self.open_compose()
        self.draft["ins"] = instruction
        await asyncio.sleep(0.15)  # laisse l'éditeur s'afficher avant de lancer la rédaction
        with self.host:  # le menu cliqué a disparu avec le rafraîchissement : on repart d'un élément stable
            await self.gen_draft()

    async def ai_custom(self) -> None:
        with self.host, ui.dialog() as dlg, ui.card().classes("w-[460px] max-w-full"):
            ui.label("Que doit répondre l'IA ?").classes("text-h6")
            ins = ui.input(placeholder="ex : accepter mais proposer jeudi").props("outlined dense autofocus").classes("w-full")
            ins.on("keydown.enter", lambda: dlg.submit(ins.value))
            with ui.row().classes("justify-end w-full"):
                ui.button("Annuler", on_click=lambda: dlg.submit(None)).props("flat no-caps")
                ui.button("✦ Rédiger", on_click=lambda: dlg.submit(ins.value)).props("unelevated no-caps color=primary")
        text = await dlg
        if text and text.strip():
            await self.ai_reply(text.strip())

    async def suggest(self) -> None:
        m = self.cur
        if not m:
            return
        self.suggestions[m["id"]] = "loading"
        self.reader.refresh()
        try:
            self.suggestions[m["id"]] = await run.io_bound(ai.suggest_replies, m["id"])
        except ai.AIError as e:
            self.suggestions.pop(m["id"], None)
            with self.host:
                ui.notify(str(e), type="negative")
        self.reader.refresh()

    def drop_suggestions(self, mid: str) -> None:
        self.suggestions.pop(mid, None)
        self.reader.refresh()

    QUICK_DEFAULT = [
        {"label": "Oui", "text": "Oui."},
        {"label": "Non", "text": "Non."},
        {"label": "Reçu, merci", "text": "Bien reçu, merci."},
        {"label": "Reprise sur décharge", "text": "Reprise sur décharge."},
    ]

    def quick_replies(self) -> list[dict]:
        raw = db.kv_get("quick_replies")
        if raw is None:
            return list(self.QUICK_DEFAULT)
        try:
            return [q for q in json.loads(raw) if q.get("label") and q.get("text")]
        except (ValueError, TypeError, AttributeError):
            return list(self.QUICK_DEFAULT)

    async def manage_quick(self) -> None:
        await self._edit_pairs(
            "quick_replies", "Mes réponses rapides", "Un clic ouvre une réponse déjà rédigée avec ce texte : vous relisez puis envoyez. "
            "Variables : {{prenom}} {{nom}} {{date}} {{objet}}.", self.quick_replies(), "Bouton", "Texte envoyé")

    async def _edit_pairs(self, key: str, title: str, intro: str, current: list[dict], label_hdr: str, text_hdr: str) -> None:
        """Fenêtre d'édition d'une liste (nom court + texte), enregistrée dans la base locale."""
        items = [dict(q) for q in current]
        with self.host, ui.dialog() as dlg, ui.card().style("width:820px;max-width:96vw"):
            ui.label(title).classes("text-h6")
            ui.label(intro).classes("text-caption text-grey")

            @ui.refreshable
            def listing() -> None:
                for q in items:
                    with ui.row().classes("w-full items-start no-wrap gap-2"):
                        ui.input(label_hdr, value=q["label"], on_change=lambda e, q=q: q.__setitem__("label", e.value or "")
                                 ).props("outlined dense stack-label").style("width:170px")
                        ui.textarea(text_hdr, value=q["text"], on_change=lambda e, q=q: q.__setitem__("text", e.value or "")
                                    ).props("outlined dense stack-label autogrow").classes("col")
                        ui.button(icon="delete_outline", on_click=lambda _, q=q: drop(q)).props("flat dense round color=negative")

            def drop(q: dict) -> None:
                items.remove(q)
                listing.refresh()

            def add() -> None:
                items.append({"label": "", "text": ""})
                listing.refresh()

            listing()
            with ui.row().classes("justify-between w-full"):
                ui.button("Ajouter", icon="add", on_click=add).props("outline no-caps dense")
                with ui.row().classes("gap-1"):
                    ui.button("Annuler", on_click=lambda: dlg.submit(False)).props("flat no-caps")
                    ui.button("Enregistrer", on_click=lambda: dlg.submit(True)).props("unelevated no-caps color=primary")
        if await dlg:
            db.kv_set(key, json.dumps([{"label": q["label"].strip(), "text": q["text"].strip()}
                                       for q in items if q["label"].strip() and q["text"].strip()], ensure_ascii=False))
            self.reader.refresh()

    async def summarize_thread(self) -> None:
        ckey = self.cur["ckey"]
        try:
            self.tsum[ckey] = await run.io_bound(ai.summarize_thread, ckey)
        except ai.AIError as e:
            return ui.notify(str(e), type="negative")
        self.reader.refresh()

    # ---------- correction et réécriture par l'IA ----------
    async def improve(self, mode: str) -> None:
        top, rest = compose.split_user_part(self.draft["html"] or "")
        if not html_to_text(top).strip():
            return ui.notify("Écrivez d'abord votre message.", type="warning")
        label = ai.REWRITE_MODES[mode][0]
        ui.notify(f"{label} : l'IA travaille…")
        try:
            new = await run.io_bound(ai.rewrite, top, mode)
        except ai.AIError as e:
            return ui.notify(str(e), type="negative")
        changes = ai.word_changes(top, new)
        if mode == "proofread" and not changes:
            return ui.notify("✓ Aucune faute détectée.", type="positive")
        with self.host, ui.dialog() as dlg, ui.card().style("width:920px;max-width:96vw"):
            ui.label(label).classes("text-h6")
            if mode == "proofread":
                ui.label(f"{len(changes)} correction(s) proposée(s) :").classes("text-body2")
                with ui.scroll_area().style("max-height:300px;width:100%"):
                    for c in changes:
                        old_s = f'<s style="color:#d92d3a">{html.escape(c["ancien"])}</s> ' if c["ancien"] else ""
                        new_s = f'<b style="color:#16a34a">{html.escape(c["nouveau"])}</b>' if c["nouveau"] else ""
                        ui.html(f'<div style="padding:4px 0;border-bottom:1px solid var(--line)"><span style="color:var(--muted)">{html.escape(c["avant"])}</span> '
                                f'{old_s}{new_s} <span style="color:var(--muted)">{html.escape(c["apres"])}</span></div>', sanitize=False)
            else:
                with ui.row().classes("w-full no-wrap gap-3"):
                    for title, h in (("Avant", top), ("Après", new)):
                        with ui.column().classes("col gap-1"):
                            ui.label(title).classes("text-caption text-grey")
                            ui.label(html_to_text(h)).style("white-space:pre-wrap;background:var(--surface2);border-radius:10px;padding:10px;max-height:300px;overflow:auto;width:100%")
            ui.label("Vous pourrez encore modifier le texte après application.").classes("text-caption text-grey")
            with ui.row().classes("justify-end w-full"):
                ui.button("Annuler", on_click=lambda: dlg.submit(False)).props("flat no-caps")
                ui.button("Appliquer", on_click=lambda: dlg.submit(True)).props("unelevated no-caps color=primary")
        if await dlg:
            self.draft["html"] = new + rest
            self.reader.refresh()
            ui.notify("Modifications appliquées", type="positive")

    async def gen_draft(self) -> None:
        if self.new and not self.draft["ins"].strip():
            return ui.notify("Décrivez d'abord le mail à rédiger.", type="warning")
        ui.notify("L'IA rédige le brouillon…")
        btn = getattr(self, "gen_btn", None)
        if btn is not None:
            btn.props("loading")
        try:
            if self.new:
                subject, body = await run.io_bound(ai.draft_new, self.draft["ins"], self.tone())
                self.draft["html"] = compose.from_text(body, account=self.draft["account"])
                if subject and not self.draft["subject"].strip():
                    self.draft["subject"] = subject
            else:
                text = await run.io_bound(ai.draft_reply, self.cur["id"], self.draft["ins"], self.tone())
                self.draft["html"] = compose.from_text(text, self.cur, account=self.cur["account"])
            self.draft["gen"] = True
        except ai.AIError as e:
            ui.notify(str(e), type="negative")
        self.reader.refresh()
