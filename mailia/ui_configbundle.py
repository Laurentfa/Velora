"""Paramètres › Utilisation et données : exporter / importer la configuration (réglages, boîtes sans mot de passe, modules)."""
import tempfile
from datetime import datetime
from pathlib import Path

from nicegui import run, ui

from mailia import configbundle
from mailia.ui_common import reveal


def panel() -> None:
    ui.label("Configuration").classes("text-subtitle2 q-mt-md")
    ui.label("Pour installer Velora sur un autre ordinateur sans tout ressaisir : réglages, boîtes mail et modules. Les clés d'API et les mots de passe ne sont jamais "
             "exportés (à saisir de nouveau sur l'autre machine). L'import ne remplace rien de ce qui existe déjà, sauf un module par une version plus récente.").classes(
        "text-caption text-grey")

    async def export() -> None:
        dest = Path.home() / "Downloads"
        dest = (dest if dest.is_dir() else Path.home()) / f"Velora-configuration-{datetime.now():%Y%m%d-%H%M}.zip"
        try:
            n = await run.io_bound(configbundle.export_bundle, dest)
        except OSError as e:
            return ui.notify(f"Export impossible : {e}", type="negative", multi_line=True)
        ui.notify(f"Configuration exportée : {n['settings']} réglage(s), {n['accounts']} boîte(s), {n['modules']} module(s) — {dest.name}", type="positive", multi_line=True)
        reveal(dest)

    async def on_upload(e) -> None:
        data = await e.file.read()
        e.sender.reset()
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "configuration.zip"
            f.write_bytes(data)
            try:
                r = await run.io_bound(configbundle.import_bundle, f)
            except (ValueError, OSError) as err:
                return ui.notify(str(err), type="negative", multi_line=True)
        ui.notify(f"Configuration importée : {r['settings']} réglage(s), {r['accounts']} boîte(s), {r['modules']} module(s) ajouté(s), {r['updated']} mis à jour. "
                  "Quittez et relancez Velora pour que tout soit pris en compte, puis saisissez les mots de passe et la clé d'IA.", type="positive", multi_line=True, timeout=0,
                  close_button="OK")

    with ui.row().classes("items-center gap-3"):
        ui.button("Exporter la configuration", icon="upload", on_click=export).props("outline no-caps dense").mark("config-export")
        ui.upload(on_upload=on_upload, auto_upload=True, label="Importer une configuration (.zip)").props("accept=application/zip,.zip flat dense").classes("config-import").mark("config-import")
