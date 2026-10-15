"""Interface de la mise à jour : proposition au démarrage, bouton « Rechercher » dans les paramètres."""
import os

from nicegui import app, run, ui

from mailia import db, updater
from mailia.version import APP_VERSION


async def install(info: dict) -> None:
    """Télécharge, vérifie, puis ferme l'appli : un petit script la remplace et la relance."""
    with ui.dialog().props("persistent") as dlg, ui.card().classes("items-center q-pa-lg").style("min-width:320px"):
        ui.spinner(size="lg")
        ui.label(f"Téléchargement de Velora {info['version']}…").classes("text-subtitle1")
        ui.label("L'application va redémarrer toute seule.").classes("text-caption text-grey")
    dlg.open()
    try:
        archive = await run.io_bound(updater.download, info)
        updater.install(archive, os.getpid())
    except Exception as e:
        dlg.close()
        return ui.notify(f"Mise à jour impossible : {e}", type="negative", multi_line=True)
    app.shutdown()


def offer(info: dict) -> None:
    async def go() -> None:
        dlg.close()
        await install(info)

    with ui.dialog() as dlg, ui.card().style("width:min(520px,92vw)"):
        ui.label(f"Velora {info['version']} est disponible").classes("text-h6").style("font-weight:700")
        ui.label(f"Vous utilisez la version {APP_VERSION}.").classes("text-caption text-grey")
        if info["notes"]:
            ui.label(info["notes"]).classes("text-body2").style("white-space:pre-wrap;max-height:240px;overflow:auto")
        if not updater.can_install():
            ui.label("Cette copie de Velora ne peut pas se remplacer toute seule (elle n'est pas dans un dossier modifiable, par exemple "
                     "Applications). Téléchargez la nouvelle version depuis la page des versions.").classes("text-caption text-negative")
        with ui.row().classes("w-full justify-end"):
            ui.button("Plus tard", on_click=dlg.close).props("flat no-caps")
            ui.button("Mettre à jour maintenant", icon="system_update", on_click=go).props("unelevated no-caps color=primary").set_enabled(updater.can_install())
    dlg.open()


async def auto_check() -> None:
    """Au démarrage, une fois par jour au plus : une simple notification, jamais de fenêtre qui interrompt le travail."""
    info = await run.io_bound(updater.check)
    if info:
        ui.notify(f"Velora {info['version']} est disponible.", type="info", timeout=0, close_button="Plus tard",
                  actions=[{"label": "Voir", "color": "white", "handler": lambda: offer(info)}])


def panel() -> None:
    """Bloc « Version » des paramètres."""
    ui.label("Version").classes("text-subtitle1").style("font-weight:700")
    ui.label(f"Velora {APP_VERSION}").classes("text-body2")
    if not updater.available():
        return ui.label("Les mises à jour automatiques ne sont pas configurées pour cette copie.").classes("text-caption text-grey")
    status = ui.label("").classes("text-caption text-grey")

    async def search() -> None:
        status.set_text("Recherche en cours…")
        try:
            info = await run.io_bound(updater.fetch_latest)
        except Exception as e:
            return status.set_text(f"Recherche impossible : {e}")
        status.set_text("Votre version est à jour." if info is None else f"La version {info['version']} est disponible.")
        if info:
            offer(info)

    ui.button("Rechercher une mise à jour", icon="system_update", on_click=search).props("flat no-caps dense").mark("update-search")
    ui.switch("Vérifier automatiquement une fois par jour", value=db.kv_get("update_auto") != "0",
              on_change=lambda e: db.kv_set("update_auto", "1" if e.value else "0"))
