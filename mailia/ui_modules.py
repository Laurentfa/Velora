"""Paramètres › Modules : activer les modules personnels et voir leur état."""
import tempfile
from pathlib import Path

from nicegui import app, run, ui

from mailia import db, modules


def modules_settings() -> None:
    ui.label("Modules").classes("text-h6").style("font-weight:700")
    ui.label("Fonctionnalités personnelles ajoutées à Velora, rangées dans un dossier à part. Un module s'exécute avec vos droits : "
             "n'activez que ceux dont vous êtes sûr. Un changement s'applique tout de suite.").classes("text-body2 text-grey")
    box = ui.column().classes("w-full gap-2 q-mt-sm")

    def set_master(on: bool) -> None:
        db.kv_set("modules_on", "1" if on else "0")
        try:
            modules.apply()
        except Exception as e:  # noqa: BLE001
            modules.log.exception("modules : échec de l'application du changement")
            ui.notify(f"Le changement est enregistré, mais n'a pas pu être appliqué tout de suite ({type(e).__name__}). Relancez Velora.", type="warning")
        draw()

    def set_module(mid: str, on: bool) -> None:
        modules.set_enabled(mid, on)
        try:
            modules.apply(mid)
        except Exception as e:  # noqa: BLE001 — le réglage est enregistré ; on le dit au lieu de laisser l'interrupteur sans réaction
            modules.log.exception("module %s : échec de l'application du changement", mid)
            ui.notify(f"Le changement est enregistré, mais n'a pas pu être appliqué tout de suite ({type(e).__name__}). Relancez Velora.", type="warning")
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
            return "En cours d'activation", "primary"
        if loaded:
            return "En cours de retrait", "primary"
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
                        src = db.kv_get(f"module:{info['id']}:src") or ""
                        if src and Path(src).is_dir():
                            ui.button(icon="sync", on_click=lambda i=info["id"], p=src: update_from_source(i, p)).props("flat round dense color=primary").tooltip(
                                f"Mettre à jour depuis {src}").mark(f"module-update-{info['id']}")
                        ui.button(icon="delete_outline", on_click=lambda i=info: ask_remove(i)).props("flat round dense color=grey").tooltip("Supprimer ce module").mark(f"module-delete-{info['id']}")
            with ui.row().classes("items-center gap-2 q-mt-sm w-full no-wrap"):
                repo = ui.input("Dépôt GitHub des modules", value=db.kv_get("modules_repo") or "", placeholder="moncompte/mes-modules").props("dense outlined stack-label").classes("col").mark("modules-repo")
                repo.on("blur", lambda: db.kv_set("modules_repo", (repo.value or "").strip()))
                ui.button("Mettre à jour depuis le dépôt", icon="cloud_download", on_click=lambda: update_from_repo((repo.value or "").strip())).props("outline no-caps").mark("modules-repo-update")
            ui.label("À renseigner : le dépôt GitHub qui contient vos modules, sous la forme « compte/dépôt » (même privé), par exemple moncompte/mes-modules. Velora met à jour "
                     "les modules déjà installés dont la version du dépôt est plus récente. Il faut l'outil « gh » (cli.github.com), connecté une fois avec « gh auth login » "
                     "dans le Terminal.").classes("text-caption text-grey")
            with ui.row().classes("items-center gap-3 q-mt-sm"):
                with ui.button("Importer un module", icon="upload").props("outline no-caps color=primary").mark("module-import"):
                    with ui.menu():
                        ui.menu_item("Depuis un dossier…", on_click=pick_folder)
                        ui.menu_item("Depuis une archive .zip…").on("click", js_handler=f"() => getElement({up.id}).$refs.qRef.pickFiles()")
                ui.label("Un module importé est copié dans le dossier de Velora ; activez-le ensuite avec son interrupteur.").classes("text-caption text-grey col")

    async def import_from(path: Path) -> None:
        try:
            try:
                mids = await run.io_bound(modules.import_bundle, path)
            except FileExistsError as e:
                with ui.dialog() as cd, ui.card():
                    ui.label(f"{'Le module' if ',' not in str(e) else 'Les modules'} « {e} » existe{'nt' if ',' in str(e) else ''} déjà. Remplacer ?").classes("text-subtitle1")
                    ui.label("Ses fichiers sont remplacés ; ses réglages sont conservés.").classes("text-caption text-grey")
                    with ui.row().classes("justify-end w-full gap-2"):
                        ui.button("Annuler", on_click=lambda: cd.submit(False)).props("flat no-caps")
                        ui.button("Remplacer", on_click=lambda: cd.submit(True)).props("unelevated no-caps color=primary")
                if not await cd:
                    return
                mids = await run.io_bound(modules.import_bundle, path, True)
        except (ValueError, OSError) as err:
            return ui.notify(str(err), type="negative")
        if path.is_dir() and len(mids) == 1:
            db.kv_set(f"module:{mids[0]}:src", str(path))  # permet « Mettre à jour » depuis le même dossier
        ui.notify(f"Module « {mids[0]} » importé. Activez-le avec son interrupteur." if len(mids) == 1
                  else f"{len(mids)} modules importés : {', '.join(mids)}. Activez-les avec leurs interrupteurs.", type="positive", multi_line=True)
        for mid in mids:
            modules.apply(mid)  # un module remplacé est rechargé tout de suite
        draw()

    async def update_from_repo(repo: str) -> None:
        db.kv_set("modules_repo", repo)
        ui.notify("Recherche de mises à jour…", timeout=1500)
        try:
            done = await run.io_bound(modules.update_from_repo, repo)
        except (ValueError, OSError) as err:
            return ui.notify(str(err), type="negative", multi_line=True)
        if not done:
            return ui.notify("Tous les modules installés sont à jour.", type="positive")
        failed = []
        for mid, _old, _new in done:
            try:
                modules.apply(mid)
            except Exception:  # noqa: BLE001
                modules.log.exception("module %s : échec du rechargement", mid)
                failed.append(mid)
        ui.notify("Mis à jour : " + ", ".join(f"{m} {o or '?'} → {n}" for m, o, n in done) + (f". À relancer : {', '.join(failed)}." if failed else "."),
                  type="warning" if failed else "positive", multi_line=True)
        draw()

    async def update_from_source(mid: str, src: str) -> None:
        try:
            await run.io_bound(modules.import_module, Path(src), True)
        except (ValueError, OSError) as err:
            return ui.notify(f"Mise à jour impossible : {err}", type="negative", multi_line=True)
        try:
            modules.apply(mid)
        except Exception as e:  # noqa: BLE001
            modules.log.exception("module %s : échec du rechargement", mid)
            ui.notify(f"Module mis à jour, mais pas rechargé ({type(e).__name__}). Relancez Velora.", type="warning")
        else:
            ui.notify(f"Module « {mid} » mis à jour depuis {src}.", type="positive", multi_line=True)
        draw()

    async def pick_folder() -> None:
        try:
            import webview
            kind = webview.FileDialog.FOLDER if hasattr(webview, "FileDialog") else webview.FOLDER_DIALOG
            res = await app.native.main_window.create_file_dialog(kind)
        except Exception:
            return ui.notify("La fenêtre de choix de dossier n'est pas disponible : importez plutôt une archive .zip.", type="warning")
        if res:
            await import_from(Path(res[0]))

    async def on_zip(e) -> None:
        data = await e.file.read()
        e.sender.reset()
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / (Path(e.file.name).name or "module.zip")
            f.write_bytes(data)
            await import_from(f)

    async def ask_remove(info: dict) -> None:
        with ui.dialog() as dlg, ui.card().style("max-width:480px"):
            ui.label(f"Supprimer le module « {info['name']} » ?").classes("text-h6")
            ui.label("Son dossier et ses réglages sont supprimés, sans retour possible. Pour le reprendre, il faudra le réimporter.").classes("text-body2")
            with ui.row().classes("justify-end w-full gap-2"):
                ui.button("Annuler", on_click=lambda: dlg.submit(False)).props("flat no-caps")
                ui.button("Supprimer", on_click=lambda: dlg.submit(True)).props("unelevated no-caps color=negative").mark("module-delete-confirm")
        if await dlg:
            await run.io_bound(modules.remove_module, info["id"])
            modules.apply(info["id"])
            ui.notify("Module supprimé.", type="positive")
            draw()

    up = ui.upload(on_upload=on_zip, auto_upload=True).props("hide-upload-btn accept=.zip").style(
        "position:absolute;left:-9999px;width:1px;height:1px;overflow:hidden")

    draw()
