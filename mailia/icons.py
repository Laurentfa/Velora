"""Icônes de la colonne de gauche : traits fins aux bouts arrondis, dans le style des icônes d'Apple Mail (boîte de réception, dossier, brouillon, avion en papier,
corbeille, archives…). Dessinées pour Velora : elles s'affichent pareil sur Mac et sur Windows et prennent la couleur du texte (currentColor)."""
from __future__ import annotations

_P = {
    "tray": ["M3.2 13.2l2.3-6.4A2 2 0 0 1 7.4 5.5h9.2a2 2 0 0 1 1.9 1.3l2.3 6.4v4.3a1.7 1.7 0 0 1-1.7 1.7H4.9a1.7 1.7 0 0 1-1.7-1.7z",
             "M3.2 13.2h4.6c.5 0 .8.2 1 .6l.5 1.1c.2.4.6.6 1 .6h3.4c.4 0 .8-.2 1-.6l.5-1.1c.2-.4.5-.6 1-.6h4.6"],
    "tray2": ["M5.5 4h13", "M3.2 12l2-5.2A1.8 1.8 0 0 1 6.9 5.7h10.2a1.8 1.8 0 0 1 1.7 1.1l2 5.2v5.3a1.7 1.7 0 0 1-1.7 1.7H4.9a1.7 1.7 0 0 1-1.7-1.7z",
              "M3.2 12h4.6c.5 0 .8.2 1 .6l.5 1.1c.2.4.6.6 1 .6h3.4c.4 0 .8-.2 1-.6l.5-1.1c.2-.4.5-.6 1-.6h4.6"],
    "folder": ["M3.4 7.6a2 2 0 0 1 2-2h3.2a2 2 0 0 1 1.5.7l1 1.1c.4.4.9.7 1.5.7h6a2 2 0 0 1 2 2v7.4a2 2 0 0 1-2 2H5.4a2 2 0 0 1-2-2z"],
    "doc": ["M7 3.5h6.4l5.1 5.1V19A1.5 1.5 0 0 1 17 20.5H7A1.5 1.5 0 0 1 5.5 19V5A1.5 1.5 0 0 1 7 3.5z", "M13.4 3.5v5.1h5.1"],
    "plane": ["M20.6 3.4L3.5 10.5l6.5 2.5 2.5 6.5z", "M10 13L20.6 3.4"],
    "junk": ["M3.6 6.2h16.8v3H3.6z", "M5.2 9.2v8.3a2 2 0 0 0 2 2h9.6a2 2 0 0 0 2-2V9.2", "M9.6 12.2l4.8 4.8", "M14.4 12.2L9.6 17"],
    "trash": ["M4.4 7h15.2", "M9.6 7V5.3a1.2 1.2 0 0 1 1.2-1.2h2.4a1.2 1.2 0 0 1 1.2 1.2V7", "M6.4 7l.8 11.3a2 2 0 0 0 2 1.9h5.6a2 2 0 0 0 2-1.9L17.6 7",
              "M10 10.5v6.2", "M14 10.5v6.2"],
    "archive": ["M3.6 4.8h16.8v4.4H3.6z", "M5.2 9.2v8.9a1.7 1.7 0 0 0 1.7 1.7h10.2a1.7 1.7 0 0 0 1.7-1.7V9.2", "M9.8 12.9h4.4"],
    "flag": ["M6 20.5V4", "M6 4.6h11.6l-2.5 3.8 2.5 3.8H6"],
    "sparkle": ["M12 3.4l2 5.4 5.4 2-5.4 2-2 5.4-2-5.4-5.4-2 5.4-2z", "M18.5 16.5v3.5", "M16.8 18.2h3.4"],
    "unread": ["M12 7.5a4.5 4.5 0 1 0 0 9 4.5 4.5 0 0 0 0-9z"],
    "pencil": ["M4.2 19.8l.9-4.1L16.2 4.6a1.9 1.9 0 0 1 2.7 0l.5.5a1.9 1.9 0 0 1 0 2.7L8.3 18.9z", "M14.4 6.4l3.2 3.2"],
    "clock": ["M12 3.8a8.2 8.2 0 1 0 0 16.4 8.2 8.2 0 0 0 0-16.4z", "M12 7.8V12l3 1.9"],
    "tasks": ["M5.2 4.6h13.6a1.6 1.6 0 0 1 1.6 1.6v11.6a1.6 1.6 0 0 1-1.6 1.6H5.2a1.6 1.6 0 0 1-1.6-1.6V6.2a1.6 1.6 0 0 1 1.6-1.6z", "M8.4 12.2l2.6 2.6 4.7-5"],
    "hourglass": ["M7 3.8h10", "M7 20.2h10", "M8 3.8c0 4.2 4 5 4 8.2s-4 4-4 8.2", "M16 3.8c0 4.2-4 5-4 8.2s4 4 4 8.2"],
    "reply": ["M9.2 6.8L4.6 11.4l4.6 4.6", "M5 11.4h8.4a5.6 5.6 0 0 1 5.6 5.6v1.4"],
    "lines": ["M5 6.8h14", "M5 12h14", "M5 17.2h9"],
    "bell": ["M12 4a5.5 5.5 0 0 0-5.5 5.5c0 4.2-1.7 5.6-2.1 6.6h15.2c-.4-1-2.1-2.4-2.1-6.6A5.5 5.5 0 0 0 12 4z", "M10 19.2a2.2 2.2 0 0 0 4 0"],
    "people": ["M9.2 11.2a3.2 3.2 0 1 0 0-6.4 3.2 3.2 0 0 0 0 6.4z", "M3.4 19.4a5.8 5.8 0 0 1 11.6 0", "M15.6 5.2a3.2 3.2 0 0 1 0 6", "M17.4 14.2a5.8 5.8 0 0 1 3.2 5.2"],
    "rules": ["M4.2 5.4h15.6l-6 7.2v5.2l-3.6 1.8v-7z"],
    "broom": ["M19.6 4.4l-7.2 7.2", "M10.5 9.8l3.7 3.7c-1.1 2.8-3.5 4.8-7.2 5.3-.3-3.7 1.4-6.3 3.5-9z", "M9.4 14.6l1.9 1.9"],
}

# glyphes utilisés jusqu'ici dans la colonne de gauche -> icône
GLYPHS = {"✉": "tray", "▸": "folder", "✦": "sparkle", "⚑": "flag", "●": "unread", "✎": "pencil", "⏰": "clock", "☑": "tasks", "⏳": "hourglass",
          "↩": "reply", "☰": "lines", "ⓘ": "bell", "👥": "people", "⌫": "broom", "▽": "rules"}
ROLES = {"drafts": "doc", "sent": "plane", "junk": "junk", "trash": "trash", "archive": "archive", "inbox": "tray"}

_cache: dict[tuple[str, int], str] = {}


def name_of(icon: str) -> str | None:
    """« svg:trash » -> « trash » ; un ancien glyphe (« ✉ ») -> son icône ; sinon None."""
    if icon.startswith("svg:"):
        return icon[4:] if icon[4:] in _P else None
    return GLYPHS.get(icon)


def svg(name: str, size: int = 21) -> str:
    key = (name, size)
    if key not in _cache:
        paths = "".join(f'<path d="{d}"/>' for d in _P[name])
        fill = ' fill="currentColor"' if name == "unread" else ' fill="none"'
        _cache[key] = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" viewBox="0 0 24 24"{fill} stroke="currentColor" stroke-width="1.7" '
                       f'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{paths}</svg>')
    return _cache[key]
