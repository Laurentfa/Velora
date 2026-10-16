"""Zoom de l'interface (comme celui d'un navigateur : 70 % à 150 %) et densité de la liste (normale ou compacte).

Le zoom est appliqué à la fenêtre native elle-même (voir native_menu.py), pas simulé en CSS : les hauteurs de page, les menus et les fenêtres
restent justes. Ici : le réglage mémorisé et les raccourcis clavier (⌘ + / ⌘ − / ⌘ 0 sur Mac, Ctrl sur Windows)."""
from nicegui import ui

from mailia import db

ZOOMS = (0.7, 0.8, 0.9, 1.0, 1.1, 1.2, 1.3, 1.5)

KEYS_JS = """if (!window.__zoomKeys) {
  window.__zoomKeys = true;
  document.addEventListener('keydown', e => {
    if (!(e.metaKey || e.ctrlKey) || e.altKey) return;
    const d = (e.key === '+' || e.key === '=') ? 1 : (e.key === '-' || e.key === '_') ? -1 : e.key === '0' ? 0 : null;
    if (d === null) return;
    e.preventDefault(); e.stopPropagation();
    emitEvent('uizoom', {d: d});
  }, true);
}"""


def nearest(z: float) -> float:
    return min(ZOOMS, key=lambda v: abs(v - z))


def zoom() -> float:
    try:
        return nearest(float(db.kv_get("ui_zoom") or 1))
    except ValueError:
        return 1.0


def set_zoom(z: float) -> float:
    z = nearest(z)
    db.kv_set("ui_zoom", str(z))  # la fenêtre native lit ce réglage et l'applique dans la seconde
    return z


def step(direction: int) -> float:
    """+1 : plus grand, -1 : plus petit, 0 : taille normale (100 %)."""
    if direction == 0:
        return set_zoom(1.0)
    i = ZOOMS.index(zoom()) + (1 if direction > 0 else -1)
    return set_zoom(ZOOMS[max(0, min(len(ZOOMS) - 1, i))])


def label(z: float) -> str:
    return f"{round(z * 100)} %"


def install_keys() -> None:
    """Raccourcis clavier de la page : le zoom change et un petit message l'indique."""
    def on_key(e) -> None:
        z = step(int((e.args or {}).get("d", 0)))
        ui.notify(f"Zoom {label(z)}", position="bottom", timeout=900, type="info")
    ui.on("uizoom", on_key)
    ui.run_javascript(KEYS_JS)


def status() -> str:
    """Ce que la fenêtre native a répondu à la dernière demande de zoom (« ok » ou la raison de l'échec)."""
    return db.kv_get("ui_zoom_status") or ""


def compact() -> bool:
    return db.kv_get("list_density") == "compact"


def set_compact(on: bool) -> None:
    db.kv_set("list_density", "compact" if on else "normal")
    ui.run_javascript(f"document.body.classList.toggle('compact', {'true' if on else 'false'})")
