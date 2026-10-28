"""Paramètres › Modules : activer les modules personnels et voir leur état."""
from nicegui import ui

from mailia import db, modules
from mailia.ui_common import reveal


def modules_settings() -> None:
    ui.label("Modules").classes("text-h6").style("font-weight:700")
    ui.label("Fonctionnalités personnelles ajoutées à Velora, rangées dans un dossier à part. Un module s'exécute avec vos droits : n'activez que ceux dont vous êtes sûr. "
             "Un changement s'applique au prochain lancement de Velora.").classes("text-caption text-grey")
    box = ui.column().classes("w-full gap-1")

    def draw() -> None:
        box.clear()
        with box:
            ui.switch("Activer les modules", value=modules.master_on(), on_change=lambda e: (db.kv_set("modules_on", "1" if e.value else "0"), draw())).props("dense").mark("modules-switch")
            if not modules.master_on():
                return
            found = modules.discover()
            if not found:
                ui.label("Aucun module installé.").classes("text-caption text-grey")
            for info in found:
                with ui.row().classes("items-center no-wrap w-full gap-2"):
                    ui.switch(value=info["id"] in modules.enabled_ids() and not info.get("error"),
                              on_change=lambda e, i=info["id"]: modules.set_enabled(i, bool(e.value))).props("dense").set_enabled(not info.get("error")).mark(f"module-{info['id']}")
                    with ui.column().classes("gap-0 col"):
                        ui.label(info["name"] + (f" · v{info['version']}" if info["version"] else "")).style("font-weight:600")
                        if info["description"]:
                            ui.label(info["description"]).classes("text-caption text-grey")
                        state = info.get("error") or modules.status.get(info["id"])
                        if state and state != "ok":
                            ui.label(state).classes("text-caption text-negative")
                        elif state == "ok":
                            ui.label("Chargé").classes("text-caption text-positive")
            ui.button("Ouvrir le dossier des modules", icon="folder_open", on_click=lambda: (modules.DIR.mkdir(parents=True, exist_ok=True), reveal(modules.DIR))).props("flat dense no-caps")

    draw()
