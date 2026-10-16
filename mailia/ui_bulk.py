"""Sélection multiple dans la liste des messages : Ctrl/Cmd-clic, Maj-clic (plage), case à cocher ; puis archiver, déplacer, corbeille, lu / non lu
sur toutes les conversations choisies."""
from nicegui import run, ui

from mailia import config, db, sync, ui_zoom

FILTERS = {"unread": "Non lus", "pinned": "Épinglés", "att": "Avec pièce jointe", "high": "Importance haute", "today": "Aujourd'hui", "week": "7 derniers jours",
           "reply": "Réponse attendue", "urgent": "Urgent"}
AI_FILTERS = {"reply", "urgent"}  # ces deux filtres viennent de l'IA : absents quand elle n'est pas disponible
DATE_FILTERS = {"today", "week"}
SORTS = {"date": "Date", "sender": "Expéditeur", "subject": "Objet"}


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

    # ---------- filtres, tri, affichage de la liste ----------
    def active_filters(self) -> list[str]:
        """Filtres actifs, dans l'ordre du menu (ceux qui viennent de l'IA sont ignorés quand l'IA n'est pas disponible)."""
        return [k for k in FILTERS if k in self.filters and (k not in AI_FILTERS or config.ai_enabled())]

    def view_is_default(self) -> bool:
        return not self.active_filters() and self.sort_by == "date" and not self.oldest_first

    async def _list_changed(self) -> None:
        self.limit = 80
        self.picked.clear()
        self.select_mode = False
        self.refresh_title()
        self.list_tools.refresh()
        await self.reload(force=True)

    async def toggle_filter(self, key: str) -> None:
        if key in self.filters:
            self.filters.discard(key)
        else:
            self.filters.add(key)
            if key in DATE_FILTERS:
                self.filters -= DATE_FILTERS - {key}  # « aujourd'hui » et « 7 derniers jours » s'excluent
        await self._list_changed()

    async def clear_filters(self) -> None:
        self.filters.clear()
        await self._list_changed()

    async def set_sort_by(self, key: str) -> None:
        self.sort_by = key if key in SORTS else "date"
        db.kv_set("list_sort_by", self.sort_by)
        await self._list_changed()

    async def set_sort(self, oldest: bool) -> None:
        self.oldest_first = oldest
        db.kv_set("list_sort", "oldest" if oldest else "recent")
        await self._list_changed()

    async def toggle_conversations(self) -> None:
        config.write_env({"CONVERSATIONS": "0" if config.conversations_enabled() else "1"})
        await self._list_changed()

    async def reset_view(self) -> None:
        self.filters.clear()
        self.sort_by, self.oldest_first = "date", False
        db.kv_set("list_sort_by", "date")
        db.kv_set("list_sort", "recent")
        await self._list_changed()

    def filter_bar(self) -> None:
        """Ligne de pastilles en haut de la liste : ce qui est actif, avec de quoi le retirer d'un clic (comme « Filtré par » d'Apple Mail)."""
        filters = self.active_filters()
        sorted_by = self.sort_by != "date" or self.oldest_first
        if not (filters or sorted_by):
            return
        with ui.row().classes("items-center gap-1 w-full").style("padding:8px 12px 4px;flex-wrap:wrap").mark("filter-bar"):
            ui.label("Filtré par :" if filters else "Trié par :").classes("rmeta")
            for k in filters:
                ui.chip(FILTERS[k], removable=True, on_value_change=None).props("dense color=blue-1 text-color=blue-10").on(
                    "remove", lambda _, k=k: self.toggle_filter(k)).mark(f"chip-{k}")
            if sorted_by:
                ui.chip(self.sort_summary(), removable=True).props("dense color=grey-3 text-color=grey-9").on(
                    "remove", lambda _: self.reset_sort()).mark("chip-sort")
            if len(filters) + bool(sorted_by) > 1:
                ui.button("Tout effacer", on_click=self.reset_view).props("flat dense no-caps size=sm color=primary")

    async def reset_sort(self) -> None:
        self.sort_by, self.oldest_first = "date", False
        db.kv_set("list_sort_by", "date")
        db.kv_set("list_sort", "recent")
        await self._list_changed()

    def sort_summary(self) -> str:
        if self.sort_by == "date":
            return "Plus anciens d'abord" if self.oldest_first else "Plus récents d'abord"
        return f"{SORTS[self.sort_by]} : " + ("Z → A" if not self.oldest_first else "A → Z")

    @ui.refreshable_method
    def list_tools(self) -> None:
        """Trois boutons à côté de la recherche : filtrer (cumulable), affichage (tri, conversations, densité), sélection de plusieurs messages."""
        filters = self.active_filters()
        head = lambda text: ui.label(text).classes("rmeta q-px-md q-pt-sm").style("text-transform:uppercase;letter-spacing:.06em;font-size:11px")
        mark = lambda on: ("✓  " if on else "     ")
        with ui.button(icon="filter_alt").props("flat dense round size=sm" + (" color=primary" if filters else "")).tooltip("Filtrer").mark("filter-menu"):
            with ui.menu():
                head("Filtrer")
                for k, label in FILTERS.items():
                    if k in AI_FILTERS and not config.ai_enabled():
                        continue
                    if k in ("today", "reply"):  # trois groupes : état du message, période, et ce que l'IA a repéré
                        ui.separator()
                    ui.menu_item(mark(k in filters) + label, on_click=lambda _, k=k: self.toggle_filter(k)).mark(f"filter-{k}")
                if filters:
                    ui.separator()
                    ui.menu_item("Effacer les filtres", on_click=self.clear_filters).mark("filter-clear")
        sort_active = self.sort_by != "date" or self.oldest_first
        with ui.button(icon="tune").props("flat dense round size=sm" + (" color=primary" if sort_active else "")).tooltip("Affichage").mark("display-menu"):
            with ui.menu():
                head("Trier par")
                for k, label in SORTS.items():
                    ui.menu_item(mark(self.sort_by == k) + label, on_click=lambda _, k=k: self.set_sort_by(k)).mark(f"sort-{k}")
                ui.separator()
                by_date = self.sort_by == "date"
                for oldest, label in ((False, "Plus récents en haut" if by_date else "Z → A"), (True, "Plus anciens en haut" if by_date else "A → Z")):
                    ui.menu_item(mark(self.oldest_first == oldest) + label, on_click=lambda _, o=oldest: self.set_sort(o)).mark("order-old" if oldest else "order-new")
                ui.separator()
                head("Affichage")
                ui.menu_item(mark(config.conversations_enabled()) + "Regrouper en conversations", on_click=self.toggle_conversations).mark("display-threads")
                ui.menu_item(mark(ui_zoom.compact()) + "Liste compacte (plus de mails à l'écran)", on_click=lambda: self.set_density(not ui_zoom.compact())).mark("display-compact")
                ui.separator()
                ui.menu_item("Réinitialiser l'affichage", on_click=self.reset_view).mark("display-reset")
        picking = self.select_mode or bool(self.picked)
        ui.button(icon="checklist", on_click=self.toggle_select_mode).props("flat dense round size=sm" + (" color=primary" if picking else "")).tooltip(
            "Terminer la sélection" if picking else "Sélectionner des messages").mark("select-toggle")

    def set_density(self, compact: bool) -> None:
        ui_zoom.set_compact(compact)
        self.list_tools.refresh()  # les coches du menu

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
            with ui.button(icon="checklist").props("flat round dense size=sm").tooltip("Sélectionner…"):
                with ui.menu():
                    ui.menu_item("Tous les messages", on_click=self.pick_all)
                    ui.menu_item("Les non lus", on_click=lambda: self.pick_where(True))
                    ui.menu_item("Les lus", on_click=lambda: self.pick_where(False))
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
