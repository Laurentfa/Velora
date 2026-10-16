"""Recherche : saisie, recherches enregistrées, complément de recherche sur les serveurs."""
import asyncio
import json
import logging
import time

from nicegui import background_tasks, run, ui

from mailia import db, search, sync
from mailia.ui_common import NOLIST_VIEWS

log = logging.getLogger("mailia")
REMOTE_TTL = 600  # secondes : la même recherche n'interroge pas deux fois le serveur


class SearchMixin:
    def saved_searches(self) -> list[dict]:
        try:
            return json.loads(db.kv_get("saved_searches") or "[]")
        except ValueError:
            return []

    async def save_search(self) -> None:
        q = (self.q or "").strip()
        if not q:
            return ui.notify("Tapez d'abord une recherche.", type="warning")
        with self.host, ui.dialog() as dlg, ui.card().style("width:460px;max-width:96vw"):
            ui.label("Enregistrer la recherche").classes("text-h6")
            ui.label(q).classes("rmeta").style("font-family:monospace;word-break:break-all")
            name = ui.input("Nom", value=q[:40]).props("outlined dense autofocus").classes("w-full")
            with ui.row().classes("justify-end w-full gap-2"):
                ui.button("Annuler", on_click=lambda: dlg.submit(None)).props("flat no-caps")
                ui.button("Enregistrer", on_click=lambda: dlg.submit(name.value)).props("unelevated no-caps color=primary")
        label = await dlg
        if not label or not label.strip():
            return
        items = [s for s in self.saved_searches() if s["q"] != q] + [{"name": label.strip()[:60], "q": q}]
        db.kv_set("saved_searches", json.dumps(items[-30:], ensure_ascii=False))
        self.side.refresh()
        ui.notify("Recherche enregistrée", type="positive")

    async def run_saved_search(self, q: str) -> None:
        await self.go("inbox", "", "")
        self.search.set_value(q)

    def delete_saved_search(self, q: str) -> None:
        db.kv_set("saved_searches", json.dumps([s for s in self.saved_searches() if s["q"] != q], ensure_ascii=False))
        self.side.refresh()

    def search_everywhere(self) -> bool:
        """Depuis la boîte de réception (ou « Non lus »), une recherche porte sur tous les dossiers ; depuis un dossier précis, sur ce dossier."""
        return bool(self.q.strip()) and self.view in ("inbox", "unread")

    async def set_query(self, q: str) -> None:
        if q != self.q:
            self.q = q
            self.refresh_title()
            await self.reload(force=True)
            if q.strip():
                background_tasks.create(self.remote_search(q))

    async def remote_search(self, q: str) -> None:
        """Après la recherche dans la copie locale, interroge aussi les serveurs (mails plus anciens que ceux copiés ici, dossiers non suivis),
        puis complète la liste : mêmes dossiers que la recherche locale (le dossier affiché et ses sous-dossiers, ou tous avec « dossier:tous »)."""
        adv = search.parse(q)
        text = (adv["text"] if adv["advanced"] else q).strip()
        if self.view in NOLIST_VIEWS or not sync.server_search_on() or len(sync.clean_query(text)) < 3:
            return
        folder = self.folder if self.view == "folder" else "INBOX"
        accounts = [self.account] if self.account else list(sync.ACCOUNTS)
        everywhere = adv["all_folders"] or self.search_everywhere()
        key = (sync.clean_query(text), tuple(accounts), folder, everywhere)
        cached = self._remote_cache.get(key)
        if cached and time.time() - cached[0] < REMOTE_TTL:
            ids = cached[1]
        else:
            await asyncio.sleep(0.7)  # on laisse finir la frappe
            if q != self.q:
                return
            self.remote_busy = True
            self.refresh_title()
            try:
                ids = await run.io_bound(sync.search_remote, text, accounts, folder, everywhere)
            except Exception:
                log.exception("recherche sur le serveur")
                ids = set()
            finally:
                self.remote_busy = False
            self._remote_cache[key] = (time.time(), ids)
        self.refresh_title()
        if q != self.q:  # la recherche a changé pendant l'attente
            return
        self.remote_ids, self.remote_for = ids, q
        await self.reload()
