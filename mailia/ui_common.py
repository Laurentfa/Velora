"""Éléments communs de l'interface : éditeur de texte riche, catégories, vues, formats de date et de taille."""
import json
import logging
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from nicegui import ui

from mailia import config, db, i18n, sync


SERVER_MODE = "--server" in sys.argv


log = logging.getLogger("mailia")


EDITOR_FONTS = {"roboto": "Roboto", "arial": "Arial", "calibri": "Calibri", "cambria": "Cambria", "georgia": "Georgia", "times": "Times New Roman",
                "verdana": "Verdana", "tahoma": "Tahoma", "trebuchet": "Trebuchet MS", "courier": "Courier New", "comic": "Comic Sans MS"}


_COLORS = [("Noir", "#000000", "⬛"), ("Rouge", "#d92d3a", "🟥"), ("Orange", "#e8710a", "🟧"), ("Vert", "#1a8f3c", "🟩"),
           ("Bleu", "#0a66e8", "🟦"), ("Violet", "#7c3aed", "🟪"), ("Gris", "#666666", "◻️")]


_HILITES = [("Jaune", "#fff3a3", "🟨"), ("Vert", "#c8f2d0", "🟩"), ("Bleu", "#cfe3ff", "🟦"), ("Rose", "#ffd6e0", "🟥"),
            ("Orange", "#ffe1c2", "🟧"), ("Aucun", "transparent", "⬜")]


_SIZES = [8, 9, 10, 10.5, 11, 12, 14, 16, 18, 20, 22, 24, 26, 28, 36, 48, 72]  # en points, comme Outlook
_DEFAULT_SIZE = 11


_STYLES = [("Texte normal", "P"), ("Titre 1", "H1"), ("Titre 2", "H2"), ("Titre 3", "H3")]
_SPACINGS = [("Simple", 1), ("1,15 (par défaut)", 1.15), ("1,5", 1.5), ("Double", 2)]


_TABLE = ('<table border="1" cellpadding="6" style="border-collapse:collapse;width:100%">'
          + "<tr>" + "<td>&nbsp;</td>" * 3 + "</tr>" + "<tr>" + "<td>&nbsp;</td>" * 3 + "</tr>" + "<tr>" + "<td>&nbsp;</td>" * 3 + "</tr></table><p><br></p>")


def _editor_config() -> tuple[list, dict]:
    defs: dict = {}
    for i, (name, hexv, ico) in enumerate(_COLORS):
        defs[f"col{i}"] = {"label": f"{ico}  {name}", "tip": f"{ico}  {name}", "cmd": "foreColor", "param": hexv}
    for i, (name, hexv, ico) in enumerate(_HILITES):
        defs[f"hl{i}"] = {"label": f"{ico}  {name}", "tip": f"{ico}  {name}", "cmd": "hiliteColor", "param": hexv}
    emos = ["😀", "😊", "😉", "😍", "🙂", "😅", "🙏", "👍", "👏", "🎉", "✅", "❌", "⚠️", "📎", "📞", "📅", "ⓘ", "💡", "🚀", "❤️"]
    for i, e in enumerate(emos):
        defs[f"em{i}"] = {"label": e, "tip": e, "cmd": "insertText", "param": e}
    for i, (name, tag) in enumerate(_STYLES):
        defs[f"st{i}"] = {"label": name, "tip": name, "cmd": "formatBlock", "param": tag}
    defs["chk"] = {"icon": "check_box_outline_blank", "tip": "Case à cocher", "cmd": "insertText", "param": "☐ "}
    defs["tbl"] = {"icon": "table_chart", "tip": "Insérer un tableau…", "cmd": "insertHTML", "param": _TABLE}
    toolbar = [
        ["undo", "redo"],
        [{"label": "Police", "icon": "font_download", "fixedLabel": True, "fixedIcon": True, "list": "no-icons",
          "options": ["default_font", *EDITOR_FONTS]},
         {"label": "Taille", "icon": "format_size", "fixedLabel": True, "fixedIcon": True, "list": "no-icons",
          "options": [f"sz{i}" for i in range(len(_SIZES))]}],
        [{"label": "Style", "icon": "title", "fixedLabel": True, "fixedIcon": True, "list": "no-icons", "tip": "Style de paragraphe (titres)",
          "options": [f"st{i}" for i in range(len(_STYLES))]}],
        ["bold", "italic", "underline", "strike"],
        [{"icon": "format_color_text", "tip": "Couleur du texte", "fixedIcon": True, "list": "no-icons", "options": [f"col{i}" for i in range(len(_COLORS))]},
         {"icon": "highlight", "tip": "Surligner le texte", "fixedIcon": True, "list": "no-icons", "options": [f"hl{i}" for i in range(len(_HILITES))]}],
        ["left", "center", "right", "justify"],
        ["unordered", "ordered", "outdent", "indent", "chk",
         {"icon": "format_line_spacing", "tip": "Interligne", "fixedIcon": True, "list": "no-icons", "options": [f"ls{i}" for i in range(len(_SPACINGS))]}],
        ["quote", "link", "hr", "tbl", "imgp",
         {"icon": "mood", "tip": "Insérer un emoji", "fixedIcon": True, "list": "no-icons", "options": [f"em{i}" for i in range(len(emos))]}],
        ["subscript", "superscript", "removeFormat"],
        ["fullscreen"],
    ]
    return toolbar, defs


def _definitions_js(defs: dict) -> str:
    """Définitions de la barre d'outils, en JavaScript : l'interligne et l'image appellent des fonctions de la page (voir ui_assets)."""
    extra = {}
    for i, pt in enumerate(_SIZES):
        label = f"{str(pt).replace('.', ',')}" + (" (normal)" if pt == _DEFAULT_SIZE else "")
        extra[f"sz{i}"] = {"label": label, "tip": f"{str(pt).replace('.', ',')} pt" + (" (normal)" if pt == _DEFAULT_SIZE else ""), "handler": f"@@window.mailiaSize({pt})@@"}
    extra |= {f"ls{i}": {"label": name, "tip": name, "handler": f"@@window.mailiaLine({v})@@"} for i, (name, v) in enumerate(_SPACINGS)}
    extra["imgp"] = {"icon": "add_photo_alternate", "tip": "Insérer une image", "handler": "@@window.mailiaPickImage()@@"}
    raw = json.dumps({**defs, **extra}, ensure_ascii=False)
    return re.sub(r'"@@(.*?)@@"', lambda m: "() => " + m.group(1).replace('\\"', '"'), raw)


def make_editor(value: str, on_change, min_height: str = "240px") -> ui.editor:
    """Éditeur de texte riche (HTML) : tout ce que propose Outlook, plus des tableaux, des emojis et du surlignage."""
    toolbar, defs = _editor_config()
    ed = ui.editor(value=value, on_change=on_change).classes("w-full mailedit")
    ed._props.update({"toolbar": toolbar, ":definitions": _definitions_js(defs), "fonts": EDITOR_FONTS, "min-height": min_height})
    return ed


CAT = {"action": "Action", "info": "Info", "newsletter": "Newsletter", "notification": "Notification", "personnel": "Perso"}


VIEWS = {"brief": ("✦", "Brief du jour"), "inbox": ("✉", "Boîte de réception"), "todo": ("⚑", "À traiter"),
         "unread": ("●", "Non lus"), "noise": ("☰", "Newsletters & notifications"), "newsletter": ("☰", "Newsletters"), "notification": ("ⓘ", "Notifications"), "drafts": ("✎", "Brouillons"),
         "outbox": ("⏰", "En attente d'envoi"), "snoozed": ("⏳", "Reportés"), "followups": ("↩", "À relancer"), "tasks": ("☑", "Tâches")}


NOLIST_VIEWS = {"brief", "drafts", "outbox", "snoozed", "followups", "tasks"}  # vues sans recherche ni liste de mails


JOURS, JOURS_LONG, MOIS, MOIS_COURTS = i18n.calendar_names()  # jours et mois dans la langue de l'interface


def full_date(iso: str) -> str:
    d = datetime.fromisoformat(iso).astimezone()
    return f"{JOURS_LONG[d.weekday()]} {d.day} {MOIS[d.month - 1]} {d.year} {i18n.at()} {d:%H:%M}"


def short_date(iso: str) -> str:
    """« 2 oct., 11:34 » (avec l'année si ce n'est pas l'année en cours)."""
    d = datetime.fromisoformat(iso).astimezone()
    year = "" if d.year == datetime.now().year else f" {d.year}"
    return f"{d.day} {MOIS_COURTS[d.month - 1]}{year}, {d:%H:%M}"


def ago(iso: str) -> str:
    d = datetime.fromisoformat(iso).astimezone()
    now = datetime.now(timezone.utc).astimezone()
    mins = (now - d).total_seconds() / 60
    if mins < 1:
        return "à l'instant"
    if mins < 60:
        return f"{int(mins)} min"
    if d.date() == now.date():
        return d.strftime("%H:%M")
    if mins < 60 * 24 * 6:
        return JOURS[d.weekday()]
    return d.strftime("%d/%m")


def initials(name: str) -> str:
    parts = (name or "?").split("<")[0].split("@")[0].split()
    return ("".join(w[0] for w in parts[:2]) or "?").upper()


def human_size(n: int) -> str:
    for unit in ("o", "Ko", "Mo", "Go"):
        if n < 1024 or unit == "Go":
            return f"{n:.0f} {unit}" if unit == "o" else f"{n:.1f} {unit}"
        n /= 1024


def reveal(path: Path) -> None:
    """Montre le fichier dans le Finder / l'Explorateur, sans jamais l'ouvrir (une pièce jointe peut être dangereuse)."""
    try:
        if sys.platform == "darwin":
            subprocess.Popen(["open", "-R", str(path)])
        elif sys.platform.startswith("win"):
            subprocess.Popen(["explorer", "/select,", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path.parent)])
    except Exception:
        pass


def open_folder(path: Path) -> None:
    """Ouvre un dossier dans le Finder / l'Explorateur."""
    try:
        if sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        elif sys.platform.startswith("win"):
            os.startfile(str(path))  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except Exception:
        pass


PRINTABLE = {"pdf", "png", "jpg", "jpeg", "gif", "webp", "bmp", "heic", "heif", "txt", "csv", "tsv", "log", "md", "json", "xml",
             "docx", "xlsx", "pptx"}  # jamais de formats à macros (docm…), de scripts, de courriers (eml) ni d'archives
PRINT_DIR_PREFIX = "mailia-impression-"


def can_print(name: str) -> bool:
    return (name or "").rsplit(".", 1)[-1].lower() in PRINTABLE and "." in (name or "")


def open_to_print(name: str, data: bytes) -> Path:
    """Enregistre la pièce jointe dans un dossier temporaire et l'ouvre dans l'application du système (Aperçu, Word…), où l'on imprime avec
    ⌘P / Ctrl+P. Le fichier est marqué « téléchargé » pour que Word et Excel l'ouvrent en mode protégé. Les dossiers de plus d'un jour sont supprimés."""
    import shutil
    import tempfile
    import time
    root = Path(tempfile.gettempdir())
    for old in root.glob(PRINT_DIR_PREFIX + "*"):
        try:
            if time.time() - old.stat().st_mtime > 86400:
                shutil.rmtree(old, ignore_errors=True)
        except OSError:
            pass
    safe = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", os.path.basename(name)).strip(". ") or "piece-jointe"
    if not can_print(safe):
        raise ValueError("Ce type de fichier ne s'imprime pas depuis l'aperçu : téléchargez-le pour l'ouvrir.")
    path = Path(tempfile.mkdtemp(prefix=PRINT_DIR_PREFIX)) / safe
    path.write_bytes(data)
    try:
        if sys.platform == "darwin":
            subprocess.run(["xattr", "-w", "com.apple.quarantine", "0081;00000000;Velora;", str(path)], check=False, timeout=10)
        elif sys.platform.startswith("win"):
            with open(str(path) + ":Zone.Identifier", "w") as z:
                z.write("[ZoneTransfer]\nZoneId=3\n")
    except Exception:
        pass
    if sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    elif sys.platform.startswith("win"):
        os.startfile(str(path))  # type: ignore[attr-defined]
    else:
        subprocess.Popen(["xdg-open", str(path)])
    return path


_launched = {"done": False}  # la première ouverture d'une fenêtre depuis le lancement de l'appli applique la boîte par défaut


def default_account() -> str:
    """Boîte à afficher à l'ouverture de l'appli (réglage de l'utilisateur) ; vide : toutes les boîtes. Ignorée si elle a été retirée ou désactivée."""
    key = db.kv_get("default_account") or ""
    cfg = sync.ACCOUNTS.get(key)
    return key if cfg is not None and cfg.enabled else ""


def initial_view() -> tuple[str, str, str]:
    """(vue, boîte, dossier) à l'ouverture d'une fenêtre. Le brief du jour s'affiche une seule fois par jour, à la première ouverture ; ensuite
    (y compris quand la fenêtre se recharge toute seule : réveil du Mac, coupure réseau…) on retrouve la vue où l'on était."""
    today = datetime.now().strftime("%Y-%m-%d")
    default = default_account()
    if default and not _launched["done"]:  # lancement de l'appli : on repart de la boîte choisie (les autres restent à sélectionner à la main)
        db.kv_set("ui_last", json.dumps({"view": "inbox", "account": default, "folder": ""}))
        db.kv_set("ui_open", "")
    _launched["done"] = True
    if config.ai_enabled() and db.kv_get("brief_day") != today:
        db.kv_set("brief_day", today)
        return "brief", default, ""
    try:
        last = json.loads(db.kv_get("ui_last") or "{}")
    except ValueError:
        last = {}
    view = last.get("view")
    if (view not in VIEWS and view != "folder") or (view == "brief" and not config.ai_enabled()):
        return "inbox", "", ""
    account = last.get("account") or ""
    folder = last.get("folder") or ""
    if account and account not in sync.ACCOUNTS:
        account = ""
    return view, account, folder if view == "folder" else ""


def label_of(key: str) -> str:
    c = sync.ACCOUNTS.get(key)
    return c.label if c else key
