"""Assistant d'ajout d'une boîte mail : adresse -> réglages trouvés automatiquement -> connexion (OAuth ou mot de passe) et test ->
intelligence artificielle (facultative) -> terminé. Sert au premier lancement et à « Ajouter une boîte mail »."""
import threading
import webbrowser

from nicegui import run, ui

from . import ai, autoconfig, config, db, i18n, oauth, sync

PROVIDER_LABEL = {"microsoft": "Microsoft", "google": "Google"}


def account_cfg(st: dict) -> config.AccountCfg:
    """Boîte à essayer (pas encore enregistrée) d'après ce que l'utilisateur a saisi."""
    return config.AccountCfg(
        key="test", label=st["label"] or "Boîte", kind=st["kind"], email=st["email"].strip(), login=(st["login"] or st["email"]).strip(),
        password=st["password"], ai_enabled=True, host=(st["host"] or "").strip(), port=int(st["port"] or 993),
        smtp_host=(st["smtp_host"] or "").strip(), smtp_port=int(st["smtp_port"] or 587), tls=st["tls"],
        tls_insecure=bool(st["tls_insecure"]))


def account_data(st: dict) -> dict:
    """Ce qui est enregistré pour la nouvelle boîte (sans le mot de passe, confié au trousseau ou au fichier de configuration)."""
    data = {"label": (st["label"] or "").strip() or st["email"].strip(), "kind": st["kind"], "email": st["email"].strip(),
            "login": (st["login"] or "").strip() if (st["login"] or "").strip() != st["email"].strip() else "",
            "host": (st["host"] or "").strip(), "ai": True, "enabled": True}
    if st["kind"] == "imap":
        data.update(port=int(st["port"] or 993), tls=st["tls"], smtp_host=(st["smtp_host"] or "").strip(), smtp_port=int(st["smtp_port"] or 587),
                    tls_insecure=bool(st["tls_insecure"]))
    return data


def ai_env(st: dict) -> dict:
    """Réglages d'IA choisis à l'étape « Intelligence artificielle » (une clé vide n'écrase rien)."""
    env = {"AI_ENABLED": "1" if st["ai"] else "0"}
    if st["ai"] and (st["api_key"] or "").strip():
        env["ANTHROPIC_API_KEY"] = st["api_key"].strip()
    if st["ai"] and (st.get("onemin_key") or "").strip():
        env["ONEMIN_API_KEY"] = st["onemin_key"].strip()
    if st["ai"] and st.get("provider") in config.PROVIDERS:
        env["AI_PROVIDER"] = st["provider"]
    return env


def recap_text(st: dict, need_ai_step: bool) -> str:
    ai_text = ("L'IA est activée." if (st["ai"] and st["api_key"]) else
               "L'IA est activée : ajoutez votre clé dans Paramètres › Intelligence artificielle." if st["ai"] else
               "L'IA est désactivée : l'appli fonctionne comme un client mail ordinaire.") if need_ai_step else "Vous pourrez régler l'IA dans les paramètres."
    return f"La boîte « {st['label']} » ({st['email']}) sera ajoutée et synchronisée. {ai_text}"


async def run_wizard(host, first: bool, friendly_error, open_link=None, on_done=None) -> None:
    open_link = open_link or (lambda url: webbrowser.open(url))
    need_ai_step = first or not (config.env("ANTHROPIC_API_KEY") or config.env("ONEMIN_API_KEY"))
    st = {"email": "", "label": "", "info": None, "kind": "imap", "login": "", "password": "", "host": "", "port": 993, "tls": "ssl",
          "user_name": config.user_name(), "smtp_host": "", "smtp_port": 587, "tls_insecure": False, "tested": False, "ai": True, "api_key": "", "onemin_key": "", "provider": "anthropic", "budget": 5.0, "hard": False, "use_password": False,
          "cancel": None}

    with host, ui.dialog().props("persistent" if first else "") as dlg, ui.card().style("width:780px;max-width:96vw;max-height:94vh;overflow:auto"):
        stepper = ui.stepper().props("flat animated").classes("w-full")
        with stepper:
            # ---------- 1. adresse
            with ui.step("Adresse", icon="alternate_email"):
                if first:
                    with ui.row().classes("w-full justify-end gap-1"):  # langue : un clic recharge l'écran dans la langue choisie
                        for code, name in i18n.AVAILABLE.items():
                            ui.button(name, on_click=lambda _, c=code: (i18n.set_language(c), ui.navigate.reload())).props(
                                f"{'unelevated color=primary' if i18n.language() == code else 'flat'} dense no-caps size=sm")
                ui.label("Bienvenue dans Velora" if first else "Ajouter une boîte mail").classes("text-h5").style("font-weight:700")
                ui.label("Saisissez l'adresse e-mail à ajouter. Les réglages du serveur sont trouvés automatiquement : seul le nom de domaine "
                         "(après le @) est interrogé.").classes("text-body2 text-grey")
                if first or not config.user_name():
                    ui.input("Votre nom", placeholder="ex : Marie Dupont").bind_value(st, "user_name").props("outlined dense stack-label").classes("w-full q-mt-md")
                    ui.label("Il sert à l'IA (« rédige pour Marie ») et à la signature par défaut ; modifiable ensuite dans Paramètres.").classes("text-caption text-grey")
                email = ui.input("Adresse e-mail", placeholder="prenom@exemple.fr").bind_value(st, "email").props("outlined autofocus stack-label").classes("w-full q-mt-md")
                ui.input("Nom affiché (facultatif)", placeholder="ex : Travail, Perso").bind_value(st, "label").props("outlined dense stack-label").classes("w-full")
                msg1 = ui.label("").classes("text-negative text-body2")
                with ui.stepper_navigation():
                    detect_btn = ui.button("Continuer", icon="arrow_forward").props("unelevated no-caps color=primary").mark("wiz-detect")
                    if first:
                        ui.button("Plus tard", on_click=dlg.close).props("flat no-caps")
                    else:
                        ui.button("Annuler", on_click=dlg.close).props("flat no-caps")

            # ---------- 2. connexion
            with ui.step("Connexion", icon="lock_open"):
                conn = ui.column().classes("w-full gap-2")

            # ---------- 3. IA
            if need_ai_step:
                with ui.step("Intelligence artificielle", icon="auto_awesome"):
                    ui.label("Intelligence artificielle (facultative)").classes("text-h5").style("font-weight:700")
                    ui.label("L'IA trie vos mails, les résume, rédige des brouillons de réponse et prépare un brief du jour. Elle utilise Claude (Anthropic) "
                             "ou 1min.AI avec votre propre clé : vous payez à l'usage, en pratique quelques euros par mois avec le modèle économique par défaut.").classes("text-body2 text-grey")
                    ui.switch("Activer les fonctions IA").bind_value(st, "ai")
                    with ui.column().classes("w-full gap-2").bind_visibility_from(st, "ai"):
                        ui.input("Clé API Claude", password=True, password_toggle_button=True, placeholder="sk-ant-…").bind_value(st, "api_key").props(
                            "outlined dense stack-label").classes("w-full")
                        ui.button("Obtenir une clé (console.anthropic.com)", icon="open_in_new",
                                  on_click=lambda: open_link("https://console.anthropic.com/settings/keys")).props("flat no-caps dense size=sm")
                        ui.input("Clé API 1min.AI (facultative)", password=True, password_toggle_button=True).bind_value(st, "onemin_key").props(
                            "outlined dense stack-label").classes("w-full")
                        ui.toggle(config.PROVIDERS, value="anthropic").bind_value(st, "provider").props("no-caps unelevated")
                        with ui.row().classes("items-center gap-3"):
                            ui.number("Budget mensuel (€)", min=0, step=1, precision=2).bind_value(st, "budget").props("outlined dense").style("width:200px")
                            ui.switch("Bloquer l'IA au plafond").bind_value(st, "hard")
                        ui.label("Vous pouvez désactiver l'IA, ou l'exclure pour certains expéditeurs, à tout moment (Paramètres › Confidentialité). "
                                 "Sans clé, l'appli fonctionne comme un client mail ordinaire.").classes("text-caption text-grey")
                    with ui.stepper_navigation():
                        ui.button("Continuer", icon="arrow_forward", on_click=lambda: stepper.next()).props("unelevated no-caps color=primary").mark("wiz-ai-next")
                        ui.button("Retour", on_click=lambda: stepper.previous()).props("flat no-caps")

            # ---------- 4. fin
            with ui.step("Terminé", icon="check_circle"):
                ui.label("Tout est prêt").classes("text-h5").style("font-weight:700")
                recap = ui.label("").classes("text-body2")
                with ui.stepper_navigation():
                    done_btn = ui.button("Commencer", icon="check", on_click=lambda: finish()).props("unelevated no-caps color=primary").mark("wiz-done")
                    ui.button("Retour", on_click=lambda: stepper.previous()).props("flat no-caps")

    # ===================== logique =====================
    async def detect() -> None:
        addr = (st["email"] or "").strip()
        if "@" not in addr:
            msg1.set_text("Saisissez une adresse e-mail complète.")
            return
        taken = {a.get("email", "").lower() for a in config.read_accounts()}
        if addr.lower() in taken:
            msg1.set_text("Cette adresse est déjà configurée.")
            return
        msg1.set_text("")
        if (st["user_name"] or "").strip():
            db.kv_set("user_name", st["user_name"].strip())
        detect_btn.props(add="loading disable")
        try:
            info = await run.io_bound(autoconfig.detect, addr)
        finally:
            detect_btn.props(remove="loading disable")
        if info.get("error"):
            msg1.set_text(info["error"])
            return
        st["info"] = info
        st.update(kind=info["kind"], host=info["host"], port=info["port"], tls=info["tls"], smtp_host=info["smtp_host"], smtp_port=info["smtp_port"],
                  login=addr, password="", tested=False, use_password=False)
        if not st["label"]:
            st["label"] = info["name"] or addr.split("@")[1].split(".")[0].capitalize()
        conn_view.refresh()
        stepper.next()

    detect_btn.on_click(detect)
    email.on("keydown.enter", detect)

    @ui.refreshable
    def conn_view() -> None:
        info = st["info"]
        if not info:
            return
        prov = info.get("oauth")
        oauth_ok = oauth.configured(prov)
        ui.label("Connexion à " + (info["name"] or st["email"])).classes("text-h5").style("font-weight:700")
        if info["found"]:
            ui.label(f"Réglages trouvés ({info['source']}).").classes("text-body2 text-grey")
        else:
            ui.label("Réglages non trouvés automatiquement : vérifiez-les ci-dessous.").classes("text-body2").style("color:#c77800")
        if info.get("help"):
            with ui.row().classes("items-center no-wrap gap-2 q-pa-sm w-full").style("background:var(--accent-soft);border-radius:10px"):
                ui.icon("info", size="20px").style("color:var(--accent)")
                ui.label(info["help"]).classes("text-body2 col")
                if info.get("help_url"):
                    ui.button("Ouvrir", icon="open_in_new", on_click=lambda: open_link(info["help_url"])).props("flat no-caps dense size=sm")
        result = ui.label("").classes("text-body2").style("white-space:pre-wrap")

        async def test() -> bool:
            if st["kind"] == "imap" and not (st["host"] or "").strip():
                result.set_text("Indiquez le serveur IMAP (réglages avancés).")
                result.style("color:var(--hi)")
                return False
            if not st["password"]:
                result.set_text("Indiquez le mot de passe." if not (prov and oauth_ok and not st["use_password"]) else "Connectez-vous d'abord avec " + PROVIDER_LABEL.get(prov, "le fournisseur") + ".")
                result.style("color:var(--hi)")
                return False
            result.set_text("Test de connexion en cours…")
            result.style("color:var(--muted)")
            try:
                text = await run.io_bound(sync.test_account, account_cfg(st))
            except Exception as e:
                result.set_text("✗ " + friendly_error(e))
                result.style("color:var(--hi)")
                st["tested"] = False
                return False
            result.set_text("✓ " + text)
            result.style("color:#16a34a")
            st["tested"] = True
            next_btn.props(remove="disable")
            return True

        # ---- connexion par le navigateur (Microsoft, Google)
        if prov and oauth_ok and not st["use_password"]:
            wait = ui.label("").classes("text-body2")

            async def do_oauth() -> None:
                st["cancel"] = threading.Event()
                oauth_btn.props(add="loading disable")
                wait.set_text("Une page vient de s'ouvrir dans votre navigateur : connectez-vous, puis acceptez. Cette fenêtre continue toute seule.")
                try:
                    st["password"] = await run.io_bound(oauth.authorize, prov, st["email"].strip(), webbrowser.open, 300, st["cancel"])
                except oauth.OAuthError as e:
                    wait.set_text("")
                    oauth_btn.props(remove="loading disable")
                    return ui.notify(str(e), type="negative", multi_line=True)
                oauth_btn.props(remove="loading disable")
                wait.set_text("Connexion acceptée.")
                await test()

            oauth_btn = ui.button(f"Se connecter avec {PROVIDER_LABEL[prov]}", icon="login", on_click=do_oauth).props("unelevated no-caps color=primary size=lg")
            if prov == "google":
                ui.button("Utiliser plutôt un mot de passe d'application", on_click=lambda: (st.update(use_password=True), conn_view.refresh())).props("flat no-caps dense size=sm")
        else:
            if prov and not oauth_ok:
                if prov == "microsoft":
                    ui.label("La connexion Microsoft sécurisée n'est pas encore activée dans cette installation. Vous pouvez essayer avec un mot de passe, "
                             "mais Microsoft le refuse pour la plupart des comptes.").classes("text-caption").style("color:#c77800")
            ui.input("Mot de passe" + (" d'application" if info.get("provider") in ("gmail", "icloud", "yahoo") else ""), password=True,
                     password_toggle_button=True).bind_value(st, "password").props("outlined dense").classes("w-full")

        # ---- réglages avancés
        with ui.expansion("Réglages avancés", icon="tune").classes("w-full").props("dense"):
            with ui.column().classes("w-full gap-2 q-pa-sm"):
                ui.select({"imap": "IMAP / SMTP", "ews": "Exchange (EWS)"}, label="Type de compte").bind_value(st, "kind").props("outlined dense").classes("w-full")
                ui.input("Identifiant", placeholder="vide = l'adresse e-mail").bind_value(st, "login").props("outlined dense stack-label").classes("w-full")
                with ui.column().classes("w-full gap-2").bind_visibility_from(st, "kind", backward=lambda k: k == "ews"):
                    ui.input("Serveur Exchange", placeholder="vide = détection automatique").bind_value(st, "host").props("outlined dense stack-label").classes("w-full")
                with ui.column().classes("w-full gap-2").bind_visibility_from(st, "kind", backward=lambda k: k == "imap"):
                    with ui.row().classes("w-full no-wrap gap-2"):
                        ui.input("Serveur IMAP").bind_value(st, "host").props("outlined dense").classes("col")
                        ui.number("Port", min=1, max=65535, precision=0).bind_value(st, "port").props("outlined dense").style("width:90px")
                    ui.select({"ssl": "SSL/TLS (993)", "starttls": "STARTTLS (143)"}, label="Sécurité IMAP").bind_value(st, "tls").props("outlined dense").classes("w-full")
                    with ui.row().classes("w-full no-wrap gap-2"):
                        ui.input("Serveur SMTP").bind_value(st, "smtp_host").props("outlined dense").classes("col")
                        ui.number("Port", min=1, max=65535, precision=0).bind_value(st, "smtp_port").props("outlined dense").style("width:90px")
                    ui.switch("Accepter un certificat non valide (serveur auto-signé)").bind_value(st, "tls_insecure")
                    ui.label("À éviter : sans vérification, quelqu'un sur le même réseau pourrait se faire passer pour votre serveur.").classes("text-caption text-grey")
        with ui.stepper_navigation():
            ui.button("Tester la connexion", icon="wifi_tethering", on_click=test).props("outline no-caps").mark("wiz-test")
            next_btn = ui.button("Continuer", icon="arrow_forward", on_click=lambda: stepper.next()).props("unelevated no-caps color=primary").mark("wiz-next")
            if not st["tested"]:
                next_btn.props(add="disable")
            ui.button("Retour", on_click=lambda: stepper.previous()).props("flat no-caps")

    with conn:
        conn_view()

    def update_recap() -> None:
        recap.set_text(recap_text(st, need_ai_step))

    stepper.on_value_change(lambda e: update_recap() if e.value == "Terminé" else None)

    async def finish() -> None:
        done_btn.props(add="loading disable")
        data = account_data(st)
        if first and not config.env("USE_KEYCHAIN") and await run.io_bound(config.keychain_available):
            config.write_env({"USE_KEYCHAIN": "1"})  # première installation : mots de passe et clé API au trousseau du système, pas dans un fichier
        config.save_account(data, st["password"])
        if need_ai_step:
            env = ai_env(st)
            config.write_env(env)
            ai.reset_client()
            db.kv_set("ai_budget_eur", str(st["budget"] or 0))
            db.kv_set("ai_budget_hard", "1" if st["hard"] else "0")
        sync.reload()
        dlg.close()
        ui.notify("Boîte ajoutée. Synchronisation en cours…", type="positive")
        if on_done:
            on_done()
        else:
            ui.navigate.reload()

    dlg.open()
