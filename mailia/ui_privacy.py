"""Lecteur : ce qui protège la vie privée à la lecture d'un message (expéditeurs de confiance, images distantes, liens, accusé de réception)."""
import json
import webbrowser
from urllib.parse import unquote, urlparse

from nicegui import run, ui

from mailia import db, queries, sync
from mailia.ui_common import SERVER_MODE


class PrivacyMixin:
    @staticmethod
    def trusted_senders() -> set[str]:
        try:
            return set(json.loads(db.kv_get("trusted_senders") or "[]"))
        except (ValueError, TypeError):
            return set()

    def trust_sender(self, addr: str, on: bool) -> None:
        cur = self.trusted_senders()
        cur.add(addr) if on else cur.discard(addr)
        db.kv_set("trusted_senders", json.dumps(sorted(cur)))
        self.reader.refresh()

    def receipt_banner(self, x: dict) -> None:
        """L'expéditeur a demandé un accusé de réception : on le propose, jamais d'envoi automatique (l'envoi révèle que le message a été lu)."""
        to = (x.get("receipt_to") or "").strip()
        acct = sync.ACCOUNTS.get(x.get("account"))
        if not to or db.kv_get(f"receipt:{x['id']}") or (acct and to.lower() == acct.email.lower()):
            return
        with ui.row().classes("mailnote items-center gap-2 w-full").mark("receipt-note"):
            ui.icon("mark_email_read", size="18px")
            ui.label(f"{to} demande un accusé de réception.").classes("col")
            if to.rpartition("@")[2].lower() != (x.get("sender") or "").rpartition("@")[2].lower():  # astuce classique : confirmer à un tiers que votre adresse est active
                ui.icon("warning_amber", size="18px").style("color:var(--hi)").tooltip(
                    "Cette adresse n'est pas celle de l'expéditeur : n'envoyez l'accusé que si vous connaissez cette personne.").mark("receipt-warn")
            ui.button("Envoyer l'accusé", icon="send", on_click=lambda _, i=x["id"]: self.answer_receipt(i, True)).props("flat dense no-caps size=sm color=primary").mark("receipt-send")
            ui.button("Ne pas répondre", on_click=lambda _, i=x["id"]: self.answer_receipt(i, False)).props("flat dense no-caps size=sm").mark("receipt-skip")

    async def answer_receipt(self, mid: str, send: bool) -> None:
        x = next((t for t in self.thread if t["id"] == mid), None) or queries.get_message(mid)
        if not x:
            return
        if send:
            try:
                await run.io_bound(sync.send_receipt, x)
            except Exception as e:
                return ui.notify(f"Accusé non envoyé : {e}", type="negative", multi_line=True)
            ui.notify("Accusé de réception envoyé.", type="positive")
        db.kv_set(f"receipt:{mid}", "1" if send else "0")
        self.reader.refresh()

    def allow_images(self, mid: str) -> None:
        self.images.add(mid)
        self.reader.refresh()

    async def open_link(self, url: str) -> None:
        """Les liens d'un mail ne s'ouvrent jamais seuls : on montre la vraie destination et on demande confirmation."""
        scheme = urlparse(url).scheme.lower()
        if scheme == "mailto":
            self.new_message()
            self.draft["to"] = unquote(url[7:].split("?")[0])
            self.reader.refresh()
            return
        if scheme not in ("http", "https"):
            return ui.notify("Ce type de lien n'est pas ouvert par sécurité.", type="warning")
        with self.host, ui.dialog() as dlg, ui.card().classes("w-[520px] max-w-full"):
            ui.label("Ouvrir ce lien dans votre navigateur ?").classes("text-subtitle1")
            ui.label(url[:300]).classes("rmeta").style("word-break:break-all;font-family:ui-monospace,monospace")
            ui.label("Vérifiez l'adresse : les messages frauduleux imitent souvent des sites connus.").classes("text-caption text-grey")
            with ui.row().classes("justify-end w-full"):
                ui.button("Annuler", on_click=lambda: dlg.submit(False)).props("flat no-caps")
                ui.button("Ouvrir", on_click=lambda: dlg.submit(True)).props("unelevated no-caps color=primary")
        if not await dlg:
            return
        if SERVER_MODE:
            ui.navigate.to(url, new_tab=True)
        else:
            webbrowser.open(url)
