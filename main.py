"""Velora : client mail avec IA, appli de bureau (Mac / Windows) en Python pur.

    python main.py            fenêtre native
    python main.py --server   serveur réseau (pour l'ouvrir depuis le téléphone), protégé par jeton
"""
import multiprocessing
import os
import sys
import threading
import time

_IS_CHILD = multiprocessing.current_process().name != "MainProcess" or any(
    a.startswith("--multiprocessing-fork") for a in sys.argv)  # dans l'appli empaquetée, le fils ne se connaît pas encore au chargement
if _IS_CHILD and not sys.platform.startswith("win"):
    # Processus fils (fenêtre native, ouvriers) : ils se terminent seuls si l'appli principale disparaît, même tuée
    # de force (leur parent devient alors le processus 1). Sans cela, la fenêtre resterait ouverte, orpheline.
    _ppid = os.getppid()

    def _watch_parent() -> None:
        while True:
            time.sleep(1.5)
            if os.getppid() != _ppid:
                os._exit(0)

    threading.Thread(target=_watch_parent, daemon=True).start()


def _native_context_menu() -> None:
    """Exécuté dans le processus de la fenêtre native (macOS : menu clic droit et barre de menus ; partout : jalon de démarrage)."""
    from mailia import native_menu
    native_menu.setup()


if __name__ == "__main__":
    # Appli empaquetée : la fenêtre native est un second lancement de ce même programme. Il doit prendre la main ICI, avant les imports
    # lourds (exchangelib, IA…) qu'il n'utilise pas : sinon la fenêtre attend ~2 s de plus, et ce processus ouvre la base et lance des synchros en double.
    multiprocessing.freeze_support()

import asyncio
import atexit
import json
import logging
import re
import signal
import subprocess
import webbrowser
from urllib.parse import unquote, urlparse
from datetime import datetime

if "--mcp-stdio" in sys.argv:  # pont pour les assistants qui lancent un programme local (Claude Desktop) : aucune interface, aucune base
    from mailia.mcpbridge import run as _bridge
    _bridge(sys.argv)
    sys.exit(0)

from mailia import perf  # en premier : le chronomètre de démarrage part d'ici
from mailia import nicegui_tweaks
nicegui_tweaks.install()
from mailia import config as _config  # léger : sert à ranger le stockage de session de l'interface dans le dossier de données
os.environ.setdefault("NICEGUI_STORAGE_PATH", str(_config.DATA_DIR / ".nicegui"))

from nicegui import app, background_tasks, native, run, ui

from mailia import ai, aipolicy, apiserver, compose, config, db, folder_order, folder_tree, htmlmail, i18n, logs, notify, queries, search, security, settings_ui, sync, vault, wizard
from mailia.textutil import for_ai
from mailia.ui_account import friendly_error
from mailia.ui_agenda import AgendaMixin
from mailia.ui_apple import AppleWriterMixin
from mailia import ui_pet
from mailia.ui_feed import FeedMixin
from mailia.ui_server_rules import ServerRulesMixin
from mailia.ui_assets import CSS, SWIPE_JS
from mailia import apple_writer, icons, weather
from mailia.brand import logo_big, logo_img
from mailia.ui_bulk import BulkMixin
from mailia.ui_cleanup import CleanupMixin
from mailia.ui_common import CAT, JOURS_LONG, MOIS, NOLIST_VIEWS, SERVER_MODE, VIEWS, ago, full_date, human_size, initial_view, initials, label_of, log, short_date
from mailia.ui_compose_ai import ComposeAiMixin
from mailia.ui_dictation import DictationMixin
from mailia.ui_drafts import DraftsMixin
from mailia.ui_files import FilesMixin
from mailia.ui_lock import lock_applies, lock_screen_now, render_db_error, render_lock_screen, session_unlocked
from mailia.ui_mailtools import MailToolsMixin
from mailia.ui_outbox import OutboxMixin
from mailia.ui_scan import ScanMixin
from mailia.ui_search import SearchMixin
from mailia.ui_settings import open_settings
from mailia.ui_tasks import TasksMixin
from mailia.ui_update import auto_check
from mailia import ui_weather
from mailia.ui_weather import WeatherMixin
from mailia import ui_zoom

perf.mark("modules chargés")
perf.install()































class Mailbox(BulkMixin, ScanMixin, CleanupMixin, SearchMixin, FilesMixin, OutboxMixin, TasksMixin, MailToolsMixin, ComposeAiMixin, DraftsMixin, DictationMixin, AppleWriterMixin, WeatherMixin, AgendaMixin, FeedMixin, ServerRulesMixin):
    """État et interface d'une fenêtre (chaque client a la sienne)."""

    def __init__(self) -> None:
        self.view, self.account, self.folder, self.q, self.limit = initial_view() + ("", 80)
        self.cur: dict | None = None
        self.rows: list[dict] = []
        self.brief: dict | None = None
        self.counts: dict = queries.counts()
        self.ver = queries.version()
        self._build_cost, self._last_bg, self._full_at = 0.0, 0.0, 0.0
        self.remote_ids: set[str] | None = None  # mails trouvés par les serveurs pour la recherche en cours
        self.remote_for, self.remote_busy = "", False
        self._remote_cache: dict = {}
        self.hl: list[str] = []
        self._more_busy = False
        self._folder_paths: dict[tuple, str] = {}
        self._row_els: dict[str, ui.element] = {}  # id du mail -> élément de sa ligne dans la liste (pour ne redessiner que les lignes modifiées)
        self.rows_sig = None
        self.busy = False
        self.compose = False
        self.thread: list[dict] = []  # messages de la conversation ouverte (vide : un seul mail)
        self.expanded: set[str] = set()  # messages dépliés dans la conversation
        self.full: set[str] = set()  # messages dont on affiche le texte complet (citations comprises)
        self.plain: set[str] = set()  # messages affichés en texte brut plutôt qu'en HTML
        self.images: set[str] = set()  # messages dont l'utilisateur a autorisé les images distantes
        self.cids: dict[str, dict] = {}  # message -> {cid: url data:} des images incrustées
        self.rep_folder = ""  # dossier d'où la conversation a été ouverte : c'est là que s'appliquent archiver / supprimer
        self.tsum: dict[str, str] = {}
        self.new = False  # True : formulaire « nouveau message » affiché à la place d'un mail
        self.draft = {"account": "", "to": "", "cc": "", "bcc": "", "subject": "", "body": "", "ins": "", "tone": "neutre",
                      "files": [], "quote": True, "html": "", "gen": False, "id": "", "reply_to": None, "importance": 1}
        self.drafts: list[dict] = []
        self.outbox: list[dict] = []
        self.snoozed: list[dict] = []
        self.followups: list[dict] = []
        self.tasks: list[dict] = []
        self.show_done = False
        self.translations: dict[str, str] = {}
        self._watch: dict[str, str] = {}  # messages mis en file d'envoi depuis cette fenêtre : id -> objet
        self._pins: set = set()
        self.picked: dict[str, dict] = {}  # sélection multiple : id -> ligne
        self.pick_anchor: str | None = None
        self.select_mode = False  # mode « sélection » : un clic sur un message le coche au lieu de l'ouvrir
        self.filters: set[str] = set()  # filtres cumulables de la liste (voir ui_bulk.FILTERS) ; remis à zéro quand on change de dossier
        self.oldest_first = db.kv_get("list_sort") == "oldest"  # ordre du tri : récents d'abord (défaut) ou anciens d'abord (A → Z pour un tri par nom)
        self.sort_by = db.kv_get("list_sort_by") if db.kv_get("list_sort_by") in ("sender", "subject") else "date"
        self.suggestions: dict = {}  # message -> réponses suggérées par l'IA (à la demande) ou "loading"
        self._saved_sig = None  # empreinte du dernier brouillon enregistré (évite de réécrire sans changement)
        self._files_sig = None
        self.save_label = None
        self.subject_input = None
        self.rcpt_add: dict = {}  # champs de destinataires du message en cours : fonctions d'ajout (À, Cc, Cci)
        self.bcc_field = None

    # ---------- construction ----------
    def build(self) -> None:
        cols = json.loads(db.kv_get("cols") or "{}")
        side_w, list_w = int(cols.get("side", 240)), int(cols.get("list", 400))
        with ui.element("div").classes("root").style(
                f"--w-side:{side_w}px;--w-list:{list_w}px").on(
                "colresize", lambda e: self.save_cols(e.args), args=["detail"]).on(
                "sidestate", lambda e: db.kv_set("side_hidden", "1" if (e.args or {}).get("detail", {}).get("hidden") else "0"),
                args=["detail"]).on("agslot", lambda e: self.agenda_slot(e.args), args=["detail"]).on("agitem", lambda e: self.agenda_item(e.args), args=["detail"]) as self.root:
            ui.element("div").classes("sidehot")
            ui.element("div").classes("resizer r1")
            ui.element("div").classes("resizer r2")
            with ui.column().classes("side gap-0"):
                self.side()
            with ui.column().classes("listcol gap-0"):
                with ui.column().classes("lhead gap-1"):
                    with ui.row().classes("items-center no-wrap gap-2"):
                        self.title_icon = ui.html("", sanitize=False).style("display:none")  # icône devant le titre (brief du jour)
                        self.title = ui.label().classes("title")
                    self.subtitle = ui.label().classes("subtitle")  # sous le titre du brief : la date et ce qui attend
                    self.ephem = ui.html("", sanitize=False)  # puis l'éphéméride : lune, lever et coucher du soleil
                    self.search_row = ui.row().classes("w-full items-center no-wrap gap-1")
                    with self.search_row:
                        self.search = ui.input(placeholder="Rechercher dans les mails…").props("dense outlined clearable debounce=300").classes("col")
                        self.search.on_value_change(lambda e: self.set_query(e.value or ""))
                        self.list_tools()
                        ui.button(icon="bookmark_add", on_click=self.save_search).props("flat dense round size=sm").tooltip(
                            "Enregistrer cette recherche dans la colonne de gauche")
                    self.chips()
                with (rows_area := ui.scroll_area().classes("rows")):
                    self.rows_view()
                rows_area.on("scroll", self.rows_scrolled, args=["verticalPosition", "verticalContainerSize", "verticalSize"], throttle=0.3)
            with ui.column().classes("readercol gap-0"):
                self.reader()
        ui_pet.apply()
        ui.timer(1.0, ui_pet.apply, once=True)  # au cas où le script du chat finit de se charger après la page
        ui.run_javascript("mailiaSideInit(%s, %s)" % (
            "true" if db.kv_get("side_hidden") == "1" else "false", "true" if db.kv_get("side_auto") == "1" else "false"))
        self.host = ui.element("div")  # support stable des fenêtres (jamais rafraîchi)
        with ui.footer().classes("lt-md bg-transparent q-pa-none").props("elevated=false"):
            with ui.row().classes("w-full justify-around items-center").style("background:var(--surface);border-top:1px solid var(--line)"):
                for v, txt in (("brief", "Brief"), ("inbox", "Boîte"), ("todo", "À traiter")):
                    if v != "inbox" and not config.ai_enabled():  # le brief et « À traiter » viennent de l'IA : masqués sans clé
                        continue
                    ui.button(txt, icon=None, on_click=lambda v=v: self.go(v, "")).props("flat no-caps dense")
                ui.button("↻", on_click=self.do_sync).props("flat dense")
        self.refresh_title()

    def folded(self) -> set[str]:
        """Boîtes dont les dossiers sont repliés dans la colonne de gauche."""
        try:
            return set(json.loads(db.kv_get("side_fold") or "[]"))
        except ValueError:
            return set()

    def folder_dropped(self, args: dict | None, acct: str, target: str) -> None:
        """Un dossier a été déposé avant ou après `target` (même niveau) : l'ordre est mémorisé pour cette boîte."""
        d = (args or {}).get("detail") or {}
        new = folder_order.move(db.folders(acct), folder_order.load(acct), d.get("from", ""), target, bool(d.get("after")))
        if new is None:
            return
        folder_order.save(acct, new)
        self.side.refresh()

    def folders_folded(self) -> set[str]:
        """Dossiers dont les sous-dossiers sont repliés (« boîte|clé »)."""
        try:
            return set(json.loads(db.kv_get("folder_fold") or "[]"))
        except ValueError:
            return set()

    def toggle_folder_fold(self, acct: str, key: str) -> None:
        cur = self.folders_folded()
        cur.symmetric_difference_update({f"{acct}|{key}"})
        db.kv_set("folder_fold", json.dumps(sorted(cur)))
        self.side.refresh()

    def toggle_fold(self, acct: str) -> None:
        cur = self.folded()
        cur.symmetric_difference_update({acct})
        db.kv_set("side_fold", json.dumps(sorted(cur)))
        self.side.refresh()

    # ---------- barre latérale ----------
    @ui.refreshable_method
    def side(self) -> None:
        c = self.counts
        use_ai = config.ai_enabled()
        def item(view: str, acc: str, icon: str, label: str, cnt: int | str = 0, hot: bool = False,
                 folder: str = "", depth: int = 0, manage: bool = False, drop: str = "", total: int | None = None, fold: bool | None = None,
                 fparent: str | None = None, ffold: bool | None = None, fkey: str = "", tip: str = "", extra: int = 0) -> None:
            on = self.view == view and self.account == acc and self.folder == folder
            el = ui.element("div").classes("nav" + (" on" if on else "") + (" sub" if depth else "") + (" ghost" if view == "ghost" else ""))
            if view == "header":  # en-tête d'une boîte qui a des sous-dossiers : replie ou déplie (la boîte de réception est sa première ligne)
                el.on("click", lambda _, a=acc: self.toggle_fold(a))
            elif view == "ghost":  # dossier parent non suivi, montré pour garder la hiérarchie : il ne s'ouvre pas
                el.on("click", lambda _, a=acc, k=fkey: self.toggle_folder_fold(a, k))
            else:
                el.on("click", lambda _, v=view, a=acc, f=folder: self.go(v, a, f))
            if depth > 1:  # sous-dossier (ex : les années dans Archive) : décalé d'un cran par niveau, sous son parent ; les dossiers de premier niveau restent alignés
                el.style(f"padding-left:{10 + 18 * (depth - 1)}px")
            if fparent is not None:  # un dossier : on peut le glisser avant ou après un autre dossier du même niveau
                el.classes("navfolder")
                el._props.update({"draggable": "true", "data-acct": acc, "data-fkey": folder, "data-parent": fparent})
                el.on("folderdrop", lambda e, a=acc, k=folder: self.folder_dropped(e.args, a, k), args=["detail"])
            if drop:  # cette ligne accepte un mail déposé : il est déplacé dans ce dossier
                el.classes("droptarget")
                el._props.update({"data-acct": acc, "data-folder": drop})
                el.on("dropmail", lambda e, a=acc, d=drop: self.dropped(e.args, a, d), args=["detail"])
            with el:
                if fold is not None:  # petite flèche : replie ou déplie les dossiers de la boîte
                    ui.icon("expand_more" if fold else "chevron_right", size="18px").style("color:var(--muted);margin-right:-6px").on(
                        "click.stop", lambda _, a=acc: self.toggle_fold(a)).tooltip("Replier les dossiers" if fold else "Déplier les dossiers").mark(f"fold-{acc}")
                if fparent is not None or view == "ghost" or fkey == "INBOX":  # ligne de l'arbre : la flèche (sous-dossiers) puis l'icône ; sans sous-dossiers, une place vide garde les icônes alignées
                    if ffold is not None:
                        ui.icon("expand_more" if ffold else "chevron_right", size="18px").style("color:var(--muted);cursor:pointer;width:18px;margin-right:-6px").on(
                            "click.stop", lambda _, a=acc, k=(fkey or folder): self.toggle_folder_fold(a, k)).tooltip(
                            "Replier les sous-dossiers" if ffold else "Déplier les sous-dossiers").mark(f"ffold-{acc}-{fkey or folder}")
                    else:
                        ui.element("span").style("width:18px;flex:none;margin-right:-6px")
                if icon == "logo":  # le logo Velora devant chaque boîte
                    ui.html(logo_img(20), sanitize=False).classes("ic").style("display:flex;justify-content:center")
                elif icons.name_of(icon):  # icône dessinée (style Apple Mail) ; un ancien glyphe est converti
                    ui.html(icons.svg(icons.name_of(icon)), sanitize=False).classes("ic").style("display:flex;justify-content:center" + (";opacity:.55" if view == "ghost" else ""))
                else:
                    ui.label(icon).classes("ic")
                ui.label(label).classes("lbl")
                if manage:
                    ui.button(icon="create_new_folder").props("flat dense round size=xs").tooltip(
                        "Choisir les dossiers affichés").on("click.stop", lambda _, a=acc: self.folders_dialog(a))
                if total is not None:  # « non lus / total » : le total est celui de la liste affichée
                    if total or cnt:
                        ui.label(f"{cnt or 0} / {total}" if total else str(cnt)).classes("cnt" + (" hot" if hot and cnt else "") + ("" if cnt else " zero"))
                    if extra:  # non lus cachés dans les sous-dossiers d'un dossier replié : « +12 », à côté du décompte du dossier lui-même
                        ui.label(f"+{extra}").classes("cnt hot").style("margin-left:4px").tooltip(tip)
                elif cnt:
                    lbl = ui.label(str(cnt)).classes("cnt" + (" hot" if hot else ""))
                    if tip:
                        lbl.tooltip(tip)

        with ui.element("div").classes("sidetop"):  # en-tête fixe
            with ui.element("div").classes("sidehead"):
                ui.html(f'<div class="brand">{logo_img(42)}<span>Velora</span></div>', sanitize=False)
                ui.button(icon="keyboard_double_arrow_left").props("flat dense round size=sm").classes("sidehidebtn").on(
                    "click", js_handler="() => mailiaSide('hide')").tooltip("Masquer la colonne (la souris au bord gauche la rouvre)")
                ui.button(icon="push_pin").props("flat dense round size=sm color=negative").classes("sidepinbtn").on(
                    "click", js_handler="() => mailiaSide('dock')").tooltip("Garder la colonne ouverte")
            ui.button("Nouveau message", icon="edit", on_click=self.new_message).props("unelevated no-caps color=primary").classes("q-mx-sm q-mb-sm")
        with ui.scroll_area().classes("sidescroll"):  # seule cette partie défile : l'en-tête et les boutons du bas restent en place
            if use_ai:
                item("brief", "", "✦", "Brief du jour", "…" if c["pending"] else "")
            ui.label("Boîtes").classes("grp")
            item("inbox", "", "svg:tray2", "Toutes les boîtes", c["unread"], True, total=c["tot_all"])
            folded = self.folded()
            ffolded = self.folders_folded()
            for key, cfg in sync.ACCOUNTS.items():
                selected = self.folder if (self.view == "folder" and self.account == key) else ""
                nodes = folder_tree.build(db.folders(key), folder_order.load(key), ffolded, selected, key)
                if not any(n["key"] != "INBOX" for n in nodes):  # une boîte sans sous-dossiers : la ligne de la boîte est sa boîte de réception
                    item("inbox", key, "logo", cfg.label, c["accounts"].get(key, 0), manage=True, drop="INBOX", total=c["tot_accounts"].get(key, 0))
                    continue
                item("header", key, "logo", cfg.label, manage=True, fold=key not in folded)  # en-tête de la boîte : la boîte de réception est la ligne suivante
                if key in folded:
                    continue
                for n in nodes:
                    ffold = n["open"] if n["kids"] else None
                    hidden = sum(c["folders"].get(f"{key}|{k}", 0) for k in n["below"]) if (n["kids"] and not n["open"]) else 0  # non lus des sous-dossiers repliés
                    tip = f"{hidden} non lu(s) dans les sous-dossiers" if hidden else ""
                    if n["ghost"]:
                        item("ghost", key, "▸", n["name"], depth=n["depth"] + 1, ffold=ffold, fkey=n["key"], tip=tip, extra=hidden)
                    elif n["key"] == "INBOX":
                        item("inbox", key, "✉", n["name"], c["accounts"].get(key, 0), True, drop="INBOX", depth=n["depth"] + 1, ffold=ffold,
                             total=c["tot_accounts"].get(key, 0), tip=tip, fkey="INBOX", extra=hidden)
                    else:
                        item("folder", key, "svg:" + icons.ROLES.get(n["role"], "folder"), n["name"], c["folders"].get(f"{key}|{n['key']}", 0), folder=n["key"], depth=n["depth"] + 1, drop=n["key"],
                             total=c["tot_folders"].get(f"{key}|{n['key']}", 0), fparent=n["parent"], ffold=ffold, tip=tip, extra=hidden)
            ui.label("Vues").classes("grp")
            if use_ai:
                item("todo", "", "⚑", "À traiter", c["todo"], True, total=c["tot_todo"])
            item("unread", "", "●", "Non lus", c["unread"])
            if c.get("drafts") or self.view == "drafts":  # seulement les brouillons pas encore copiés sur le serveur (hors ligne, assistants) : les autres sont dans le dossier Brouillons de leur boîte
                item("drafts", "", "✎", "Brouillons à synchroniser", c.get("drafts", 0))
            if c.get("outbox") or self.view == "outbox":
                item("outbox", "", "⏰", "En attente d'envoi", c.get("outbox", 0), bool(c.get("outbox")))
            item("tasks", "", "☑", "Tâches", c.get("tasks", 0))
            if self.srules_accounts():
                item("srules", "", "▽", "Règles de tri", 0)
            if c.get("snoozed") or self.view == "snoozed":
                item("snoozed", "", "⏳", "Reportés", c.get("snoozed", 0))
            if c.get("followups") or self.view == "followups":
                item("followups", "", "↩", "À relancer", c.get("followups", 0), True)
            with ui.element("div").classes("nav").on("click", lambda: self.open_contacts()):
                ui.html(icons.svg("people"), sanitize=False).classes("ic").style("display:flex;justify-content:center")
                ui.label("Contacts").classes("lbl")
            if use_ai:
                with ui.element("div").classes("nav").on("click", lambda: self.ask_dialog()).mark("ask"):
                    ui.html(icons.svg("sparkle"), sanitize=False).classes("ic").style("display:flex;justify-content:center")
                    ui.label("Demander à ma boîte").classes("lbl")
            with ui.element("div").classes("nav").on("click", lambda: self.cleanup_dialog()):
                ui.html(icons.svg("broom"), sanitize=False).classes("ic").style("display:flex;justify-content:center")
                ui.label("Nettoyer ma boîte").classes("lbl")
            if use_ai:
                item("newsletter", "", "☰", "Newsletters", c["newsletter"], total=c["tot_newsletter"])
                item("notification", "", "ⓘ", "Notifications", c["notification"], total=c["tot_notification"])
            saved = self.saved_searches()
            if saved:
                ui.label("Recherches").classes("grp")
                for s in saved:
                    with ui.element("div").classes("nav").on("click", lambda _, q=s["q"]: self.run_saved_search(q)):
                        ui.label("⌕").classes("ic")
                        ui.label(s["name"]).classes("lbl").tooltip(s["q"])
                        ui.button(icon="close").props("flat dense round size=xs").tooltip("Supprimer cette recherche").on(
                            "click.stop", lambda _, q=s["q"]: self.delete_saved_search(q))
            # panneau qui n'apparaît que pendant un glisser-déposer : Archive, Corbeille et « autre dossier » du compte concerné
            with ui.element("div").classes("droppanel"):
                for key, cfg in sync.ACCOUNTS.items():
                    grp = ui.element("div").classes("dp-group")
                    grp._props["data-acct"] = key
                    grp.style("display:none")
                    rows = db.folders(key)
                    trash = next((f["key"] for f in rows if f["role"] == "trash"), None)
                    shown = {f["key"] for f in rows if f["sync"]}
                    arch = sync.archive_folder(key)
                    with grp:
                        ui.label(f"Déposer dans — {cfg.label}").classes("dp-title")
                        for label, dest, icon in (("Archive", arch if arch not in shown else None, "archive"),
                                                  ("Corbeille", trash, "delete"), ("Autre dossier…", "__other__", "folder_open")):
                            if not dest:
                                continue
                            t = ui.element("div").classes("dp-item droptarget")
                            t._props.update({"data-acct": key, "data-folder": dest})
                            t.on("dropmail", lambda e, a=key, d=dest: self.dropped(e.args, a, d), args=["detail"])
                            with t:
                                ui.icon(icon, size="20px").style("color:var(--muted)")
                                ui.label(label)
        status = f"{c['pending']} à analyser" if (c["pending"] and use_ai) else ""
        ui.label(status).classes("rmeta q-px-sm")
        with ui.row().classes("gap-2 q-px-sm"):
            ui.button(icon="sync", on_click=self.do_sync).props(f"flat dense round size=sm{' loading' if self.busy else ''}").tooltip("Synchroniser")
            auto = db.kv_get("side_auto") == "1"
            ui.button(icon="vertical_split", on_click=self.toggle_side_auto).props(
                "round dense size=sm color=primary " + ("unelevated" if auto else "flat")).tooltip(
                "Masquer la colonne automatiquement")
            ui.button(icon="settings", on_click=self.settings).props("flat dense round size=sm").tooltip("Paramètres")
            if lock_applies() and vault.lock_enabled(db):
                ui.button(icon="lock", on_click=lock_screen_now).props("flat dense round size=sm").tooltip("Verrouiller maintenant")

    @ui.refreshable_method
    def chips(self) -> None:
        with ui.row().classes("lt-md gap-1"):
            for key, name in [("", "Toutes"), *[(k, c.label) for k, c in sync.ACCOUNTS.items()]]:
                ui.button(name, on_click=lambda _, k=key: self.set_account(k)).props(
                    f"{'unelevated color=primary' if self.account == key else 'outline'} dense no-caps size=sm")

    # ---------- liste ----------
    def refresh_title(self) -> None:
        if self.view == "folder":
            fo = next((f for f in db.folders(self.account) if f["key"] == self.folder), None)
            name = f"{label_of(self.account)} · {fo['path'] if fo else 'Dossier'}"
        else:
            name = label_of(self.account) if self.account and self.view == "inbox" else VIEWS[self.view][1]
        self.title.set_text(name + (" · tous les dossiers" if self.search_everywhere() else "")
                            + (" · recherche sur le serveur…" if self.remote_busy else ""))
        self.search_row.set_visibility(self.view not in NOLIST_VIEWS)
        brief = self.view == "brief"
        self.title_icon.set_content(icons.svg("sparkle", 24) if brief else "")
        self.title_icon.style("display:flex;color:var(--accent)" if brief else "display:none")
        now = datetime.now()
        self.subtitle.set_text(f"{JOURS_LONG[now.weekday()].capitalize()} {now.day} {MOIS[now.month - 1]}" if brief else "")
        self.subtitle.set_visibility(brief)
        self.ephem.set_content(ui_weather.ephemeris_html(datetime.now().astimezone(), weather.cached_summary()) if brief else "")
        self.ephem.set_visibility(brief)

    async def restore_open(self) -> None:
        """Fenêtre rechargée en cours de travail (moins de 10 minutes après la dernière ouverture d'un message) : on rouvre ce message."""
        if self.view == "brief":
            return
        try:
            o = json.loads(db.kv_get("ui_open") or "{}")
        except ValueError:
            return
        if o.get("id") and time.time() - float(o.get("t") or 0) < 600 and queries.get_message(o["id"]):
            await self.open(o["id"])

    def remember_view(self) -> None:
        db.kv_set("ui_last", json.dumps({"view": self.view, "account": self.account, "folder": self.folder}))

    def brief_card(self, b: dict, keep) -> None:
        """Brief du jour : une phrase, puis « À faire en priorité » et « À suivre », chaque point ouvrant son mail."""
        sm = b.get("summary") or {}
        by_id = {x["id"]: x for k in ("todo", "info", "noise") for x in b[k]}
        # un point de la synthèse dont le mail n'est plus à traiter (lu, archivé, supprimé, reporté) disparaît tout de suite, sans attendre une nouvelle synthèse de l'IA
        with ui.element("div").classes("brief"):
            self.weather_card()
            ui.label(sm.get("intro", "")).classes("brief-intro")
            if sm.get("intro", "").startswith("Synthèse indisponible"):
                ui.button("Réessayer", icon="refresh", on_click=self.retry_brief).props("flat dense no-caps size=sm color=primary")
            for key, title, kind in (("urgent", "À faire en priorité", " hi"), ("waiting", "À suivre", "")):
                pts = [p for p in (sm.get(key) or []) if p["id"].split("|", 1)[0] in sync.ACCOUNTS and p["id"] in by_id and keep(by_id[p["id"]])]
                if not pts:
                    continue
                ui.label(title).classes("brief-sec")
                for p in pts:
                    m = by_id.get(p["id"])
                    with ui.element("div").classes("bitem").on("click", lambda _, i=p["id"]: self.open(i)):
                        ui.element("div").classes("bdot" + kind)
                        with ui.element("div").classes("btext"):
                            ui.label(p["text"])
                            if m:
                                ui.label(f"{m['sender_name'] or m['sender']} · {ago(m['date'])}").classes("bmeta")
            if sm.get("info"):
                ui.label(sm["info"]).classes("brief-info")
            pend = self.counts["pending"]
            ui.label(f"{self.counts['unread']} non lus" + (f" · {pend} en cours d'analyse" if pend else "")).classes("bmeta q-mt-sm")

    def row(self, m: dict) -> None:
        sel = " sel" if self.cur and self.cur.get("ckey") == m.get("ckey") else ""
        n = m.get("conv_n", 1)
        unread = bool(m.get("conv_unread")) if "conv_unread" in m else not m["is_read"]
        what = "Lu" if unread else "Non lu"
        wrap = ui.element("div").classes("swrap" + (" selmode" if (self.picked or self.select_mode) else "")).on("swipe", lambda e, m=m: self.swiped(m, e.args), args=["detail"])
        wrap._props.update({"draggable": "true", "data-id": m["id"], "data-acct": m["account"], "data-folder": m["folder"]})
        self._row_els[m["id"]] = wrap
        with wrap:
            with ui.element("div").classes("swl"):  # révélé en glissant vers la droite
                ui.icon("drafts" if unread else "mark_email_unread", size="22px")
                ui.label("Marquer " + what.lower())
            with ui.element("div").classes("swr"):  # révélé en glissant vers la gauche
                ui.button("Archiver", icon="archive", on_click=lambda _, m=m: self.swipe_act(m, "archive")).props("unelevated square stack no-caps color=primary").classes("swb")
                ui.button("Supprimer", icon="delete", on_click=lambda _, m=m: self.swipe_act(m, "delete")).props("unelevated square stack no-caps color=negative").classes("swb")
            picked = m["id"] in self.picked
            with ui.element("div").classes(f"mrow{'' if unread else ' read'}{sel}{' picked' if picked else ''}").on(
                    "click", lambda e, m=m: self.row_click(m, e.args), args=["metaKey", "ctrlKey", "shiftKey"]).mark(f"row-{m['id']}"):
                ui.element("div").classes("dot")
                with ui.element("div").classes("rb"):
                    with ui.element("div").classes("top"):
                        self.marked(("★ " if aipolicy.is_vip(m["sender"]) else "") + (m["sender_name"] or m["sender"]), "from")
                        with ui.element("div").classes("right"):
                            if n > 1:
                                ui.label(str(n)).classes("convn").tooltip(f"{n} messages dans la conversation")
                            if m.get("woke"):
                                ui.icon("alarm", size="14px").style("color:var(--accent)").tooltip("Message reporté : c'est l'heure de le revoir")
                            if m.get("pinned"):
                                ui.icon("push_pin", size="14px").style("color:#d92d3a").tooltip("Épinglé en haut")
                            if m.get("importance") == 2:
                                ui.icon("priority_high", size="16px").style("color:#d92d3a;font-weight:700").tooltip("Importance haute : l'expéditeur a marqué ce message important")
                            elif m.get("importance") == 0:
                                ui.icon("arrow_downward", size="14px").style("color:var(--muted)").tooltip("Importance basse")
                            if m.get("verb"):
                                ui.icon("forward" if m["verb"] == 2 else "reply", size="14px").style("color:var(--muted)").tooltip(
                                    "Vous avez transféré ce message" if m["verb"] == 2 else "Vous avez répondu à ce message")
                            ui.label(("📎 " if m["has_att"] else "") + ago(m["date"])).classes("date")
                    self.marked(m["subject"], "subj")
                    use_ai = config.ai_enabled()
                    if self.hl and m.get("snip"):  # recherche : l'extrait du texte où figure le mot (au lieu du résumé de l'IA)
                        self.marked(m["snip"], "sum snip")
                    elif m["summary"] and use_ai:
                        self.marked(m["summary"], "sum")
                    with ui.element("div").classes("badges"):
                        if use_ai and m["priority"] == 3:
                            ui.label("Urgent").classes("badge p3")
                        if use_ai and m["needs_reply"]:
                            ui.label("Réponse attendue").classes("badge rep")
                        if use_ai and m["category"]:
                            ui.label(CAT.get(m["category"], m["category"])).classes("badge")
                        ui.label(label_of(m["account"])).classes("badge acc")
                        if self.q.strip() and m["folder"] != (self.folder if self.view == "folder" else "INBOX"):  # résultat venu d'un autre dossier : on dit lequel
                            path = self._folder_paths.get((m["account"], m["folder"]), "")
                            ui.label(path.rsplit("/", 1)[-1] or m["folder"]).classes("badge").tooltip(path or m["folder"])
                        if m["folder"] == "INBOX" and security.quick_row(m):
                            ui.label("⚠ Suspect").classes("badge p3").tooltip("Ce message ressemble à une tentative d'hameçonnage")
                        with ui.element("div").classes("rowact"):  # à droite de la ligne des badges ; « click.stop » : ne pas ouvrir le message
                            if m["has_att"]:
                                ui.button(icon="visibility").props("flat round dense size=sm").on(
                                    "click.stop", lambda m=m: self.row_preview(m)).tooltip("Aperçu de la pièce jointe, sans ouvrir le message")
                            ui.button(icon="mark_email_read" if unread else "mark_email_unread").props("flat round dense size=sm").on(
                                "click.stop", lambda m=m: self.toggle_read(m)).tooltip("Marquer comme lu" if unread else "Marquer comme non lu")
                            ui.button(icon="archive").props("flat round dense size=sm").on("click.stop", lambda m=m: self.swipe_act(m, "archive")).tooltip(
                                "Archiver la conversation" if n > 1 else "Archiver")
                            ui.button(icon="delete").props("flat round dense size=sm").on("click.stop", lambda m=m: self.swipe_act(m, "delete")).tooltip(
                                "Mettre la conversation à la corbeille" if n > 1 else "Mettre à la corbeille")
            self.row_menu(m, unread)

    def marked(self, text: str, classes: str) -> None:
        """Texte d'une ligne de la liste ; pendant une recherche, les mots cherchés sont surlignés en jaune."""
        if self.hl and search._spans(text, self.hl):
            ui.html(search.highlight_html(text, self.hl), sanitize=False).classes(classes)
        else:
            ui.label(text).classes(classes)

    async def row_preview(self, m: dict) -> None:
        """Aperçu d'une pièce jointe depuis la liste : on cherche, dans la conversation, les pièces jointes qu'on sait afficher."""
        found = []
        for x in reversed(self._conversation(m)):
            if not x.get("has_att"):
                continue
            full = queries.get_message(x["id"]) or x
            if full.get("att_json") is None:  # message synchronisé sans son contenu : on le relit d'abord
                try:
                    await run.io_bound(sync.ensure_details, full)
                except Exception as e:
                    return ui.notify(f"Le serveur ne répond pas : {e}", type="negative", multi_line=True)
                full = queries.get_message(x["id"]) or full
            found += [(full, a) for a in (json.loads(full["att_json"]) if full.get("att_json") else []) if self.preview_kind(a["name"])]
        if not found:
            return ui.notify("Aucune pièce jointe à afficher en aperçu (vous pouvez l'enregistrer depuis le message).", type="info")
        if len(found) == 1:
            return await self.preview(found[0][1], found[0][0])
        with self.host, ui.dialog() as dlg, ui.card().style("min-width:320px;max-width:92vw"):
            ui.label("Quelle pièce jointe afficher ?").style("font-weight:700")
            async def pick(a: dict, full: dict) -> None:
                dlg.close()
                await self.preview(a, full)
            for full, a in found:
                ui.button(f"{a['name']} · {human_size(a['size'])}", icon="visibility",
                          on_click=lambda _, a=a, full=full: pick(a, full)).props("flat no-caps align=left").classes("w-full")
            ui.button("Fermer", on_click=dlg.close).props("flat no-caps")
        dlg.open()

    def row_menu(self, m: dict, unread: bool) -> None:
        """Clic droit sur un mail de la liste : les actions courantes, sur toute la conversation.
        Le menu représente la moitié des éléments d'une ligne : ses entrées ne sont créées qu'au premier survol de la ligne (avant tout clic droit),
        sauf sur téléphone où le survol n'existe pas."""
        menu = ui.context_menu()
        done = []

        def build(_=None) -> None:
            if done:
                return
            done.append(1)
            pinned = bool(m.get("pinned")) or m.get("ckey") in self._pins
            with menu:
                ui.menu_item("Répondre", on_click=lambda: self.reply_to(m))
                ui.menu_item("Transférer…", on_click=lambda: self.forward(m))
                if m["folder"] == "INBOX":
                    ui.menu_item("Retirer l'épingle" if pinned else "Épingler en haut", on_click=lambda: self.toggle_pin(m))
                ui.menu_item("Marquer comme lu" if unread else "Marquer comme non lu", on_click=lambda: self.toggle_read(m))
                if m["folder"] == "INBOX":
                    opts = self.snooze_options()
                    ui.menu_item("Reporter : " + opts[0][0].lower(), on_click=lambda w=opts[0][1]: self.snooze(m, w))
                    ui.menu_item("Reporter…", on_click=lambda: self.snooze_dialog(m))
                ui.menu_item("Créer une tâche…", on_click=lambda: self.task_dialog(m))
                if m["folder"] == "INBOX" and security.quick_row(m):
                    ui.menu_item("Ce message n'est pas suspect", on_click=lambda: self.mark_not_suspect(m))
                if m["folder"] == "INBOX":
                    vip = aipolicy.is_vip(m["sender"])
                    ui.menu_item("Retirer des expéditeurs prioritaires" if vip else "★ Expéditeur prioritaire", on_click=lambda: self.toggle_vip(m))
                ui.separator()
                ui.menu_item("Archiver", on_click=lambda: self.swipe_act(m, "archive"))
                ui.menu_item("Déplacer vers…", on_click=lambda: self.move_dialog(m))
                ui.menu_item("Supprimer", on_click=lambda: self.swipe_act(m, "delete")).classes("text-negative")

        if SERVER_MODE:
            build()
        else:
            menu.parent_slot.parent.on("mouseenter.once", build)

    async def toggle_vip(self, m: dict) -> None:
        s = m["sender"].lower()
        items = aipolicy.vip_list()
        if aipolicy.is_vip(s):
            items = [p for p in items if not aipolicy.matches_sender(p, s)]
            ui.notify(f"{s} n'est plus prioritaire", type="info")
        else:
            items.append(s)
            with db.conn() as c:  # les mails déjà reçus de cet expéditeur passent aussi en urgent
                c.execute("UPDATE messages SET priority=3 WHERE lower(sender)=? AND folder='INBOX' AND is_read=0", (s,))
            ui.notify(f"{s} est maintenant prioritaire ★", type="positive")
        aipolicy.set_vip_list(items)
        await self.reload(force=True)

    async def reply_to(self, m: dict) -> None:
        await self.open(m["id"])
        if self.cur and self.cur.get("ckey") == m.get("ckey"):
            await self.reply()



    @ui.refreshable_method
    def rows_view(self) -> None:
        t = time.perf_counter()
        try:
            self._rows_view_body()
        finally:
            self._build_cost = time.perf_counter() - t  # sert à espacer les redessins automatiques sur un ordinateur lent
            self._full_at = time.monotonic()
            perf.record("liste : dessin complet", self._build_cost)

    def _rows_view_body(self) -> None:
        self._row_els = {}
        self._folder_paths = {(f["account"], f["key"]): f["path"] for a in sync.ACCOUNTS for f in db.folders(a)}
        self._pins = db.pin_keys()
        if (self.picked or self.select_mode) and self.view not in NOLIST_VIEWS - {"brief"}:
            self.bulk_bar()
        if self.view not in NOLIST_VIEWS:
            self.filter_bar()  # pastilles : ce qui filtre ou trie la liste, à retirer d'un clic
        alert = settings_ui.send_alert()
        if alert:  # rattachement manqué ou envoi incertain : visible tant qu'on ne l'a pas lu
            with ui.row().classes("items-center no-wrap gap-2 w-full").style(
                    "padding:10px 14px;margin:8px 0;border-radius:10px;background:#fdecea;color:#8a1c14;border:1px solid #f1b5af"):
                ui.icon("warning", size="20px")
                ui.label(settings_ui.alert_text(alert)).classes("col").style("font-size:13px")
                ui.button("Compris", on_click=self.dismiss_send_alert).props("flat dense no-caps size=sm color=negative")
        if self.view == "drafts":
            if not self.drafts:
                ui.label("Aucun brouillon en attente. Vos brouillons sont dans le dossier Brouillons de chaque boîte ; seuls ceux qui n'y sont pas encore (hors ligne) ou que vos assistants ont préparés apparaissent ici.").classes("empty")
            for dr in self.drafts:
                self.draft_row(dr)
            return
        if self.view == "snoozed":
            if not self.snoozed:
                ui.label("Aucun message reporté. Le bouton ⏲ de la barre d'actions masque un message jusqu'à l'heure choisie.").classes("empty")
            for r in self.snoozed:
                self.snoozed_row(r)
            return
        if self.view == "followups":
            if not self.followups:
                ui.label("Rien à relancer. Quand vous envoyez un message avec « Me rappeler si je n'ai pas de réponse », il apparaît ici si personne n'a répondu.").classes("empty")
            for f in self.followups:
                self.followup_row(f)
            return
        if self.view == "tasks":
            self.tasks_view()
            return
        if self.view == "srules":
            self.server_rules_view()
            return
        if self.view == "outbox":
            if not self.outbox:
                ui.label("Aucun message en attente. Les messages programmés et ceux qui partent dans quelques secondes apparaissent ici.").classes("empty")
            for r in self.outbox:
                self.outbox_row(r)
            return
        if self.view == "brief":
            b = self.brief
            if not b:
                ui.label("Chargement…").classes("empty")
                return
            keep = (lambda x: x["account"] == self.account) if self.account else (lambda x: True)
            todo, info, noise = ([x for x in b[k] if keep(x)] for k in ("todo", "info", "noise"))
            self.brief_card(b, keep)
            for title, items, n in (("À traiter", todo, 50), ("Pour info", info, 30), ("Newsletters & notifications", noise, 5)):
                if items:
                    with ui.element("div").classes("sect"):
                        ui.label(title)
                        ui.label(str(len(items)))
                    for m in items[:n]:
                        self.row(m)
            if not (todo or info or noise):
                ui.label("Rien de non lu analysé pour l'instant.").classes("empty")
            return
        if not self.rows:
            ui.label("Aucun message.").classes("empty")
            return
        woke = [m for m in self.rows if m.get("woke")]
        pinned = [m for m in self.rows if m.get("pinned")]
        shown = {"woke": woke, "pinned": pinned}
        last = None
        folded = db.kv_get("pins_folded") != "0"
        for m in self.rows:
            kind = "woke" if m.get("woke") else "pinned" if m.get("pinned") else ("other" if (woke or pinned) else None)
            if kind != last and kind:
                if kind == "pinned":
                    with ui.element("div").classes("sect").style("cursor:pointer").on("click", self.toggle_pins_folded):
                        with ui.row().classes("items-center no-wrap gap-1").style("color:#d92d3a;font-weight:700"):
                            ui.icon("chevron_right" if folded else "expand_more", size="18px")
                            ui.icon("push_pin", size="18px")
                            ui.label("Épinglés")
                        ui.label(str(len(pinned))).style("color:#d92d3a;font-weight:700")
                else:
                    with ui.element("div").classes("sect"):
                        ui.label({"woke": "À revoir (reportés)", "other": "Autres messages"}[kind])
                        if kind in shown:
                            ui.label(str(len(shown[kind])))
            last = kind
            if kind == "pinned" and folded:
                continue
            self.row(m)
        if len(self.rows) >= self.limit and not self.auto_more():  # boîte de réception : la suite se charge toute seule (voir rows_scrolled)
            ui.button("Afficher plus", on_click=self.more).props("flat no-caps").classes("self-center")

    # ---------- lecture ----------
    @ui.refreshable_method
    def reader(self) -> None:
        m = self.cur
        if self.new:
            with ui.column().classes("rhead gap-1"):
                ui.button("‹ Retour", on_click=self.back).props("flat dense no-caps").classes("lt-md self-start")
                ui.label("Nouveau message").classes("text-h6").style("font-weight:700")
            with ui.scroll_area().classes("rbody"):
                with ui.column().classes("rcontent gap-3"):
                    self.composer()
            return
        if not m and self.view == "brief":
            self.agenda_panel()  # le brief du jour : l'agenda de la semaine à la place de l'image d'attente ; il disparaît dès qu'on ouvre un mail ou qu'on écrit
            return
        if not m:
            with ui.column().classes("placeholder items-center gap-4"):
                ui.html(logo_big(), sanitize=False)
                ui.label("Sélectionnez un message")
            return
        if self.thread:
            self.thread_view()
            return
        with ui.column().classes("rhead gap-1"):
            ui.button("‹ Retour", on_click=self.back).props("flat dense no-caps").classes("lt-md self-start")
            ui.label(m["subject"]).classes("text-h6").style("line-height:1.3;font-weight:700")
            with ui.row().classes("items-center gap-3 no-wrap"):
                ui.avatar(initials(m["sender_name"] or m["sender"]), color="blue-1", text_color="blue-9", size="34px")
                when = full_date(m["date"])
                with ui.column().classes("gap-0"):
                    ui.label(f"{m['sender_name'] or m['sender']} <{m['sender']}>").classes("rmeta").style("color:var(--text);font-weight:600")
                    with ui.row().classes("items-center gap-2 no-wrap"):
                        ui.label(f"{when} · {label_of(m['account'])}").classes("rmeta")
                        if m.get("importance") in (0, 2):
                            high = m["importance"] == 2
                            with ui.row().classes("items-center gap-0 no-wrap").style(
                                    "padding:1px 8px 1px 4px;border-radius:10px;" + ("background:rgba(217,45,58,.12);color:#d92d3a" if high else "background:var(--hover);color:var(--muted)")):
                                ui.icon("priority_high" if high else "arrow_downward", size="14px")
                                ui.label("Importance haute" if high else "Importance basse").style("font-size:12px;font-weight:600")
            if m["to_addrs"]:
                ui.label(f"À : {m['to_addrs']}").classes("rmeta").style("word-break:break-word")
            if m["cc_addrs"]:
                ui.label(f"Cc : {m['cc_addrs']}").classes("rmeta").style("word-break:break-word")
            if m.get("bcc_addrs"):
                ui.label(f"Cci : {m['bcc_addrs']}").classes("rmeta").style("word-break:break-word")
            elif self.is_bcc_to_me(m):
                ui.label("🙈 Vous êtes en copie cachée (votre adresse n'apparaît pas dans les destinataires)").classes("rmeta")
        with ui.scroll_area().classes("rbody"):
            with ui.column().classes("rcontent gap-3"):
                use_ai = config.ai_enabled()
                if use_ai and m["summary"]:
                    with ui.element("div").classes("aicard"):
                        with ui.row().classes("w-full items-center no-wrap"):
                            ui.label("✦ Résumé IA").classes("tag col")
                            ui.button(f"Classé : {CAT.get(m['category'], m['category'] or '?')}", icon="edit",
                                      on_click=lambda: self.category_dialog(m)).props("flat dense no-caps size=sm color=primary").tooltip(
                                "Ce n'est pas la bonne catégorie ? Corrigez-la")
                        ui.label(m["summary"]).classes("q-mt-xs")
                        if m["suggested"]:
                            ui.label(f"Suggestion : {m['suggested']}").classes("sub")
                elif use_ai:
                    with ui.element("div").classes("aicard"):
                        ui.label("✦ IA").classes("tag")
                        ui.label("Pas encore analysé.").classes("sub")
                        ui.button("Analyser ce message", on_click=self.analyze_cur).props("unelevated no-caps color=primary").classes("q-mt-sm")
                self.translation_card(m)
                self.phishing_banner(m)
                self.meeting_card(m)
                self.unsub_banner(m)
                self.toolbar(m)
                if self.compose:
                    self.composer()
                self.body_view(m)

    def body_view(self, x: dict, trim: bool = False) -> None:
        """Corps du message : HTML d'origine (nettoyé, dans un cadre isolé) ; texte brut à défaut ou sur demande."""
        html = x.get("body_html")
        text = x["body"] or ""
        if not html and not text.strip() and sync.needs_details(x):  # le contenu n'a pas pu être lu sur le serveur : on le dit, au lieu d'un faux « message vide »
            with ui.row().classes("mailnote items-center gap-2 w-full"):
                ui.icon("cloud_off", size="18px")
                ui.label("Contenu non chargé : le serveur n'a pas répondu.").classes("col")
                ui.button("Réessayer", icon="refresh", on_click=lambda _, i=x["id"]: self.retry_details(i)).props("flat dense no-caps size=sm color=primary")
            return
        if not html or x["id"] in self.plain:
            if trim:  # dans une conversation : on masque les citations répétées
                short = for_ai(text, 20000)
                cut = len(short) < len(text) * 0.9
                self.marked(text if (x["id"] in self.full or not cut) else short, "mailbody")
                if cut:
                    ui.button("Masquer le message cité" if x["id"] in self.full else "Afficher le message complet",
                              on_click=lambda _, i=x["id"]: self.toggle_full(i)).props("flat dense no-caps size=sm")
            else:
                self.marked(text, "mailbody")
            if html:
                ui.button("Afficher la mise en forme", icon="auto_awesome_mosaic",
                          on_click=lambda _, i=x["id"]: self.toggle_plain(i)).props("flat dense no-caps size=sm")
            return
        clean = htmlmail.sanitize(html)
        sender = (x.get("sender") or "").strip().lower()
        trusted = bool(sender) and sender in self.trusted_senders()
        allow = x["id"] in self.images or trusted
        with ui.column().classes("w-full gap-1"):
            if htmlmail.has_remote_images(clean) and not allow:
                with ui.row().classes("mailnote items-center gap-2"):
                    ui.icon("image_not_supported", size="18px")
                    ui.label("Images distantes bloquées pour protéger votre vie privée.").classes("col")
                    ui.button("Afficher les images", on_click=lambda _, i=x["id"]: self.allow_images(i)).props("flat dense no-caps size=sm color=primary")
                    if sender:
                        ui.button(f"Toujours pour {sender}", on_click=lambda _, a=sender: self.trust_sender(a, True)).props("flat dense no-caps size=sm color=primary")
            elif trusted and htmlmail.has_remote_images(clean):
                with ui.row().classes("mailnote items-center gap-2"):
                    ui.icon("image", size="18px")
                    ui.label(f"Images affichées automatiquement pour {sender}.").classes("col")
                    ui.button("Ne plus afficher", on_click=lambda _, a=sender: self.trust_sender(a, False)).props("flat dense no-caps size=sm")
            frame = ui.element("iframe").classes("mailframe")
            frame._props.update({"sandbox": "allow-scripts", "srcdoc": htmlmail.build_doc(clean, allow, self.cids.get(x["id"]), self.hl or None)})
            frame.on("mailink", lambda e: self.open_link(((e.args or {}).get("detail") or {}).get("url", "")), args=["detail"])
            ui.button("Texte brut", icon="notes", on_click=lambda _, i=x["id"]: self.toggle_plain(i)).props("flat dense no-caps size=sm")

    def toggle_side_auto(self) -> None:
        on = db.kv_get("side_auto") != "1"
        db.kv_set("side_auto", "1" if on else "0")
        ui.run_javascript(f"mailiaAuto({'true' if on else 'false'})")
        self.side.refresh()  # l'icône reflète l'état

    def save_cols(self, e: dict) -> None:
        d = (e or {}).get("detail") or {}
        db.kv_set("cols", json.dumps({"side": int(d.get("side", 240)), "list": int(d.get("list", 400))}))

    def toggle_plain(self, mid: str) -> None:
        self.plain.symmetric_difference_update({mid})
        self.reader.refresh()

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

    def toolbar(self, m: dict) -> None:
        use_ai = config.ai_enabled()
        multi = len([a for a in (m["to_addrs"] + "," + m["cc_addrs"] if m["cc_addrs"] else m["to_addrs"]).split(",") if a.strip()]) > 1
        with ui.row().classes("gap-1 items-center no-wrap"):
            ui.button("Répondre", icon="reply", on_click=self.reply).props("unelevated no-caps no-wrap color=primary")
            if use_ai:
                with ui.button("✦ Réponse IA").props("outline no-caps no-wrap color=primary icon-right=arrow_drop_down").tooltip(
                        "L'IA rédige un brouillon : rien n'est envoyé, et aucun appel tant que vous ne choisissez pas"):
                    with ui.menu():
                        for it in self.ai_intents():
                            ui.menu_item(it["label"], on_click=lambda _, it=it: self.ai_reply(it["text"]))
                        ui.separator()
                        ui.menu_item("Traduire ce message en français", on_click=lambda: self.translate(m))
                        ui.menu_item("Autre consigne…", on_click=self.ai_custom)
                        ui.menu_item("✦ Suggérer des réponses pour ce message", on_click=self.suggest)
                        ui.menu_item("Gérer cette liste…", on_click=self.manage_intents)
            if multi:
                ui.button(icon="reply_all", on_click=lambda: self.reply(all_=True)).props("flat round dense").tooltip("Répondre à tous")
            ui.button(icon="forward_to_inbox", on_click=lambda: self.forward(m)).props("flat round dense").tooltip("Transférer ce message")
            if use_ai and not getattr(self, "has_summary", False):
                ui.button(icon="auto_awesome", on_click=self.summarize_thread).props("flat round dense color=primary").tooltip("Résumer la conversation")
            if m["folder"] == "INBOX":
                pinned = m["ckey"] in db.pin_keys()
                ui.button(icon="push_pin", on_click=lambda: self.toggle_pin(m)).props(
                    "round dense color=negative " + ("unelevated" if pinned else "flat")).tooltip(
                    "Retirer l'épingle" if pinned else "Épingler en haut de la boîte de réception")
            if m["folder"] == "INBOX":
                with ui.button(icon="schedule").props("flat round dense").tooltip("Reporter : masquer jusqu'à plus tard"):
                    with ui.menu():
                        for label, when in self.snooze_options():
                            ui.menu_item(f"{label}  ·  {self.fr_when(when)}", on_click=lambda _, w=when: self.snooze(m, w))
                        ui.separator()
                        ui.menu_item("Choisir la date et l'heure…", on_click=lambda: self.snooze_dialog(m))
            ui.button(icon="task_alt", on_click=lambda: self.task_dialog(m)).props("flat round dense").tooltip("Créer une tâche à partir de ce message")
            if config.ai_enabled() and not aipolicy.is_excluded(m):
                ui.button(icon="gpp_maybe", on_click=lambda: self.phishing_ai(m)).props("flat round dense").tooltip(
                    "Ce message est-il une arnaque ? Avis de l'IA (à la demande)")
            google_cal = config.calendar_backend() == "google"
            if google_cal or (sync.ACCOUNTS.get(m["account"]) and sync.ACCOUNTS[m["account"]].kind == "ews"):
                ui.button(icon="event", on_click=lambda: self.event_dialog(m)).props("flat round dense").tooltip(
                    "Ajouter un rendez-vous à Google Agenda" if google_cal else "Ajouter un rendez-vous à l'agenda Exchange")
            if sync.ACCOUNTS.get(m["account"]) and sync.ACCOUNTS[m["account"]].kind == "ews" and sync.ACCOUNTS[m["account"]].password:
                ui.button(icon="rule", on_click=lambda: self.srules_from_mail(m)).props("flat round dense").tooltip(
                    "Créer une règle de tri sur le serveur à partir de ce message").mark("srules-from-mail")
            ui.button(icon="archive", on_click=lambda: self.act("archive")).props("flat round dense").tooltip("Archiver")
            ui.button(icon="drive_file_move", on_click=self.move_dialog).props("flat round dense").tooltip("Déplacer")
            ui.button(icon="mark_email_unread", on_click=lambda: self.act("unread")).props("flat round dense").tooltip("Marquer non lu")
            ui.button(icon="delete", on_click=lambda: self.act("delete")).props("flat round dense color=negative").tooltip("Supprimer")
        with ui.row().classes("gap-1 items-center q-mt-xs"):
            ui.label("Réponse rapide :").classes("rmeta")
            for q in self.quick_replies():
                ui.button(q["label"], on_click=lambda _, q=q: self.quick_reply(q["text"])).props("outline dense no-caps size=sm color=primary").tooltip(q["text"])
            ui.button(icon="tune", on_click=self.manage_quick).props("flat dense round size=sm").tooltip("Gérer mes réponses rapides")
        atts = json.loads(m["att_json"]) if m["att_json"] else []
        if atts:
            with ui.row().classes("gap-1 items-center q-mt-xs"):
                for a in atts:
                    ui.button(f"{a['name']} · {human_size(a['size'])}", icon="attach_file",
                              on_click=lambda _, a=a: self.download(a)).props("outline dense no-caps size=sm color=grey-8")
                    if self.preview_kind(a["name"]):
                        ui.button(icon="visibility", on_click=lambda _, a=a: self.preview(a)).props(
                            "flat dense round size=sm color=grey-8").tooltip(f"Aperçu de {a['name']}")
        sg = self.suggestions.get(m["id"])
        if use_ai and sg:
            with ui.row().classes("gap-1 items-center q-mt-xs"):
                if sg == "loading":
                    ui.spinner(size="16px")
                    ui.label("L'IA cherche des réponses adaptées…").classes("rmeta")
                else:
                    ui.label("✦ Suggestions :").classes("rmeta")
                    for sug in sg:
                        ui.button(sug["label"], on_click=lambda _, sug=sug: self.ai_reply(sug["instruction"])).props(
                            "unelevated dense no-caps size=sm color=primary").tooltip(sug["instruction"])
                    ui.button(icon="close", on_click=lambda: self.drop_suggestions(m["id"])).props("flat dense round size=xs").tooltip("Masquer")

    async def retry_brief(self) -> None:
        """Relance tout de suite la synthèse du brief après un échec."""
        db.kv_set("brief:fail_at", "")
        self.brief = queries.brief(refresh=True)
        ui.notify("Synthèse relancée…")
        await asyncio.sleep(6)
        await self.reload(force=True)

    def toggle_pins_folded(self) -> None:
        db.kv_set("pins_folded", "1" if db.kv_get("pins_folded") == "0" else "0")
        self.rows_view.refresh()

    def dismiss_send_alert(self) -> None:
        db.kv_set("send_alert", "")
        self.rows_view.refresh()

    async def toggle_pin(self, m: dict) -> None:
        on = m["ckey"] not in db.pin_keys()
        try:
            await run.io_bound(sync.set_pinned, m, on)
        except Exception as e:
            return ui.notify(f"Le serveur n'a pas pris le drapeau : {friendly_error(e)}", type="negative")
        await self.reload(force=True)
        self.reader.refresh()














































    def thread_view(self) -> None:
        """Conversation : les messages empilés du plus ancien au plus récent, les récents/non lus dépliés."""
        th, cur = self.thread, self.cur
        me = sync.ACCOUNTS[cur["account"]].email.lower()
        subject = re.sub(r"^\s*((re|tr|fwd?|rép?)\s*:\s*)+", "", th[-1]["subject"], flags=re.I) or th[-1]["subject"]
        names: list[str] = []
        for x in th:
            who = "Moi" if x["sender"].lower() == me else (x["sender_name"] or x["sender"])
            if who not in names:
                names.append(who)
        with ui.column().classes("rhead gap-1"):
            ui.button("‹ Retour", on_click=self.back).props("flat dense no-caps").classes("lt-md self-start")
            ui.label(subject).classes("text-h6").style("line-height:1.3;font-weight:700")
            ui.label(f"{len(th)} messages · " + ", ".join(names[:4]) + ("…" if len(names) > 4 else "")).classes("rmeta")
        with ui.scroll_area().classes("rbody"):
            with ui.column().classes("rcontent gap-3"):
                if config.ai_enabled():
                    summary = self.tsum.get(cur["ckey"])
                    cached = db.kv_get(f"thread_sum:{cur['ckey']}")
                    if not summary and cached and cached.split("\n", 1)[0] == str(len(th)):
                        summary = cached.split("\n", 1)[1]
                    self.has_summary = bool(summary)
                    if summary:
                        with ui.element("div").classes("aicard"):
                            ui.label("✦ Résumé de la conversation").classes("tag")
                            ui.label(summary).classes("q-mt-xs")
                self.translation_card(cur)
                self.phishing_banner(cur)
                self.meeting_card(cur)
                self.unsub_banner(cur)
                self.toolbar(cur)
                if self.compose:
                    self.composer()
                for x in th:
                    self.thread_card(x, me)

    def thread_card(self, x: dict, me: str) -> None:
        mine = x["sender"].lower() == me
        opened = x["id"] in self.expanded
        focus = " focus" if self.cur and self.cur["id"] == x["id"] else ""
        unread = not x["is_read"]
        with ui.element("div").classes("tcard" + focus):
            with ui.element("div").classes("thead").on("click", lambda _, i=x["id"]: self.toggle_card(i)):
                ui.avatar(initials(x["sender_name"] or x["sender"]), color="blue-1", text_color="blue-9", size="32px")
                with ui.element("div").classes("who"):
                    with ui.element("div").classes("name" + (" unread" if unread else "")):
                        ui.label(x["sender_name"] or x["sender"]).style("display:inline")
                        if mine:
                            ui.label("Moi").classes("me-badge")
                    if opened:
                        ui.label("À : " + (x["to_addrs"] or "—")).classes("snip")
                    else:
                        ui.label(" ".join(for_ai(x["body"] or "", 300).split())[:160]
                                 or ("(contenu non chargé)" if sync.needs_details(x) else "(message vide)")).classes("snip")
                if x.get("category") and not mine and config.ai_enabled():
                    ui.button(f"Classé : {CAT.get(x['category'], x['category'])}", icon="edit").props(
                        "flat dense no-caps size=xs color=primary").on("click.stop", lambda _, x=x: self.category_dialog(x)).tooltip(
                        "Ce n'est pas la bonne catégorie ? Corrigez-la")
                if x.get("verb"):
                    ui.icon("forward" if x["verb"] == 2 else "reply", size="16px").style("color:var(--muted)").tooltip(
                        "Vous avez transféré ce message" if x["verb"] == 2 else "Vous avez répondu à ce message")
                ui.label(("📎 " if x["has_att"] else "") + short_date(x["date"])).classes("when")
            if opened:
                with ui.element("div").classes("tbody"):
                    if x["cc_addrs"]:
                        ui.label(f"Cc : {x['cc_addrs']}").classes("rmeta").style("word-break:break-word")
                    if x.get("bcc_addrs"):
                        ui.label(f"Cci : {x['bcc_addrs']}").classes("rmeta").style("word-break:break-word")
                    elif self.is_bcc_to_me(x):
                        ui.label("🙈 Vous êtes en copie cachée").classes("rmeta")
                    self.body_view(x, trim=True)
                    atts = json.loads(x["att_json"]) if x["att_json"] else []
                    if atts:
                        with ui.row().classes("gap-1 items-center q-mt-sm"):
                            for a in atts:
                                ui.button(f"{a['name']} · {human_size(a['size'])}", icon="attach_file",
                                          on_click=lambda _, a=a, x=x: self.download(a, x)).props("outline dense no-caps size=sm color=grey-8")
                                if self.preview_kind(a["name"]):
                                    ui.button(icon="visibility", on_click=lambda _, a=a, x=x: self.preview(a, x)).props(
                                        "flat dense round size=sm color=grey-8").tooltip(f"Aperçu de {a['name']}")

    async def retry_details(self, mid: str) -> None:
        """Relit sur le serveur le contenu d'un message qu'on n'avait pas pu charger."""
        x = next((t for t in self.thread if t["id"] == mid), None) or queries.get_message(mid)
        if not x:
            return
        try:
            await run.io_bound(sync.ensure_details, x)
        except Exception as e:
            return ui.notify(f"Le serveur ne répond toujours pas : {e}", type="negative", multi_line=True)
        await self._prepare(queries.get_message(mid))
        if self.thread:
            self.thread = queries.thread(x["ckey"])
            self.cur = next((t for t in self.thread if t["id"] == (self.cur or {}).get("id")), self.thread[-1])
        else:
            self.cur = queries.get_message(mid) or self.cur
        self.reader.refresh()

    async def toggle_card(self, mid: str) -> None:
        x = next((t for t in self.thread if t["id"] == mid), None)
        if not x:
            return
        if mid in self.expanded:
            self.expanded.discard(mid)
        else:
            self.expanded.add(mid)
            await self._prepare(x)
            if not x["is_read"]:
                db.set_read_by_id(mid, True)
                background_tasks.create(self._mark_remote(dict(x)))
        self.thread = queries.thread(x["ckey"])
        self.cur = next((t for t in self.thread if t["id"] == mid), self.cur)
        self.reader.refresh()
        await self.reload(force=True)

    def toggle_full(self, mid: str) -> None:
        self.full.symmetric_difference_update({mid})
        self.reader.refresh()














    # ---------- actions ----------
    async def go(self, view: str, acc: str, folder: str = "") -> None:
        self.view, self.account, self.folder, self.q, self.limit = view, acc, folder, "", 80
        self.picked.clear()
        self.select_mode = False
        self.filters.clear()
        self.list_tools.refresh()
        self.search.set_value("")
        if view == "brief":  # retour au brief : l'agenda remplace le mail ouvert
            self.cur, self.thread = None, []
            self.root.classes(remove="reading")
            self.reader.refresh()
        elif not self.cur and not self.new:  # on quitte le brief sans mail ouvert : l'agenda cède la place à l'image d'attente
            self.reader.refresh()
        db.kv_set("ui_open", "")  # on change de vue volontairement : rien à rouvrir
        self.remember_view()
        self.refresh_title()
        self.chips.refresh()
        await self.reload(force=True)

    async def set_account(self, acc: str) -> None:
        self.account = acc
        if self.view == "folder":
            self.view, self.folder = "inbox", ""
        self.remember_view()
        self.refresh_title()
        self.chips.refresh()
        await self.reload(force=True)







    async def more(self) -> None:
        self.limit += 80
        await self.reload(force=True)

    def auto_more(self) -> bool:
        """Boîte de réception (et « Non lus ») : la liste se prolonge d'elle-même en descendant, sans bouton « Afficher plus »."""
        return self.view in ("inbox", "unread")

    async def rows_scrolled(self, e) -> None:
        """Approche du bas de la liste : on charge la suite (80 de plus), ajoutée à la fin sans redessiner ce qui est déjà affiché."""
        if not self.auto_more() or self._more_busy or len(self.rows) < self.limit:
            return
        a = e.args or {}
        if a.get("verticalPosition", 0) + a.get("verticalContainerSize", 0) < a.get("verticalSize", 0) - 700:
            return
        self._more_busy = True
        try:
            await self.more()
        finally:
            self._more_busy = False

    async def reload(self, force: bool = False) -> None:
        self.hl = []  # remis plus bas par la liste de mails quand une recherche est en cours
        self.counts = queries.counts()
        if self.view == "brief":
            self.refresh_title()  # les compteurs du sous-titre suivent ce que vous gérez
            self.brief = queries.brief()
            sig = ("b", self.account, self.brief["headline"], *[(x["id"], x["is_read"]) for k in ("todo", "info", "noise") for x in self.brief[k]])
        elif self.view == "drafts":
            self.drafts = db.drafts_list(local_only=True)
            sig = ("d", *[(x["id"], x["updated"]) for x in self.drafts])
        elif self.view == "outbox":
            self.outbox = db.outbox_list()
            sig = ("o", *[(x["id"], x["status"], x["send_at"], x["error"]) for x in self.outbox])
        elif self.view == "snoozed":
            self.snoozed = queries.snoozed_rows()
            sig = ("s", *[(x["id"], x["until"]) for x in self.snoozed])
        elif self.view == "followups":
            self.followups = db.followups_due()
            sig = ("f", *[(x["id"], x["due_at"]) for x in self.followups])
        elif self.view == "srules":
            sig = ("sr", self.srules_acct)
        elif self.view == "tasks":
            self.tasks = db.tasks_list(self.show_done)
            sig = ("t", self.show_done, *[(x["id"], x["done"], x["text"], x["due"]) for x in self.tasks])
        else:
            self.rows = queries.list_messages(self.account, self.q, self.view == "unread" or "unread" in self.filters,
                                              self.view if self.view in ("todo", "noise", "newsletter", "notification") else "", self.limit,
                                              folder=self.folder if self.view == "folder" else "",
                                              flags=frozenset(self.active_filters()), sort_by=self.sort_by,
                                              oldest_first=self.oldest_first, everywhere=self.search_everywhere(),
                                              extra_ids=self.remote_ids if self.remote_for == self.q else None)
            self.hl = search.highlight_terms(self.q) if self.q.strip() else []  # mots à surligner dans la liste
            if self.hl:
                queries.add_snippets(self.rows, self.hl)
            # tout ce qui change l'aspect de la liste est dans la signature : ainsi un rechargement « forcé » (ouvrir un mail, cocher…) ne redessine
            # que les lignes touchées, au lieu des 80
            look = (self.view, self.account, self.folder, self.q, tuple(self.active_filters()) + (self.sort_by,), self.oldest_first, self.limit, tuple(sorted(self.picked)), self.select_mode,
                    db.kv_get("pins_folded"), repr(settings_ui.send_alert()), config.ai_enabled(), tuple(label_of(a) for a in sync.ACCOUNTS))
            cur_key = self.cur.get("ckey") if self.cur else None
            sig = ("l", look, tuple((x["id"], x["is_read"], x["summary"], x.get("conv_n"), x.get("conv_unread"), x.get("pinned"), x.get("woke"),
                                     cur_key is not None and cur_key == x.get("ckey"), x.get("importance")) for x in self.rows))
        # la colonne de gauche n'est redessinée que si ce qu'elle affiche a changé : sinon un clic sur ses icônes pouvait tomber
        # pile pendant un redessin (toutes les 12 s environ, à chaque synchro) et se perdre
        side_sig = (repr(sorted(self.counts.items(), key=lambda kv: kv[0])), vault.lock_enabled(db), self.view, self.account, self.folder, self.busy,
                    config.ai_enabled(), tuple(sync.ACCOUNTS),
                    tuple((f["account"], f["key"], f["name"], f["sync"]) for f in db.folders()))
        if force or side_sig != getattr(self, "_side_sig", None):
            self._side_sig = side_sig
            self.side.refresh()
        if force or sig != self.rows_sig:
            if sig[0] != "l":
                full = True
            elif sig == self.rows_sig:  # forcé mais rien de visible n'a changé : on ne redessine que pour rafraîchir les heures relatives (« 5 min »)
                full = time.monotonic() - self._full_at > 60
            else:
                full = not self._patch_rows(sig)
            if full:
                self.rows_view.refresh()
            self.rows_sig = sig

    def _patch_rows(self, sig: tuple) -> bool:
        """Quelques lignes ont changé (lu, résumé IA, sélection, nombre de messages) alors que la liste est la même : on ne reconstruit que
        celles-là. Redessiner les 80 lignes coûte plus d'une seconde de fenêtre figée sur un ordinateur lent. False = il faut tout redessiner."""
        old = self.rows_sig
        if not (old and old[0] == "l" and sig[0] == "l" and self._row_els):
            return False
        same_look = old[1][:6] + old[1][7:] == sig[1][:6] + sig[1][7:]  # tout sauf la profondeur de la liste (indice 6)
        if len(sig[2]) > len(old[2]):  # liste prolongée : les lignes sont ajoutées à la suite
            return same_look and self._append_rows(old, sig)
        if not (old[1] == sig[1] and len(old[2]) == len(sig[2])):
            return False
        changed = []
        for o, n, m in zip(old[2], sig[2], self.rows):
            if o != n:
                if o[0] != n[0] or o[5] != n[5] or o[6] != n[6] or m["id"] not in self._row_els:  # autre mail, épinglé/reporté changé (sections) ou ligne repliée
                    return False
                changed.append(m)
        if len(changed) > 15:
            return False
        t = time.perf_counter()
        self._pins = db.pin_keys()
        self._folder_paths = {(f["account"], f["key"]): f["path"] for a in sync.ACCOUNTS for f in db.folders(a)}
        for m in changed:
            former = self._row_els[m["id"]]
            parent = former.parent_slot.parent
            index = list(parent.default_slot.children).index(former)
            with parent:
                self.row(m)
            self._row_els[m["id"]].move(parent, target_index=index)
            former.delete()
        perf.record("liste : lignes redessinées", time.perf_counter() - t)
        return True

    def _append_rows(self, old: tuple, sig: tuple) -> bool:
        """La liste s'est allongée sans que rien d'autre ne change : on ajoute les nouvelles lignes en bas (seulement quand il n'y a pas de bouton
        « Afficher plus » à garder en dernier, et que les nouvelles lignes restent dans la même section)."""
        n = len(old[2])
        if not self.auto_more() or n == 0 or sig[2][:n] != old[2]:
            return False
        new, last = self.rows[n:], self.rows[n - 1]
        if last.get("woke") or last.get("pinned") or any(m.get("woke") or m.get("pinned") for m in new):
            return False
        t = time.perf_counter()
        parent = next(reversed(self._row_els.values())).parent_slot.parent
        self._pins = db.pin_keys()
        self._folder_paths = {(f["account"], f["key"]): f["path"] for a in sync.ACCOUNTS for f in db.folders(a)}
        with parent:
            for m in new:
                self.row(m)
        perf.record("liste : lignes ajoutées", time.perf_counter() - t)
        return True

    async def tick(self) -> None:
        v = queries.version()
        if v != self.ver:
            # le dessin de la liste fige la fenêtre pendant sa durée : sur un ordinateur lent (1,5 s), l'analyse IA du retard de mails la
            # redessinait toutes les 3 s. Les redessins venus de l'arrière-plan sont donc espacés de 6 fois la durée d'un dessin.
            if time.monotonic() - self._last_bg < 6 * self._build_cost:
                return
            self._last_bg = time.monotonic()
            self.ver = v
            await self.reload()

    async def open(self, mid: str) -> None:
        if self.compose or self.new:
            await self._save_draft_now()
        m = queries.get_message(mid)
        if not m:
            return ui.notify("Message introuvable", type="negative")
        if m["folder"] and m["folder"] == sync.drafts_folder(m["account"]):  # un message du dossier Brouillons s'ouvre dans l'éditeur, pas en lecture
            return await self.open_server_draft(m)
        self.new, self.compose, self.rep_folder = False, False, m["folder"]
        db.kv_set("ui_open", json.dumps({"id": mid, "t": time.time()}))  # pour rouvrir ce message si la fenêtre se recharge toute seule
        if m["ckey"] in db.snooze_keys("woke"):  # on l'a revu : il n'a plus à rester en tête
            db.snooze_clear(m["ckey"])
        th = queries.thread(m["ckey"]) if config.conversations_enabled() else [m]
        if len(th) > 1:  # conversation : on déplie le dernier message reçu et tous les non lus
            me = sync.ACCOUNTS[m["account"]].email.lower()
            received = [x for x in th if x["sender"].lower() != me]
            last = (received or th)[-1]
            unread = [x for x in th if not x["is_read"] and x["folder"] == m["folder"]]
            self.expanded = {x["id"] for x in unread} | {last["id"]}
            self.full = set()
            # les messages repliés dont le texte n'est pas encore en base (ex. un transfert qu'on vient d'envoyer) sont chargés aussi :
            # sinon leur aperçu affiche « (message vide) » alors qu'ils ont un contenu
            to_prepare = [x for x in th if x["id"] in self.expanded or not (x.get("body") or "").strip()]
            for x in unread:
                db.set_read_by_id(x["id"], True)
                background_tasks.create(self._mark_remote(dict(x)))
            self.thread = queries.thread(m["ckey"])
            self.cur = next(x for x in self.thread if x["id"] == last["id"])
        else:
            self.thread = []
            to_prepare = [m]
            self.cur = m
            if not m["is_read"]:
                db.set_read_by_id(mid, True)
                m["is_read"] = 1
                background_tasks.create(self._mark_remote(dict(m)))
        c = self.cur
        self.draft.update(files=[], quote=True, to=c["sender"], cc="", body="", ins="", html="", gen=False, importance=1,
                          subject=c["subject"] if c["subject"].lower().startswith("re:") else "RE: " + c["subject"])
        self.root.classes(add="reading")
        await self.reload(force=True)
        self.reader.refresh()  # on affiche tout de suite ce qu'on a ; la lecture sur le serveur (lente ou limitée) vient après
        missing = [x for x in to_prepare if sync.needs_details(x) or (x.get("body_html") and x["id"] not in self.cids)]
        if missing:
            for x in missing:
                await self._prepare(x)
            if self.cur and self.cur["ckey"] == c["ckey"]:  # l'utilisateur n'est pas passé à un autre message entre-temps
                if self.thread:
                    self.thread = queries.thread(c["ckey"])
                    self.cur = next((x for x in self.thread if x["id"] == c["id"]), self.cur)
                else:
                    self.cur = queries.get_message(c["id"]) or self.cur
                with self.host:
                    self.reader.refresh()

    async def _prepare(self, x: dict) -> dict:
        """Charge ce qui manque à un message ancien : copie, pièces jointes, HTML d'origine, images incrustées."""
        if sync.needs_details(x):
            try:
                x = await run.io_bound(sync.ensure_details, x)
            except Exception:
                log.exception("lecture du détail d'un message")
        html = x.get("body_html")
        if html and x["id"] not in self.cids:
            refs = htmlmail.cid_refs(htmlmail.sanitize(html))
            self.cids[x["id"]] = {}
            if refs:
                try:
                    self.cids[x["id"]] = await run.io_bound(sync.inline_images, x, refs)
                except Exception:
                    log.debug("images incrustées de %s illisibles", x.get("id"), exc_info=True)
        return x

    async def _mark_remote(self, m: dict) -> None:
        try:
            await run.io_bound(sync.provider(m["account"]).set_read, m["folder"], m["uid"], True)
        except Exception as e:
            ui.notify(f"Lu en local, pas sur le serveur : {e}", type="warning")

    def settings(self) -> None:
        open_settings(self.host, self.folders_dialog)

    def back(self) -> None:
        self.root.classes(remove="reading")
        if self.new:
            self.new = False
            self.reader.refresh()

    async def analyze_cur(self) -> None:
        try:
            await run.io_bound(ai.analyze_and_store, self.cur["id"])
        except ai.AIError as e:
            return ui.notify(str(e), type="negative")
        self.cur = queries.get_message(self.cur["id"])
        self.reader.refresh()
        await self.reload(force=True)

    async def act(self, kind: str, dest: str | None = None) -> None:
        """Archiver / déplacer / supprimer s'appliquent à toute la conversation (dans le dossier d'où on l'a ouverte) ;
        « non lu » ne marque que le dernier message."""
        m = self.cur
        targets = [x for x in self.thread if x["folder"] == self.rep_folder] if self.thread else [m]
        if kind == "unread":
            targets = targets[-1:]
        n = len(targets)
        nxt = (self._neighbour(1) or self._neighbour(-1)) if kind != "unread" else None  # conversation à ouvrir ensuite
        if kind == "delete":
            with self.host, ui.dialog() as dlg, ui.card():
                ui.label("Mettre ce message à la corbeille ?" if n == 1 else f"Mettre cette conversation ({n} messages) à la corbeille ?")
                with ui.row().classes("justify-end w-full"):
                    ui.button("Annuler", on_click=lambda: dlg.submit(False)).props("flat no-caps")
                    ui.button("Supprimer", on_click=lambda: dlg.submit(True)).props("unelevated no-caps color=negative")
            if not await dlg:
                return
        try:
            await run.io_bound(sync.perform_many, targets, kind, dest)
        except Exception as e:
            return ui.notify(f"Action impossible : {e}", type="negative", multi_line=True)
        what = "Conversation" if n > 1 else "Message"
        done = {"archive": f"{what} archivée" if n > 1 else "Message archivé",
                "delete": f"{what} mise à la corbeille" if n > 1 else "Message mis à la corbeille",
                "move": f"{what} déplacée" if n > 1 else "Message déplacé", "unread": "Marqué comme non lu"}[kind]
        ui.notify(done, type="positive")
        if kind == "unread":
            if self.thread:
                self.thread = queries.thread(m["ckey"])
                self.cur = next((x for x in self.thread if x["id"] == m["id"]), self.thread[-1])
            else:
                self.cur = queries.get_message(m["id"])
        else:
            self.cur, self.thread = None, []
            self.root.classes(remove="reading")
        self.reader.refresh()
        await self.reload(force=True)
        if kind != "unread" and nxt and nxt["ckey"] != m["ckey"]:
            await self.open(nxt["id"])

    async def dropped(self, e: dict, acct: str, dest: str) -> None:
        """Un mail (ou une conversation) a été déposé sur un dossier."""
        m = queries.get_message(((e or {}).get("detail") or {}).get("id", ""))
        if not m or m["account"] != acct:
            return
        if dest == "__other__":
            return await self.move_dialog(m)
        await self._move_conv(m, dest)

    async def _move_conv(self, m: dict, dest: str) -> None:
        th = self._conversation(m)
        nxt = self._neighbour(1) or self._neighbour(-1)
        name = next((f["path"] for f in db.folders(m["account"]) if f["key"] == dest), "le dossier")
        try:
            await run.io_bound(sync.perform_many, th, "move", dest)
        except Exception as err:
            return ui.notify(f"Déplacement impossible : {err}", type="negative", multi_line=True)
        ui.notify(f"{'Conversation déplacée' if len(th) > 1 else 'Message déplacé'} vers « {name} »", type="positive")
        await self._after_swipe(m, removed=True, nxt=nxt)
        self.side.refresh()

    # ---------- gestes sur la liste ----------
    def _conversation(self, m: dict) -> list[dict]:
        """Les messages de la conversation de `m` qui sont dans le même dossier (ceux que l'on range / supprime)."""
        th = [x for x in queries.thread(m["ckey"]) if x["folder"] == m["folder"]] if config.conversations_enabled() else []
        return th or [m]

    async def swiped(self, m: dict, e: dict) -> None:
        action = ((e or {}).get("detail") or {}).get("action")
        if action == "read":
            await self.toggle_read(m)
        elif action == "archive":
            await self.swipe_act(m, "archive")

    async def toggle_read(self, m: dict) -> None:
        th = self._conversation(m)
        unread = [x for x in th if not x["is_read"]]
        try:
            if unread:
                await run.io_bound(sync.perform_many, unread, "read")
            else:
                await run.io_bound(sync.perform_many, th[-1:], "unread")
        except Exception as err:
            return ui.notify(f"Action impossible : {err}", type="negative", multi_line=True)
        await self._after_swipe(m, removed=False)

    async def swipe_act(self, m: dict, kind: str) -> None:
        th = self._conversation(m)
        nxt = self._neighbour(1) or self._neighbour(-1)
        try:
            await run.io_bound(sync.perform_many, th, kind, None)
        except Exception as err:
            return ui.notify(f"Action impossible : {err}", type="negative", multi_line=True)
        what = "Conversation" if len(th) > 1 else "Message"
        ui.notify({"archive": f"{what} archivé{'e' if len(th) > 1 else ''}",
                   "delete": f"{what} mis{'e' if len(th) > 1 else ''} à la corbeille"}[kind], type="positive")
        await self._after_swipe(m, removed=True, nxt=nxt)

    async def _after_swipe(self, m: dict, removed: bool, nxt: dict | None = None) -> None:
        opened = removed and self.cur and self.cur.get("ckey") == m.get("ckey")
        if opened:  # la conversation ouverte vient d'être rangée
            self.cur, self.thread = None, []
            self.root.classes(remove="reading")
            self.reader.refresh()
        await self.reload(force=True)
        if opened and nxt and nxt["ckey"] != m["ckey"]:
            await self.open(nxt["id"])  # on enchaîne sur la conversation suivante

    async def folders_dialog(self, acct: str) -> None:
        """Une seule règle : un dossier coché est copié dans l'appli, tenu à jour et affiché ; décoché, il n'existe pas ici."""
        cfg = sync.ACCOUNTS[acct]
        if len(db.folders(acct)) <= 1:
            ui.notify("Lecture des dossiers sur le serveur…")
            await run.io_bound(sync.refresh_folders, acct)
        rows = sorted(db.folders(acct), key=lambda r: (r["key"] != "INBOX", r["path"].lower()))
        on = {r["key"]: bool(r["sync"]) for r in rows}
        arch = {"v": sync.archive_folder(acct)}
        limit = {"v": config.sync_limit()}
        ROLES = {"inbox": "boîte de réception", "archive": "archive", "trash": "corbeille", "sent": "envoyés",
                 "drafts": "brouillons", "junk": "spam"}

        with self.host, ui.dialog() as dlg, ui.card().style("width:780px;max-width:96vw"):
            ui.label(f"Dossiers affichés — {cfg.label}").classes("text-h6")
            with ui.column().classes("gap-0 q-mb-sm"):
                ui.label("✔ Coché : le dossier apparaît dans la barre latérale, ses mails sont copiés dans l'application et mis à jour automatiquement.").classes("text-body2")
                ui.label("✘ Décoché : le dossier n'apparaît pas dans l'application. Rien n'est supprimé sur le serveur.").classes("text-body2")
                ui.label("La boîte de réception est toujours affichée. L'IA n'analyse jamais que la boîte de réception.").classes("text-caption text-grey")
            # arbre des dossiers : même hiérarchie que sur le serveur, repliable, avec icônes
            TECH = {"problèmes de synchronisation", "historique des conversations", "flux rss", "planifié",
                    "boîte d'envoi", "journal", "notes", "tâches", "conflits"}
            ICONS = {"inbox": "inbox", "archive": "archive", "trash": "delete", "sent": "send", "drafts": "edit_note", "junk": "report"}
            TINTS = {"inbox": "blue-8", "archive": "blue-grey-6", "trash": "grey-7", "sent": "blue-6", "drafts": "blue-4", "junk": "grey-7"}
            by_path = {r["path"]: {"id": r["key"], "label": r["name"], "icon": ICONS.get(r["role"], "folder"),
                                   "color": TINTS.get(r["role"], "blue-4"),
                                   "hint": "toujours affichée" if r["key"] == "INBOX" else ROLES.get(r["role"], ""),
                                   "noTick": r["key"] == "INBOX", "children": []} for r in rows}
            roots = []
            for path, node in by_path.items():
                parent = by_path.get(path.rsplit("/", 1)[0]) if "/" in path else None
                (parent["children"] if parent else roots).append(node)
            tech = [n for n in roots if n["label"].lower() in TECH]
            nodes = [n for n in roots if n not in tech]
            if tech:
                nodes.append({"id": "__tech", "label": "Dossiers techniques d'Outlook", "icon": "settings", "color": "grey-6",
                              "hint": "rarement utiles", "tickable": False, "children": tech})
            count = ui.label().classes("text-caption text-grey")

            def update_count() -> None:
                n = sum(1 for k, v in on.items() if v and k != "INBOX")
                count.set_text("Boîte de réception + " + (f"{n} autre(s) dossier(s) affiché(s)" if n else "aucun autre dossier"))

            def on_tick(e) -> None:
                ticked = set(e.value or [])
                for k in on:
                    if k != "INBOX":
                        on[k] = k in ticked
                update_count()

            flt = ui.input(placeholder="Rechercher un dossier…").props("dense outlined clearable").classes("w-full")
            with ui.scroll_area().classes("w-full").style("height:calc(100vh - 470px);min-height:190px;max-height:380px;border:1px solid var(--line);border-radius:10px;padding:6px 10px"):
                tree = ui.tree(nodes, node_key="id", label_key="label", tick_strategy="strict", on_tick=on_tick).props(
                    "dense no-nodes-label=\"Aucun dossier\" no-results-label=\"Aucun dossier ne correspond\"")
                tree.add_slot("default-header", """
                    <div class="row items-center no-wrap" style="gap:8px">
                        <q-icon :name="props.node.icon" :color="props.node.color" size="19px"></q-icon>
                        <span :style="props.node.id === 'INBOX' ? 'font-weight:600' : ''">{{ props.node.label }}</span>
                        <span v-if="props.node.hint" class="treehint">{{ props.node.hint }}</span>
                    </div>""")
                tree.tick(["INBOX", *[k for k, v in on.items() if v and k != "INBOX"]])

            def apply_filter(e) -> None:
                tree._props["filter"] = e.value or ""
                tree.update()

            flt.on_value_change(apply_filter)
            update_count()
            others = {r["key"]: r["path"] for r in rows if r["key"] != "INBOX"}
            ui.select(others, label="Le bouton « Archiver » déplace le mail vers…", clearable=True,
                      with_input=True).bind_value(arch, "v").props("outlined dense").classes("w-full q-mt-sm")
            ui.select({100: "100 derniers mails", 150: "150 derniers mails", 300: "300 derniers mails",
                       500: "500 derniers mails", 1000: "1 000 derniers mails", 2000: "2 000 derniers mails", 5000: "5 000 derniers mails"},
                      label="Mails gardés par dossier (les plus récents)").bind_value(limit, "v").props("outlined dense").classes("w-full q-mt-sm")
            ui.label("Un gros dossier (ex. Archive) se charge en une minute environ la première fois. Plus on garde de mails, plus c'est long.").classes("text-caption text-grey")

            async def save() -> None:
                before = {r["key"] for r in rows if r["sync"]}
                after = {k for k, v in on.items() if v} | {"INBOX"}
                for k in before - after:  # dossiers décochés : on les retire de l'appli
                    await run.io_bound(db.purge_folder, acct, k)
                db.set_folder_sync(acct, after)
                db.kv_set(f"archive:{acct}", arch["v"] or "")
                if limit["v"] != config.sync_limit():  # nouvelle profondeur : on recharge tous les dossiers suivis
                    config.write_env({"SYNC_LIMIT": str(limit["v"])})
                    for a2 in sync.ACCOUNTS:
                        for f2 in db.folders(a2):
                            if f2["sync"]:
                                db.set_folder_state(a2, f2["key"], None)
                dlg.close()
                self.side.refresh()
                ui.notify("Dossiers mis à jour. Chargement en cours…", type="positive")
                background_tasks.create(self._sync_after_folders())

            with ui.row().classes("justify-end w-full q-mt-sm"):
                ui.button("Annuler", on_click=dlg.close).props("flat no-caps")
                ui.button("Enregistrer", on_click=save).props("unelevated no-caps color=primary")
        dlg.open()

    async def _sync_after_folders(self) -> None:
        try:
            await run.io_bound(sync.run_cycle, True)
        finally:
            await self.reload(force=True)








    async def choose_folder(self, opts: dict, title: str = "Déplacer vers…", confirm: str = "Déplacer") -> str | None:
        """Fenêtre de choix d'un dossier (recherche + liste). Retourne la clé du dossier choisi, sinon None."""
        choice = {"key": None, "q": ""}
        with self.host, ui.dialog() as dlg, ui.card().style("width:520px;max-width:96vw"):
            ui.label(title).classes("text-h6")
            search = ui.input(placeholder="Rechercher un dossier…").props("outlined dense clearable autofocus").classes("w-full")

            def matching() -> list[tuple[str, str]]:
                q = choice["q"].strip().lower()
                return sorted(((k, p) for k, p in opts.items() if q in p.lower()), key=lambda kp: kp[1].lower())

            @ui.refreshable
            def folder_list() -> None:
                rows = matching()
                if not rows:
                    ui.label("Aucun dossier ne correspond.").classes("rmeta q-pa-md")
                for k, path in rows:
                    parts = path.split("/")
                    on = choice["key"] == k
                    with ui.row().classes("items-center no-wrap gap-2 w-full").style(
                            "padding:6px 10px;border-radius:8px;cursor:pointer;" + ("background:var(--accent-soft);color:var(--accent)" if on else "")
                    ).on("click", lambda _, k=k: pick(k)).on("dblclick", lambda _, k=k: dlg.submit(k)):
                        ui.icon("folder_open" if on else "folder", size="18px").style("opacity:.75")
                        ui.label(parts[-1]).style("font-weight:600")
                        if len(parts) > 1:
                            ui.label("/".join(parts[:-1])).classes("rmeta ellipsis").style("min-width:0")

            def pick(k: str) -> None:
                choice["key"] = k
                folder_list.refresh()

            def on_search(e) -> None:
                choice["q"] = e.value or ""
                folder_list.refresh()

            search.on_value_change(on_search)
            search.on("keydown.enter", lambda: (matching() and dlg.submit(choice["key"] or matching()[0][0])))
            with ui.scroll_area().style("height:340px;border:1px solid var(--line);border-radius:10px"):
                folder_list()
            with ui.row().classes("justify-end w-full"):
                ui.button("Annuler", on_click=lambda: dlg.submit(None)).props("flat no-caps")
                ui.button(confirm, on_click=lambda: dlg.submit(choice["key"])).props("unelevated no-caps color=primary")
        return await dlg

    async def move_dialog(self, msg: dict | None = None) -> None:
        m = msg or self.cur
        opts = {f["key"]: f["path"] for f in db.folders(m["account"]) if f["key"] != (m["folder"] if msg else (self.rep_folder or m["folder"]))}
        dest = await self.choose_folder(opts)
        if dest:
            await (self._move_conv(m, dest) if msg else self.act("move", dest))













    @staticmethod
    def is_bcc_to_me(m: dict) -> bool:
        """Mail reçu dont les destinataires visibles ne contiennent pas l'adresse de la boîte : on est en copie cachée (ou sur une liste de diffusion)."""
        cfg = sync.ACCOUNTS.get(m["account"])
        if not cfg or m["folder"] != "INBOX" or (m["sender"] or "").lower() == cfg.email.lower() or m.get("att_json") is None \
                or not (m.get("to_addrs") or "").strip():  # destinataires pas encore chargés : on ne peut rien affirmer
            return False
        seen = f"{m['to_addrs'] or ''},{m['cc_addrs'] or ''}".lower()
        return cfg.email.lower() not in seen











    async def do_sync(self) -> None:
        self.busy = True
        self.side.refresh()
        report = await run.io_bound(sync.run_cycle, True)
        self.busy = False
        errs = report["errors"]
        ui.notify("; ".join(f"{label_of(k)} : {v}" for k, v in errs.items()) if errs else "Synchronisé",
                  type="warning" if errs else "positive")
        await self.reload(force=True)
























async def index_page() -> None:
    t_page = time.perf_counter()
    if not any(n == "page demandée par la fenêtre" for n, _ in perf.MARKS):
        perf.mark("page demandée par la fenêtre")
    ui.colors(primary="#0a66e8", secondary="#4a9bff", accent="#0a66e8")
    ui.add_head_html(SWIPE_JS)
    ui.add_head_html(f'<script src="/pet/pet.js?v={ui_pet.version()}" defer></script>')  # chat de compagnie (désactivé par défaut)
    if (script := i18n.client_script()):
        ui.add_head_html(script)  # interface dans une autre langue que le français
    ui.dark_mode({"light": False, "dark": True}.get(db.kv_get("theme") or ""))  # clair / sombre, sinon suit le système
    ui.add_css(CSS)
    if ui_zoom.compact():  # liste compacte : une classe sur la page, que le CSS exploite
        ui.query("body").classes(add="compact")
    if SERVER_MODE and not app.storage.user.get("ok"):
        with ui.card().classes("absolute-center"):
            ui.label("Velora").classes("text-h5")
            tk = ui.input("Jeton d'accès", password=True, password_toggle_button=True).props("outlined dense autofocus")

            def login() -> None:
                if tk.value and tk.value.strip() == config.env("APP_TOKEN"):
                    app.storage.user["ok"] = True
                    ui.navigate.reload()
                else:
                    ui.notify("Jeton invalide", type="negative")

            tk.on("keydown.enter", login)
            ui.button("Entrer", on_click=login).props("unelevated no-caps color=primary")
        return
    if DB_ERROR:
        render_db_error()
        return
    if lock_applies() and not session_unlocked():
        render_lock_screen()
        return
    box = Mailbox()
    ua = ""
    try:
        ua = ui.context.client.request.headers.get("user-agent", "")
    except Exception:
        pass
    # navigateur : « Velora » = la fenêtre de l'appli ; Chrome / Safari / Firefox = un navigateur ouvert sur l'appli (un autre écran qui se recharge)
    kind = "fenêtre de l'appli" if ("Velora" in ua or "Velora" in ua or ("AppleWebKit" in ua and "Safari" not in ua and "Chrome" not in ua)) else "navigateur"
    log.info("page ouverte [%s, client %s] (vue : %s) · %s", kind, ui.context.client.id[:6], box.view, ua[-60:])  # repère, dans le journal, une page qui se recharge toute seule
    box.build()
    global DB_NOTICE
    if DB_NOTICE:
        ui.notify(DB_NOTICE, type="warning", multi_line=True, timeout=0, close_button="OK")
        DB_NOTICE = ""
    await box.reload(force=True)
    await box.restore_open()
    perf.record("page : construction de l'interface", time.perf_counter() - t_page)
    if not any(n == "interface affichée" for n, _ in perf.MARKS):
        perf.mark("interface affichée")
    if lock_applies() and vault.lock_enabled(db) and vault.idle_minutes(db):
        ui.on("idlelock", lambda: lock_screen_now())
        ui.run_javascript("""(() => { const lim = %d * 60000; let t = Date.now();
          for (const e of ['mousemove', 'keydown', 'mousedown', 'touchstart', 'wheel']) document.addEventListener(e, () => { t = Date.now(); }, true);
          if (window.__idleT) clearInterval(window.__idleT);
          window.__idleT = setInterval(() => { if (Date.now() - t > lim) { clearInterval(window.__idleT); emitEvent('idlelock', {}); } }, 15000); })()""" % vault.idle_minutes(db))
    ui_zoom.install_keys()
    ui.on("uiready", lambda e: perf.browser_marks(e.args))
    ui.run_javascript("""requestAnimationFrame(() => requestAnimationFrame(() => { const n = performance.getEntriesByType('navigation')[0] || {};
      emitEvent('uiready', {origin: performance.timeOrigin, dcl: n.domContentLoadedEventEnd || 0, load: n.loadEventEnd || 0, now: performance.now()}); }))""")
    ui.timer(3, box.tick)
    ui.timer(5, box.autosave)
    ui.timer(2, box.watch_outbox)
    ui.timer(20, auto_check, once=True)  # laisse l'appli s'afficher d'abord
    ui.on('mailkey', box.on_key)
    if not config.read_accounts():
        await wizard.run_wizard(box.host, True, friendly_error)  # premier lancement : aucune boîte configurée
    elif not sync.ACCOUNTS:
        ui.notify("Toutes vos boîtes sont désactivées : réactivez-en une dans les paramètres.", type="warning")
        open_settings(box.host, box.folders_dialog)
    elif not any(c.password for c in sync.ACCOUNTS.values()):
        open_settings(box.host, box.folders_dialog)


index = ui.page("/")(index_page)  # la fonction reste appelable seule : les tests de l'interface la réenregistrent


async def outbox_loop() -> None:
    """Envoie les messages de la file dont l'heure est venue, même si aucune fenêtre n'est ouverte."""
    try:
        uncertain, _cleaned = await run.io_bound(db.outbox_recover)  # avant le premier envoi : les envois interrompus sont signalés, jamais rejoués
        if uncertain:
            notify.send("Envoi interrompu", f"{uncertain} message(s) à vérifier dans « En attente d'envoi » : sont-ils partis ?")
    except Exception:
        log.exception("reprise de la file d'envoi")
    await asyncio.sleep(2)
    ticks = 0
    while True:
        try:
            if db.outbox_count():
                await run.io_bound(sync.send_due)
            if ticks % 150 == 0 and await run.io_bound(db.backup_due):  # toutes les 5 min, on regarde si la copie du jour est faite
                try:
                    await run.io_bound(db.backup_now)
                except Exception:
                    log.exception("sauvegarde de la base")
            if ticks % 15 == 0:  # toutes les 30 s : reports arrivés à échéance, relances sans réponse
                await run.io_bound(sync.reminders_upkeep)
            if ticks % 15 == 7:  # toutes les 30 s : copie des brouillons modifiés dans le dossier Brouillons du serveur (et suppression des copies périmées)
                await run.io_bound(sync.mirror_drafts)
        except Exception:
            log.exception("file d'envoi")
        ticks += 1
        await asyncio.sleep(2)


async def sync_loop() -> None:
    await asyncio.sleep(1)
    while True:
        try:
            await run.io_bound(sync.run_cycle)
        except Exception:
            log.exception("cycle de synchro")
        await asyncio.sleep(5)


def _stop_children() -> None:
    """Ferme la fenêtre native et les processus fils avec l'application."""
    for child in multiprocessing.active_children():
        try:
            child.terminate()
        except Exception:
            pass


def _cleanup_stale() -> None:
    """Au démarrage de l'appli installée : ferme les restes (fenêtre, processus) d'un lancement précédent tué de force."""
    if not getattr(sys, "frozen", False) or sys.platform.startswith("win"):
        return
    try:
        out = subprocess.run(["ps", "-axo", "pid=,ppid=,command="], capture_output=True, text=True, timeout=5).stdout
    except Exception:
        return
    exe = os.path.realpath(sys.executable)
    for line in out.splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) == 3 and parts[1] == "1" and int(parts[0]) != os.getpid() \
                and parts[2].startswith(exe) and "multiprocessing" in parts[2]:
            try:
                os.kill(int(parts[0]), signal.SIGTERM)
            except OSError:
                pass


def _webview_args() -> dict:
    """Windows : par défaut la fenêtre (WebView2) recrée un profil de navigateur tout neuf dans un dossier temporaire à chaque lancement, l'efface
    à la fermeture, et supprime les cookies au démarrage : ~2,5 s de démarrage perdues. Un profil permanent, rangé dans le dossier de données
    (donc dans ce que l'antivirus peut exclure), est réutilisé d'un lancement à l'autre : le démarrage est plus rapide et les fichiers de la page
    restent en cache. Sur Mac, la fenêtre n'a pas ce coût : rien ne change."""
    if not sys.platform.startswith("win") or os.environ.get("VELORA_WEBVIEW_PRIVATE") == "1":
        return {}
    return {"private_mode": False, "storage_path": str(config.DATA_DIR / "webview")}


DB_ERROR = ""
DB_NOTICE = ""  # base abîmée puis réparée au démarrage : dit à l'utilisateur ce qui s'est passé (affiché une fois)
try:
    try:
        db.init()
    except Exception as e:
        if not db.is_corruption(e):
            raise
        logging.getLogger("mailia").error("base abîmée (%s) : réparation", e)
        outcome = db.recover()
        db.init()
        DB_NOTICE = ("Votre base de données était abîmée. " + (f"Elle a été remise en état depuis la copie automatique ({outcome.split(' ', 1)[1]}) : les derniers jours "
                     "de mails se resynchroniseront." if outcome != "vide" else "Aucune copie valable : Velora repart d'une base vide, et vos mails se relisent depuis vos serveurs.")
                     + " L'ancien fichier est conservé à côté (mail.db.abimee-…).")
    compose.seed_templates()
    perf.mark("base prête")
except RuntimeError as e:  # base chiffrée dont la clé est introuvable : on affiche l'écran de récupération au lieu de planter
    DB_ERROR = str(e)
    logging.getLogger("mailia").error("base inaccessible : %s", e)
async def _start_api() -> None:
    await run.io_bound(apiserver.ensure)  # ouvre l'API et/ou le serveur MCP si vous les avez activés


if not DB_ERROR:
    app.on_startup(lambda: perf.mark("serveur de l'interface démarré"))
    app.on_startup(lambda: background_tasks.create(perf.lag_monitor()))
    app.on_startup(lambda: background_tasks.create(sync_loop()))
    app.on_startup(lambda: background_tasks.create(outbox_loop()))
    app.on_startup(lambda: background_tasks.create(_start_api()))
    app.add_static_files("/weather-icons", str(weather.ICONS))  # icônes météo animées (Meteocons, licence MIT)
    app.add_static_files("/pet", str(ui_pet.DIR))  # chat de compagnie
    app.on_startup(apple_writer.refresh_async)  # Apple Intelligence prête ? vérifié en arrière-plan dès le démarrage
    app.on_shutdown(apiserver.runner.stop)
# le serveur de l'interface n'accepte que les requêtes destinées à cette machine : une page web malveillante qui ferait pointer un nom de
# domaine vers 127.0.0.1 (« DNS rebinding ») est refusée. En mode serveur (téléphone), le jeton d'accès protège l'accès.
from starlette.middleware.trustedhost import TrustedHostMiddleware  # noqa: E402
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["*"] if SERVER_MODE else ["127.0.0.1", "localhost", "[::1]", "::1"])
app.on_shutdown(_stop_children)
atexit.register(_stop_children)
if not DB_ERROR:
    sync.start_push()

if __name__ in {"__main__", "__mp_main__"}:
    multiprocessing.freeze_support()
    if multiprocessing.current_process().name == "MainProcess":
        _cleanup_stale()
    logs.setup()
    app.native.start_args["func"] = _native_context_menu  # s'exécute dans le processus de la fenêtre native
    app.native.start_args.update(_webview_args())
    _conn = {}  # moment de la dernière déconnexion de la fenêtre : de quoi expliquer un rechargement dans le journal

    def _client_connected(client) -> None:
        gone = _conn.pop("t", None)
        if gone is not None:
            log.info("page reconnectée [client %s] après %.1f s (sans rechargement)", client.id[:6], time.time() - gone)

    def _client_disconnected(client) -> None:
        _conn["t"] = time.time()
        log.info("page déconnectée du serveur [client %s] (veille, fenêtre masquée ou réseau local coupé)", client.id[:6])

    app.on_connect(_client_connected)
    app.on_disconnect(_client_disconnected)
    perf.mark("ui.run lancé")
    ui.run(
        # Délai laissé à la fenêtre pour se reconnecter avant que le serveur ne l'oublie (et que la page se recharge, en perdant ce qui était ouvert).
        # Le réglage par défaut (3 s) est trop court : une veille, une fenêtre masquée ou sur un autre bureau suffit à le dépasser.
        reconnect_timeout=3600,
        title="Velora", favicon="✉️", dark=None, reload=False, show=False, language=i18n.QUASAR[i18n.language()],
        native=not SERVER_MODE, window_size=None if SERVER_MODE else (1360, 860),  # window_size seul suffit à ouvrir une fenêtre
        host="0.0.0.0" if SERVER_MODE else "127.0.0.1", port=8765 if SERVER_MODE else native.find_open_port(),
        storage_secret=config.ensure("SESSION_SECRET"),
    )
