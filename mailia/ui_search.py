"""Recherche : saisie, recherches enregistrées."""
import json

from nicegui import ui

from mailia import db


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

    async def set_query(self, q: str) -> None:
        if q != self.q:
            self.q = q
            await self.reload(force=True)
