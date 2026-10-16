"""Ajout, modification et retrait d'une boîte mail (boîte de dialogue détaillée ; l'assistant d'ajout est dans wizard.py)."""
from nicegui import run, ui

from mailia import autoconfig, compose, config, db, htmlmail, sync, tlsutil

from mailia.ui_common import make_editor


PRESETS = {  # une seule table de fournisseurs : celle de la détection automatique (mailia/autoconfig.py)
    **{v["name"] + (" (mot de passe d'application requis)" if "application" in v["help"] else ""):
       {"host": v["host"], "port": 993, "tls": "ssl", "smtp_host": v["smtp_host"], "smtp_port": 587} for v in autoconfig.PROVIDERS.values() if v["host"]},
    "Autre fournisseur": {},
}


def friendly_error(e: Exception) -> str:
    """Message compréhensible pour une erreur de connexion."""
    name, text = type(e).__name__, str(e)
    low = text.lower()
    if name in ("UnauthorizedError",) or "authentication" in low or "login failed" in low or "invalid credentials" in low or "authenticationfailed" in low:
        return "Identifiant ou mot de passe refusé. Vérifiez-les (certains fournisseurs exigent un mot de passe d'application)."
    if name in ("gaierror", "TransportError") or "name or service not known" in low or "nodename nor servname" in low or "getaddrinfo" in low:
        return "Serveur introuvable. Vérifiez l'adresse du serveur et votre connexion internet."
    if "timed out" in low or "timeout" in low:
        return "Le serveur ne répond pas (délai dépassé). Vérifiez l'adresse et le port."
    if tlsutil.is_cert_error(e):
        return ("Le certificat de sécurité du serveur n'est pas reconnu : le nom ne correspond pas, il est expiré ou auto-signé. "
                "Vérifiez l'adresse du serveur. Si vous connaissez ce serveur et lui faites confiance, cochez « Accepter un certificat non valide » "
                "dans les réglages avancés ; sinon, n'insistez pas (quelqu'un pourrait se faire passer pour lui).")
    if "ssl" in low or "certificate" in low:
        return "Connexion sécurisée impossible. Essayez l'autre mode de sécurité (SSL/TLS ou STARTTLS) ou vérifiez le port."
    return f"{name} : {text[:240]}"


def open_account(host, key: str | None = None) -> None:
    """Ajoute ou modifie une boîte mail (Exchange ou IMAP/SMTP). Le mot de passe reste dans le .env de l'utilisateur."""
    cur = next((x for x in config.read_accounts() if x["key"] == key), None) if key else None
    has_pw = bool(config.get_password(key)) if key else False
    a = {"label": (cur or {}).get("label", ""), "kind": (cur or {}).get("kind", "imap"), "email": (cur or {}).get("email", ""),
         "login": (cur or {}).get("login", ""), "password": "", "host": (cur or {}).get("host", ""),
         "port": (cur or {}).get("port", 993), "tls": (cur or {}).get("tls", "ssl"),
         "smtp_host": (cur or {}).get("smtp_host", ""), "smtp_port": (cur or {}).get("smtp_port", 587),
         "tls_insecure": bool((cur or {}).get("tls_insecure", False)),
         "ai": bool((cur or {}).get("ai", True)), "enabled": bool((cur or {}).get("enabled", True)), "preset": "Autre fournisseur"}
    els: dict = {}
    state = {"warned": False}

    def to_cfg() -> config.AccountCfg:
        return config.AccountCfg(
            key=key or "test", label=a["label"].strip() or "Boîte", kind=a["kind"], email=a["email"].strip(),
            login=(a["login"] or a["email"]).strip(), password=a["password"] or (config.get_password(key) if key else ""),
            ai_enabled=a["ai"], host=(a["host"] or "").strip(), port=int(a["port"] or 993),
            smtp_host=(a["smtp_host"] or "").strip(), smtp_port=int(a["smtp_port"] or 587), tls=a["tls"],
            tls_insecure=bool(a["tls_insecure"]))

    def missing() -> str:
        if not a["label"].strip():
            return "Donnez un nom à cette boîte."
        if "@" not in a["email"]:
            return "Indiquez l'adresse e-mail."
        if not (a["password"] or has_pw):
            return "Indiquez le mot de passe."
        if a["kind"] == "imap" and not (a["host"] or "").strip():
            return "Indiquez le serveur IMAP."
        return ""

    def apply_preset(e) -> None:
        for k, v in PRESETS.get(e.value, {}).items():
            els[k].set_value(v)

    with host, ui.dialog() as dlg, ui.card().style("width:780px;max-width:96vw"):
        ui.label("Modifier la boîte mail" if cur else "Ajouter une boîte mail").classes("text-h6")
        ui.input("Nom affiché", placeholder="ex : Travail, Perso").bind_value(a, "label").props("outlined dense stack-label").classes("w-full")
        ui.select({"ews": "Exchange (EWS)", "imap": "IMAP / SMTP"}, label="Type de compte").bind_value(a, "kind").props(
            "outlined dense stack-label" + (" disable" if cur else "")).classes("w-full")
        ui.input("Adresse e-mail").bind_value(a, "email").props("outlined dense stack-label").classes("w-full")
        ui.input("Identifiant", placeholder="vide = l'adresse e-mail").bind_value(a, "login").props("outlined dense stack-label").classes("w-full")
        ui.input("Mot de passe", password=True, password_toggle_button=True,
                 placeholder="défini ✓ (laisser vide pour conserver)" if has_pw else "").bind_value(a, "password").props("outlined dense stack-label").classes("w-full")

        with ui.column().classes("w-full gap-2").bind_visibility_from(a, "kind", backward=lambda k: k == "ews"):
            ui.input("Serveur Exchange (EWS)", placeholder="ex : mail.exemple.fr — vide = détection automatique").bind_value(a, "host").props(
                "outlined dense stack-label").classes("w-full")
            ui.label("Authentification par identifiant et mot de passe. Microsoft 365 / Exchange Online peut l'interdire.").classes("text-caption text-grey")

        with ui.column().classes("w-full gap-2").bind_visibility_from(a, "kind", backward=lambda k: k == "imap"):
            ui.select(list(PRESETS), label="Fournisseur (remplit les serveurs)", on_change=apply_preset).bind_value(a, "preset").props(
                "outlined dense stack-label").classes("w-full")
            with ui.row().classes("w-full no-wrap gap-2"):
                els["host"] = ui.input("Serveur IMAP (réception)").bind_value(a, "host").props("outlined dense stack-label").classes("col")
                els["port"] = ui.number("Port", min=1, max=65535, precision=0).bind_value(a, "port").props("outlined dense stack-label").style("width:90px")
            els["tls"] = ui.select({"ssl": "SSL/TLS (port 993)", "starttls": "STARTTLS (port 143)"}, label="Sécurité IMAP").bind_value(a, "tls").props(
                "outlined dense stack-label").classes("w-full")
            with ui.row().classes("w-full no-wrap gap-2"):
                els["smtp_host"] = ui.input("Serveur SMTP (envoi)").bind_value(a, "smtp_host").props("outlined dense stack-label").classes("col")
                els["smtp_port"] = ui.number("Port", min=1, max=65535, precision=0).bind_value(a, "smtp_port").props("outlined dense stack-label").style("width:90px")
            ui.label("SMTP : port 587 (STARTTLS) ou 465 (SSL).").classes("text-caption text-grey")
            ui.switch("Accepter un certificat non valide (serveur à certificat auto-signé)").bind_value(a, "tls_insecure")
            ui.label("À éviter : sans vérification du certificat, une personne sur le même réseau pourrait se faire passer pour votre serveur et lire "
                     "votre mot de passe. À réserver à un serveur que vous administrez.").classes("text-caption text-grey")

        ui.switch("Boîte activée (synchronisée et affichée)").bind_value(a, "enabled")
        if config.ai_enabled():  # sans clé d'IA, ce réglage n'aurait aucun effet : on ne le montre pas
            ui.switch("Analyse IA sur cette boîte (boîte de réception)").bind_value(a, "ai")
        sig = {"custom": bool(key and (db.kv_get(f"signature_html:{key}") or "").strip()),
               "html": (db.kv_get(f"signature_html:{key}") if key else "") or compose.signature_html()}
        if key:
            ui.switch("Signature différente pour cette boîte").bind_value(sig, "custom")
            sig_ed = make_editor(sig["html"], lambda e: sig.__setitem__("html", e.value or ""), "110px")
            sig_ed.bind_visibility_from(sig, "custom")
            ui.label("Sinon, la signature générale (Paramètres › Affichage et signature) est utilisée.").classes("text-caption text-grey")
        result = ui.label().classes("text-body2").style("white-space:pre-wrap")

        async def run_test() -> bool:
            err = missing()
            if err:
                result.set_text(err)
                result.style("color:var(--hi)")
                return False
            result.set_text("Test de connexion en cours…")
            result.style("color:var(--muted)")
            try:
                msg = await run.io_bound(sync.test_account, to_cfg())
            except Exception as e:
                result.set_text("✗ " + friendly_error(e))
                result.style("color:var(--hi)")
                return False
            result.set_text("✓ " + msg)
            result.style("color:#16a34a")
            return True

        async def save() -> None:
            err = missing()
            if err:
                result.set_text(err)
                result.style("color:var(--hi)")
                return
            if not state["warned"] and not await run_test():
                state["warned"] = True
                save_btn.set_text("Enregistrer quand même")
                return
            data = {"key": key, "label": a["label"].strip(), "kind": a["kind"], "email": a["email"].strip(),
                    "login": (a["login"] or "").strip(), "host": (a["host"] or "").strip(), "ai": bool(a["ai"]),
                    "enabled": bool(a["enabled"])}
            if a["kind"] == "imap":
                data.update(port=int(a["port"] or 993), tls=a["tls"], smtp_host=(a["smtp_host"] or "").strip(),
                            smtp_port=int(a["smtp_port"] or 587), tls_insecure=bool(a["tls_insecure"]))
            if not key:
                data.pop("key")
            config.save_account(data, a["password"])
            if key:
                custom = sig["custom"] and (sig["html"] or "").strip() and "<" in sig["html"]
                db.kv_set(f"signature_html:{key}", htmlmail.sanitize_outgoing(sig["html"]) if custom else "")
            sync.reload()
            dlg.close()
            ui.notify("Boîte enregistrée. Synchronisation en cours…", type="positive")
            ui.navigate.reload()

        with ui.row().classes("justify-end w-full q-mt-sm"):
            ui.button("Annuler", on_click=dlg.close).props("flat no-caps")
            ui.button("Tester la connexion", icon="wifi_tethering", on_click=run_test).props("outline no-caps")
            save_btn = ui.button("Enregistrer", on_click=save).props("unelevated no-caps color=primary")
    dlg.open()


async def delete_account_dialog(host, key: str) -> None:
    cfg = sync.ACCOUNTS.get(key)
    if not cfg:
        return
    with db.conn() as c:
        n = c.execute("SELECT COUNT(*) FROM messages WHERE account=?", (key,)).fetchone()[0]
    with host, ui.dialog() as dlg, ui.card().classes("w-[480px] max-w-full"):
        ui.label(f"Retirer la boîte « {cfg.label} » ?").classes("text-h6")
        ui.label(f"{cfg.email}").classes("text-body2")
        ui.label(f"Ses {n} mails copiés dans l'application, ses dossiers et ses réglages seront effacés d'ici. "
                 "Rien n'est supprimé sur le serveur de messagerie : vos mails restent intacts, et vous pourrez "
                 "rajouter la boîte plus tard.").classes("text-body2")
        with ui.row().classes("justify-end w-full"):
            ui.button("Annuler", on_click=lambda: dlg.submit(False)).props("flat no-caps")
            ui.button("Retirer cette boîte", on_click=lambda: dlg.submit(True)).props("unelevated no-caps color=negative")
    if not await dlg:
        return
    count = await run.io_bound(sync.forget_account, key)
    ui.notify(f"Boîte « {cfg.label} » retirée ({count} mails locaux effacés).", type="positive")
    ui.navigate.reload()
