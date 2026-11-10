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
        try:
            await run.io_bound(db.backup_now)  # copie de la base avant de remplacer l'application : retour possible si la nouvelle version la gâtait
        except Exception:  # noqa: BLE001 — une sauvegarde impossible ne doit pas empêcher la mise à jour
            pass
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


IDLE_SECONDS = 20 * 60  # sans toucher à Velora depuis ce temps : l'installation automatique peut avoir lieu
_pending: dict | None = None  # version trouvée, en attente d'un moment où vous ne travaillez pas
_installing = False


def _found(info: dict) -> None:
    """Une version plus récente existe : installation automatique au prochain moment calme, ou simple notification si elle est désactivée."""
    global _pending
    if updater.auto_install():
        _pending = info
    else:  # (NiceGUI ne sait pas transmettre une fonction Python dans les « actions » d'une notification : l'envoi échouait avec « Type is not JSON serializable »)
        ui.notification(f"Velora {info['version']} est disponible.", type="info", timeout=0, close_button="Voir",
                        on_dismiss=lambda _=None: offer(info))


async def auto_check() -> None:
    """Au démarrage, puis toutes les 10 minutes (la recherche elle-même n'a lieu que toutes les 6 heures) : jamais de fenêtre qui interrompt le travail."""
    global _installing, _pending
    if _installing:
        return
    if _pending is None:
        info = await run.io_bound(updater.check)
        if info:
            _found(info)
    if _pending is not None and updater.auto_install() and await _quiet():
        _installing = True
        await install(_pending)
        _pending = None  # on n'arrive ici que si l'installation a échoué (sinon l'appli s'est fermée) : prochaine tentative à la prochaine recherche, dans 6 heures
        _installing = False


async def _quiet() -> bool:
    """Personne n'a touché à Velora depuis 20 minutes, aucun message n'est en cours de rédaction, aucune fenêtre n'est ouverte."""
    try:
        return bool(await ui.run_javascript(f"(Date.now() - (window.__lastAct || Date.now())) / 1000 > {IDLE_SECONDS} && !document.querySelector('.q-editor') && !document.querySelector('.q-dialog')", timeout=5))
    except Exception:  # noqa: BLE001 — page absente ou occupée : on réessaiera
        return False


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
    ui.switch("Vérifier automatiquement (toutes les 6 heures)", value=db.kv_get("update_auto") != "0",
              on_change=lambda e: db.kv_set("update_auto", "1" if e.value else "0"))
    ui.switch("Installer automatiquement quand vous ne travaillez pas", value=db.kv_get("update_install") != "0",
              on_change=lambda e: db.kv_set("update_install", "1" if e.value else "0")).mark("update-install")
    ui.label("Au bout de 20 minutes sans utiliser Velora, sans message en cours de rédaction. Une copie de la base est faite avant, puis Velora redémarre seule.").classes(
        "text-caption text-grey")
