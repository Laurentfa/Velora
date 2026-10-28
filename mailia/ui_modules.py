"""Paramètres › Modules : activer les modules personnels et voir leur état."""
from nicegui import ui

from mailia import db, modules
from mailia.ui_common import reveal


def modules_settings() -> None:
    ui.label("Modules").classes("text-h6").style("font-weight:700")
    ui.label("Fonctionnalités personnelles ajoutées à Velora, rangées dans un dossier à part. Un module s'exécute avec vos droits : "
             "n'activez que ceux dont vous êtes sûr. Un changement s'applique au prochain lancement de Velora.").classes("text-body2 text-grey")
    box = ui.column().classes("w-full gap-2 q-mt-sm")

    def set_master(on: bool) -> None:
        db.kv_set("modules_on", "1" if on else "0")
        draw()

    def set_module(mid: str, on: bool) -> None:
        modules.set_enabled(mid, on)
        draw()

    def state_of(info: dict, wanted: bool) -> tuple[str, str]:
        """(texte, couleur) : ce que Velora fait de ce module, maintenant et au prochain lancement."""
        loaded = modules.status.get(info["id"]) == "ok"
        if info.get("error"):
            return info["error"], "negative"
        err = modules.status.get(info["id"])
        if err and err != "ok":
            return f"Erreur au chargement : {err}", "negative"
        if wanted and loaded:
            return "Chargé", "positive"
        if wanted:
            return "S'activera au prochain lancement", "primary"
        if loaded:
            return "Sera retiré au prochain lancement", "primary"
        return "Désactivé", "grey"

    def draw() -> None:
        box.clear()
        with box:
            on = modules.master_on()
            with ui.card().classes("w-full").props("flat bordered"):
                with ui.row().classes("items-center no-wrap w-full gap-3"):
                    ui.icon("toggle_on", size="28px").style("color:var(--accent)")
                    with ui.column().classes("col gap-0"):
                        ui.label("Activer les modules").style("font-weight:600")
                        ui.label("Interrupteur général : éteint, aucun module n'est chargé.").classes("text-caption text-grey")
                    ui.switch(value=on, on_change=lambda e: set_master(bool(e.value))).props("dense").mark("modules-switch")
            if not on:
                return
            found = modules.discover()
            if not found:
                ui.label("Aucun module installé.").classes("text-body2 text-grey q-mt-sm")
            for info in found:
                wanted = info["id"] in modules.enabled_ids() and not info.get("error")
                text, color = state_of(info, wanted)
                with ui.card().classes("w-full").props("flat bordered"):
                    with ui.row().classes("items-start no-wrap w-full gap-3"):
                        ui.icon("extension", size="28px").style("color:var(--accent);margin-top:2px")
                        with ui.column().classes("col gap-0"):
                            with ui.row().classes("items-baseline gap-2"):
                                ui.label(info["name"]).style("font-weight:600")
                                if info["version"]:
                                    ui.label(f"v{info['version']}").classes("text-caption text-grey")
                            if info["description"]:
                                ui.label(info["description"]).classes("text-body2 text-grey")
                            ui.label(text).classes(f"text-caption text-{color}" if color != "grey" else "text-caption text-grey").style("font-weight:600;margin-top:4px")
                        sw = ui.switch(value=wanted, on_change=lambda e, i=info["id"]: set_module(i, bool(e.value))).props("dense").mark(f"module-{info['id']}")
                        if info.get("error"):
                            sw.disable()
            with ui.row().classes("items-center gap-3 q-mt-sm"):
                ui.button("Ouvrir le dossier des modules", icon="folder_open",
                          on_click=lambda: (modules.DIR.mkdir(parents=True, exist_ok=True), reveal(modules.DIR))).props("outline no-caps color=primary")
                ui.label("Pour ajouter un module, copiez son dossier ici ; pour le supprimer, désactivez-le puis mettez son dossier à la corbeille.").classes("text-caption text-grey col")

    draw()
