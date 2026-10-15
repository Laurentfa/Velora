"""Sélection multiple dans la liste des messages : Ctrl/Cmd-clic, Maj-clic (plage), case à cocher ; puis archiver, déplacer, corbeille, lu / non lu
sur toutes les conversations choisies."""
from nicegui import run, ui

from mailia import db, sync


def range_ids(ids: list[str], anchor: str | None, target: str) -> list[str]:
    """Identifiants de la plage entre `anchor` et `target` (inclus, dans l'ordre de la liste). Sans repère valide : seulement `target`."""
    if anchor not in ids or target not in ids:
        return [target]
    i, j = sorted((ids.index(anchor), ids.index(target)))
    return ids[i:j + 1]


class BulkMixin:
    # ---------- sélection ----------
    def visible_rows(self) -> list[dict]:
        """Lignes réellement affichées dans la liste (les épinglés repliés n'y sont pas)."""
        rows = list(self.rows or [])
        if db.kv_get("pins_folded") != "0":
            rows = [m for m in rows if not m.get("pinned")]
        return rows

    def _picks_changed(self) -> None:
        self.rows_view.refresh()
        self.list_tools.refresh()

    def pick_toggle(self, m: dict) -> None:
        if m["id"] in self.picked:
            self.picked.pop(m["id"])
        else:
            self.picked[m["id"]] = m
        self.pick_anchor = m["id"]
        self._picks_changed()

    def pick_range(self, m: dict) -> None:
        rows = {r["id"]: r for r in self.visible_rows()}
        for i in range_ids(list(rows), self.pick_anchor, m["id"]):
            self.picked[i] = rows[i]
        self._picks_changed()

    def pick_all(self) -> None:
        for r in self.visible_rows():
            self.picked[r["id"]] = r
        self.select_mode = True
        self._picks_changed()

    def pick_where(self, unread: bool) -> None:
        """Sélectionne les conversations non lues (ou lues) de la liste affichée."""
        self.picked = {r["id"]: r for r in self.visible_rows() if bool(r.get("conv_unread")) == unread}
        self.select_mode = True
        self._picks_changed()

    def pick_clear(self) -> None:
        """Termine la sélection : plus rien de coché, retour au mode normal (un clic ouvre le message)."""
        self.picked.clear()
        self.pick_anchor = None
        self.select_mode = False
        self._picks_changed()

    def toggle_select_mode(self) -> None:
        if self.select_mode:
            return self.pick_clear()
        self.select_mode = True
        self._picks_changed()

    async def set_read_filter(self, value: str) -> None:
        self.read_filter = value
        self.limit = 80
        self.picked.clear()
        self.select_mode = False
        self.refresh_title()
        self.list_tools.refresh()
        await self.reload(force=True)

    async def set_sort(self, oldest: bool) -> None:
        self.oldest_first = oldest
        db.kv_set("list_sort", "oldest" if oldest else "recent")
        self.limit = 80
        self.list_tools.refresh()
        await self.reload(force=True)

    @ui.refreshable_method
    def list_tools(self) -> None:
        """Icône de la liste : filtrer (tous / non lus / lus) et sélectionner plusieurs messages."""
        active = bool(self.read_filter or self.select_mode or self.picked or self.oldest_first)
        with ui.button(icon="filter_list").props("flat dense round size=sm" + (" color=primary" if active else "")).tooltip("Filtrer et sélectionner"):
            with ui.menu():
                ui.label("Afficher").classes("rmeta q-px-md q-pt-sm").style("text-transform:uppercase;letter-spacing:.06em;font-size:11px")
                for key, label in (("", "Tous les messages"), ("unread", "Non lus seulement"), ("read", "Lus seulement")):
                    ui.menu_item(("✓  " if self.read_filter == key else "     ") + label, on_click=lambda _, k=key: self.set_read_filter(k))
                ui.separator()
                ui.label("Trier").classes("rmeta q-px-md q-pt-sm").style("text-transform:uppercase;letter-spacing:.06em;font-size:11px")
                for oldest, label in ((False, "Plus récents d'abord"), (True, "Plus anciens d'abord")):
                    ui.menu_item(("✓  " if self.oldest_first == oldest else "     ") + label, on_click=lambda _, o=oldest: self.set_sort(o))
                ui.separator()
                ui.label("Sélection").classes("rmeta q-px-md q-pt-sm").style("text-transform:uppercase;letter-spacing:.06em;font-size:11px")
                ui.menu_item("Terminer la sélection" if self.select_mode else "Sélectionner des messages", on_click=self.toggle_select_mode)
                ui.menu_item("Tout sélectionner", on_click=self.pick_all)
                ui.menu_item("Sélectionner les non lus", on_click=lambda: self.pick_where(True))
                ui.menu_item("Sélectionner les lus", on_click=lambda: self.pick_where(False))
                if self.picked:
                    ui.menu_item("Tout désélectionner", on_click=self.pick_clear)

    async def row_click(self, m: dict, args: dict | None) -> None:
        a = args or {}
        if a.get("shiftKey") and self.picked:
            return self.pick_range(m)
        if a.get("shiftKey") or a.get("metaKey") or a.get("ctrlKey") or self.picked or self.select_mode:
            return self.pick_toggle(m)
        await self.open(m["id"])

    # ---------- barre d'actions ----------
    def bulk_bar(self) -> None:
        n = len(self.picked)
        with ui.row().classes("bulkbar items-center no-wrap gap-1 w-full"):
            ui.label(f"{n} sélectionné(s)" if n else "Cliquez sur les messages").style("font-weight:700;white-space:nowrap")
            ui.button(icon="select_all", on_click=self.pick_all).props("flat round dense size=sm").tooltip("Tout sélectionner")
            ui.space()
            ui.button(icon="archive", on_click=lambda: self.bulk_act("archive")).props("flat round dense size=sm color=primary").tooltip("Archiver")
            ui.button(icon="drive_file_move", on_click=lambda: self.bulk_act("move")).props("flat round dense size=sm color=primary").tooltip("Déplacer vers…")
            ui.button(icon="mark_email_read", on_click=lambda: self.bulk_act("read")).props("flat round dense size=sm color=primary").tooltip(
                "Marquer comme lus (ou non lus si tout est déjà lu)")
            ui.button(icon="delete", on_click=lambda: self.bulk_act("delete")).props("flat round dense size=sm color=negative").tooltip("Mettre à la corbeille")
            ui.button(icon="close", on_click=self.pick_clear).props("flat round dense size=sm").tooltip("Terminer la sélection (Échap)")

    # ---------- exécution ----------
    @staticmethod
    def _bulk_perform(groups: list[list[dict]], kind: str, dest: str | None) -> tuple[int, str]:
        """Applique l'action conversation par conversation ; une erreur n'arrête pas les suivantes. Retourne (réussies, première erreur)."""
        done, first = 0, ""
        for msgs in groups:
            try:
                sync.perform_many(msgs, kind, dest)
                done += 1
            except Exception as e:
                first = first or str(e)
        return done, first

    async def bulk_act(self, kind: str) -> None:
        picked = list(self.picked.values())
        if not picked:
            return
        groups: dict[str, list[dict]] = {}
        for m in picked:
            groups[m["ckey"]] = self._conversation(m)
        n = len(groups)
        dest = None
        if kind == "move":
            accounts = {m["account"] for m in picked}
            if len(accounts) > 1:
                return ui.notify("Ces messages viennent de plusieurs boîtes : sélectionnez-en d'une seule pour les déplacer.", type="warning", multi_line=True)
            folders = {m["folder"] for m in picked}
            opts = {f["key"]: f["path"] for f in db.folders(next(iter(accounts))) if not (len(folders) == 1 and f["key"] in folders)}
            dest = await self.choose_folder(opts, f"Déplacer {n} conversation(s) vers…")
            if not dest:
                return
        elif kind == "delete":
            with self.host, ui.dialog() as dlg, ui.card():
                ui.label(f"Mettre {n} conversation(s) à la corbeille ?")
                with ui.row().classes("justify-end w-full"):
                    ui.button("Annuler", on_click=lambda: dlg.submit(False)).props("flat no-caps")
                    ui.button("Supprimer", on_click=lambda: dlg.submit(True)).props("unelevated no-caps color=negative")
            if not await dlg:
                return
        elif kind == "read":
            unread = [x for g in groups.values() for x in g if not x["is_read"]]
            if unread:
                work, op = [unread], "read"
            else:
                work, op = [g[-1:] for g in groups.values()], "unread"
            ui.notify("Traitement en cours…")
            done, err = await run.io_bound(self._bulk_perform, work, op, None)
            self.pick_clear()
            await self.reload(force=True)
            return ui.notify("Marqués comme lus" if op == "read" else "Marqués comme non lus", type="positive") if not err else ui.notify(
                f"Action impossible : {err}", type="negative", multi_line=True)
        ui.notify("Traitement en cours…")
        done, err = await run.io_bound(self._bulk_perform, list(groups.values()), kind, dest)
        label = {"archive": "archivée(s)", "delete": "mise(s) à la corbeille", "move": "déplacée(s)"}[kind]
        opened = bool(self.cur) and self.cur.get("ckey") in groups
        self.picked.clear()
        self.pick_anchor = None
        self.select_mode = False
        self.list_tools.refresh()
        if opened:  # la conversation ouverte vient d'être rangée
            self.cur, self.thread = None, []
            self.root.classes(remove="reading")
            self.reader.refresh()
        await self.reload(force=True)
        self.side.refresh()
        if done:
            ui.notify(f"{done} conversation(s) {label}", type="positive")
        if err:
            ui.notify(f"{n - done} conversation(s) n'ont pas pu être traitées : {err}", type="negative", multi_line=True)
