"""Verrouillage de l'application : écran du code, session déverrouillée, récupération de la clé de la base."""
import hmac
import os

from nicegui import app, run, ui

from mailia import config, db, vault
from mailia.brand import logo_big, logo_img

from mailia.ui_common import SERVER_MODE


def lock_applies() -> bool:
    """Le verrouillage protège la fenêtre locale ; en mode serveur (téléphone), c'est le jeton d'accès qui protège."""
    return (not SERVER_MODE) or os.environ.get("MAILIA_TEST_LOCK") == "1"


def session_unlocked() -> bool:
    """Déverrouillé pour CETTE session (ce navigateur ou cette fenêtre) et pour ce lancement de l'appli seulement :
    un autre navigateur local qui atteindrait le serveur de l'appli n'entre pas."""
    return (not vault.lock_enabled(db)) or app.storage.user.get("unlock") == vault.BOOT_ID


def session_unlock() -> None:
    app.storage.user["unlock"] = vault.BOOT_ID


def lock_screen_now() -> None:
    """Verrouille l'application : l'écran de code remplace tout."""
    app.storage.user["unlock"] = ""
    ui.navigate.reload()


def render_db_error() -> None:
    """Base chiffrée mais clé absente du trousseau : saisie de la clé de récupération."""
    with ui.card().classes("absolute-center").style("width:min(520px,94vw)"):
        with ui.row().classes("items-center no-wrap gap-2"):
            ui.html(logo_img(34), sanitize=False)
            ui.label("Velora").classes("text-h5").style("font-weight:700")
        ui.label("La base de données est chiffrée, mais sa clé est introuvable dans le trousseau du système.").classes("text-body2")
        ui.label("Saisissez la clé de récupération (64 caractères) notée lors du chiffrement. Sinon, vous pouvez repartir d'une base vide : "
                 "vos mails se resynchroniseront depuis vos serveurs.").classes("text-caption text-grey")
        k = ui.input("Clé de récupération").props("outlined dense autofocus").classes("w-full")
        msg = ui.label("").classes("text-negative text-caption")

        def restore() -> None:
            if vault.restore_key(k.value or ""):
                ui.notify("Clé rétablie. L'application va se fermer : rouvrez-la.", type="positive")
                ui.timer(1.5, lambda: app.shutdown(), once=True)
            else:
                msg.set_text("Cette clé n'ouvre pas la base.")

        async def reset() -> None:
            with ui.dialog() as dlg, ui.card():
                ui.label("Repartir d'une base vide ?").classes("text-h6")
                ui.label("L'ancienne base chiffrée est conservée (mail.db.chiffree) mais vos réglages locaux, brouillons et contacts de cette base ne seront plus accessibles.").classes("text-body2")
                with ui.row().classes("justify-end w-full"):
                    ui.button("Annuler", on_click=lambda: dlg.submit(False)).props("flat no-caps")
                    ui.button("Repartir de zéro", on_click=lambda: dlg.submit(True)).props("unelevated no-caps color=negative")
            if await dlg:
                config.DB_PATH.replace(config.DB_PATH.with_name("mail.db.chiffree"))
                ui.notify("Base mise de côté. Rouvrez l'application.", type="info")
                ui.timer(1.5, lambda: app.shutdown(), once=True)

        k.on("keydown.enter", restore)
        with ui.row().classes("w-full justify-between"):
            ui.button("Repartir d'une base vide", on_click=reset).props("flat no-caps color=negative")
            ui.button("Rétablir la clé", on_click=restore).props("unelevated no-caps color=primary")


def render_lock_screen() -> None:
    # grand V voilé en fond (comme l'écran d'accueil), puis logo et nom plus grands au-dessus du champ du code
    ui.html(logo_big(640, 0.10), sanitize=False).style(
        "position:fixed;left:50%;top:50%;transform:translate(-50%,-50%);max-width:90vmin;pointer-events:none;z-index:0")
    with ui.column().classes("absolute-center items-center gap-3").style("width:min(380px,92vw);z-index:1"):
        with ui.row().classes("items-center no-wrap gap-4"):
            ui.html(logo_img(84), sanitize=False)
            ui.label("Velora").classes("text-h3").style("font-weight:700")
        ui.label("Application verrouillée").classes("text-body2 text-grey")
        pin = ui.input(placeholder="Code", password=True, password_toggle_button=True).props("outlined dense autofocus input-class=text-center").classes("w-full").style(
            "width:240px;max-width:100%;background:var(--surface);border-radius:8px")
        msg = ui.label("").classes("text-negative text-caption")

        async def submit() -> None:
            ok, wait = vault.check_code(db, pin.value or "")
            if ok:
                session_unlock()  # sans cela, la page se rechargeait et redemandait le code
                ui.navigate.reload()
            else:
                pin.set_value("")
                msg.set_text(f"Trop d'essais : patientez {wait} s." if wait else "Code incorrect.")

        async def touch_id() -> None:
            if await run.io_bound(vault.biometric_prompt):
                session_unlock()
                ui.navigate.reload()

        async def forgot() -> None:
            accts = {k: f"{c.label} ({c.email})" for k, c in config.load_accounts(enabled_only=False).items() if c.password}
            if not accts:
                return ui.notify("Aucune boîte avec mot de passe : impossible de réinitialiser.", type="negative")
            with ui.dialog() as dlg, ui.card().style("width:460px;max-width:94vw"):
                ui.label("Réinitialiser le code").classes("text-h6")
                ui.label("Pour prouver que l'ordinateur est bien à vous, saisissez le mot de passe d'une de vos boîtes mail. "
                         "Le verrouillage est alors désactivé (vous pourrez en choisir un nouveau dans les paramètres).").classes("text-caption text-grey")
                sel = ui.select(accts, value=next(iter(accts)), label="Boîte").props("outlined dense").classes("w-full")
                pw = ui.input("Mot de passe de cette boîte", password=True).props("outlined dense").classes("w-full")
                with ui.row().classes("justify-end w-full"):
                    ui.button("Annuler", on_click=lambda: dlg.submit(None)).props("flat no-caps")
                    ui.button("Réinitialiser", on_click=lambda: dlg.submit((sel.value, pw.value))).props("unelevated no-caps color=primary")
            r = await dlg
            if not r:
                return
            real = (config.load_accounts(enabled_only=False).get(r[0]).password or "")
            if real and hmac.compare_digest(real.encode(), (r[1] or "").encode()):
                vault.clear_code(db)
                session_unlock()
                ui.navigate.reload()
            else:
                ui.notify("Mot de passe incorrect.", type="negative")

        pin.on("keydown.enter", submit)
        ui.button("Déverrouiller", on_click=submit).props("unelevated no-caps color=primary").style("min-width:240px")
        if db.kv_get("lock_bio") == "1" and vault.biometric_available():
            ui.button("Touch ID", icon="fingerprint", on_click=touch_id).props("outline no-caps").style("min-width:240px")
            ui.timer(0.5, touch_id, once=True)
        ui.button("Code oublié ?", on_click=forgot).props("flat no-caps size=sm")
