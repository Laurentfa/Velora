"""Panneaux des paramètres : automatismes (alertes, prioritaires, apprentissage, résumé quotidien), confidentialité, suivi des coûts IA."""
from nicegui import ui

from . import aipolicy, config, db, notify, security

CAT_FR = {"action": "À traiter", "info": "Info", "newsletter": "Newsletter", "notification": "Notification", "personnel": "Perso"}


def _lines(text: str) -> list[str]:
    return [x.strip() for x in (text or "").replace(",", "\n").splitlines() if x.strip()]


def automations_panel() -> None:
    ui.label("Automatismes").classes("text-h6").style("font-weight:700")

    # --- alertes avant l'envoi
    ui.label("Avant l'envoi").classes("text-subtitle2 q-mt-md")
    ui.switch("Me prévenir : pièce jointe oubliée, objet vide, destinataire jamais contacté, beaucoup de destinataires",
              value=db.kv_get("send_checks") != "0", on_change=lambda e: db.kv_set("send_checks", "1" if e.value else "0"))
    ui.label("Contrôles locaux et gratuits (sans IA). L'envoi reste toujours possible.").classes("text-caption text-grey")

    # --- expéditeurs prioritaires
    ui.label("Expéditeurs prioritaires").classes("text-subtitle2 q-mt-lg")
    ui.label("Un mail d'une de ces adresses ou de ces domaines passe toujours en urgent, déclenche une notification et porte une étoile ★. "
             "Une adresse par ligne (jean@exemple.fr) ou un domaine entier (exemple.fr).").classes("text-caption text-grey")
    vip = ui.textarea(value="\n".join(aipolicy.vip_list())).props("outlined autogrow dense").classes("w-full")

    def save_vip() -> None:
        aipolicy.set_vip_list(_lines(vip.value))
        ui.notify("Expéditeurs prioritaires enregistrés", type="positive")

    ui.button("Enregistrer", icon="save", on_click=save_vip).props("outline no-caps dense")

    # --- apprentissage
    ui.label("Apprentissage de vos corrections").classes("text-subtitle2 q-mt-lg")
    ui.switch("Apprendre de mes corrections de classement (« Classé : … ✎ »)", value=aipolicy.learning_enabled(),
              on_change=lambda e: db.kv_set("learn_corrections", "1" if e.value else "0"))
    ui.label("Vos dernières corrections sont montrées à l'IA comme exemples (quelques centimes par mois), et deux corrections identiques "
             "sur un même domaine suffisent à classer tout le domaine.").classes("text-caption text-grey")

    @ui.refreshable
    def learned_list() -> None:
        items = aipolicy.learned()
        if not items:
            ui.label("Rien retenu pour l'instant.").classes("text-caption text-grey")
            return
        for kind, target, cat in items:
            with ui.row().classes("w-full items-center no-wrap gap-2"):
                ui.badge(kind, color="grey-3", text_color="grey-9")
                ui.label(target).classes("col ellipsis")
                ui.label(f"→ {CAT_FR.get(cat, cat)}").classes("text-caption")
                ui.button(icon="close", on_click=lambda _, k=kind, t=target: (aipolicy.forget(k, t), learned_list.refresh())).props(
                    "flat dense round size=xs").tooltip("Oublier")
    learned_list()

    # --- résumé quotidien
    ui.label("Résumé quotidien").classes("text-subtitle2 q-mt-lg")
    ui.switch("Chaque jour, une notification récapitule ce qui m'attend", value=db.kv_get("digest_on") == "1",
              on_change=lambda e: db.kv_set("digest_on", "1" if e.value else "0"))
    with ui.row().classes("items-center gap-3"):
        ui.select({h: f"{h} h" for h in range(5, 13)}, value=int(db.kv_get("digest_hour") or 8), label="Heure",
                  on_change=lambda e: db.kv_set("digest_hour", str(e.value))).props("outlined dense").style("min-width:110px")
        ui.switch("Jours ouvrés seulement", value=db.kv_get("digest_weekdays") == "1",
                  on_change=lambda e: db.kv_set("digest_weekdays", "1" if e.value else "0"))
    ui.switch("Ajouter une phrase de synthèse rédigée par l'IA (un appel par jour)", value=db.kv_get("digest_ai") == "1",
              on_change=lambda e: db.kv_set("digest_ai", "1" if e.value else "0"))

    def test_digest() -> None:
        from . import queries
        title, text = queries.digest_text(db.kv_get("digest_ai") == "1")
        ok = notify.send(title, text)
        ui.notify(("Notification envoyée : " if ok else "Notifications non prises en charge ici. Aperçu : ") + text, type="positive" if ok else "info", multi_line=True)

    ui.button("Tester maintenant", icon="notifications", on_click=test_digest).props("outline no-caps dense")


def privacy_panel(extra=None) -> None:
    """Confidentialité : ce que l'IA voit, qui en est exclu. `extra` : fonction qui ajoute d'autres sections (chiffrement, verrouillage)."""
    ui.label("Confidentialité et sécurité").classes("text-h6").style("font-weight:700")
    ai_on = config.ai_enabled()
    no_key = config.ai_switch() and not config.ai_has_key()
    with ui.element("div").classes("q-pa-md").style("background:var(--ai-soft);border-radius:12px;border:1px solid var(--line)"):
        ui.label("IA : " + ("activée" if ai_on else "inactive — aucune clé d'API enregistrée, rien n'est envoyé" if no_key
                            else "désactivée — aucun contenu n'est envoyé à un fournisseur d'IA")).style("font-weight:700")
        if ai_on:
            ui.label("Ce qui part chez votre fournisseur d'IA (" + " / ".join(v for k, v in config.PROVIDERS.items() if config.provider_ready(k)) + "), uniquement si la fonction est active : le texte des mails à analyser (tri et résumé), "
                     "ce que vous demandez à l'IA de rédiger ou de traduire, et — seulement quand vous cliquez sur « Résumer » — le contenu d'une pièce jointe. "
                     "Jamais vos mots de passe, ni les mails des boîtes ou expéditeurs exclus ci-dessous.").classes("text-caption")
        else:
            ui.label("L'interrupteur général et les clés se trouvent dans l'onglet « Intelligence artificielle ». Sans clé, ou désactivé, plus rien "
                     "n'est envoyé et les éléments IA disparaissent de l'écran.").classes("text-caption")

    ui.label("Boîtes concernées").classes("text-subtitle2 q-mt-md")
    accounts = config.load_accounts(enabled_only=False)
    for k, c in accounts.items():
        with ui.row().classes("w-full items-center no-wrap gap-2"):
            ui.icon("check_circle" if c.ai_enabled else "block", size="18px").style("color:" + ("var(--accent)" if c.ai_enabled else "var(--muted)"))
            ui.label(f"{c.label} ({c.email})").classes("col ellipsis")
            ui.label("IA autorisée" if c.ai_enabled else "jamais d'IA").classes("text-caption text-grey")
    ui.label("Se règle boîte par boîte : onglet « Boîtes mail », bouton « Modifier ».").classes("text-caption text-grey")

    ui.label("Expéditeurs jugés sûrs").classes("text-subtitle2 q-mt-md")
    ui.label("Leurs messages ne sont jamais signalés comme suspects (vous les avez indiqués vous-même avec « Ce n'est pas une arnaque »). Une adresse ou un "
             "domaine par ligne : retirez une ligne pour que la protection s'applique de nouveau.").classes("text-caption text-grey")
    safe = ui.textarea(value="\n".join(security.safe_senders())).props("outlined autogrow dense").classes("w-full")

    def save_safe() -> None:
        security.set_safe_senders(_lines(safe.value))
        ui.notify("Expéditeurs sûrs enregistrés", type="positive")

    ui.button("Enregistrer", icon="save", on_click=save_safe).props("outline no-caps dense")
    ui.label("Expéditeurs et domaines exclus de l'IA").classes("text-subtitle2 q-mt-md")
    ui.label("Leurs mails ne sont jamais analysés, résumés ni traduits par l'IA, même sur demande. Une adresse ou un domaine par ligne "
             "(ex : cpam.fr, patient@exemple.fr).").classes("text-caption text-grey")
    ex = ui.textarea(value="\n".join(aipolicy.excluded_patterns())).props("outlined autogrow dense").classes("w-full")

    def save_ex() -> None:
        aipolicy.set_excluded_patterns(_lines(ex.value))
        ui.notify("Exclusions enregistrées", type="positive")

    ui.button("Enregistrer", icon="save", on_click=save_ex).props("outline no-caps dense")
    if extra:
        extra()


def usage_panel() -> None:
    """Suivi de la consommation IA : mois en cours, budget, fonctions, 30 derniers jours, mois précédents."""
    u = aipolicy.usage_summary()
    ui.label("Consommation de l'IA").classes("text-subtitle2")
    pct = (u["month"] / u["budget"]) if u["budget"] else 0
    ui.label(f"{u['month']:.2f} € ce mois-ci" + (f" sur {u['budget']:.2f} € de budget" if u["budget"] else "")).style("font-size:22px;font-weight:700")
    if u["budget"]:
        ui.linear_progress(value=min(1, pct), show_value=False, color="negative" if pct >= 1 else ("warning" if pct >= 0.8 else "primary")).props("rounded size=10px")
    ui.label(f"Estimation d'après les jetons mesurés et les tarifs publics, au taux de {u['rate']:.2f} € pour 1 $. La facture réelle d'Anthropic fait foi. Les appels via 1min.AI sont comptés à 0 € ici : ils consomment vos crédits 1min.AI.").classes("text-caption text-grey")

    from . import onemin
    oc = onemin.credits_summary()
    if oc:
        ui.label((f"1min.AI ce mois-ci, mesuré par l'appli : {oc['credits']:,} crédits pour {oc['calls']} appel(s) (API native uniquement). "
                  "Votre solde réel est affiché sur votre tableau de bord 1min.AI.").replace(",", " ")).classes("text-caption")
    with ui.row().classes("items-center gap-3 q-mt-sm"):
        bud = ui.number("Budget mensuel (€, 0 = aucun)", value=u["budget"], min=0, step=1, precision=2).props("outlined dense").style("width:230px")
        hard = ui.switch("Bloquer l'IA au plafond", value=u["hard"])

    def save_budget() -> None:
        db.kv_set("ai_budget_eur", str(bud.value or 0))
        db.kv_set("ai_budget_hard", "1" if hard.value else "0")
        ui.notify("Budget enregistré. Une alerte vous prévient à 80 %.", type="positive")

    ui.button("Enregistrer le budget", icon="save", on_click=save_budget).props("outline no-caps dense")

    if u["by_feature"]:
        ui.label("Par fonction, ce mois-ci").classes("text-caption q-mt-md")
        for name, calls, eur in u["by_feature"]:
            with ui.row().classes("w-full items-center no-wrap gap-2"):
                ui.label(name).classes("col")
                ui.label(f"{calls} appel(s)").classes("text-caption text-grey")
                ui.label(f"{eur:.3f} €").style("min-width:70px;text-align:right")
    top = max((e for _, e in u["by_day"]), default=0) or 1
    ui.label("30 derniers jours").classes("text-caption q-mt-md")
    with ui.row().classes("items-end no-wrap gap-1").style("height:60px"):
        for day, eur in u["by_day"]:
            ui.element("div").style(f"width:9px;height:{max(2, int(56 * eur / top))}px;background:{'var(--accent)' if eur else 'var(--line)'};border-radius:2px").tooltip(
                f"{day[8:]}/{day[5:7]} : {eur:.3f} €")
    if u["by_month"]:
        ui.label("Mois précédents").classes("text-caption q-mt-md")
        ui.label(" · ".join(f"{m[5:]}/{m[:4]} : {e:.2f} €" for m, e in u["by_month"])).classes("text-body2")


def send_alert() -> dict | None:
    """Dernière alerte d'envoi (rattachement manqué ou envoi incertain) non encore lue, sinon None."""
    import json
    from . import db
    try:
        a = json.loads(db.kv_get("send_alert") or "null")
    except ValueError:
        return None
    return a if isinstance(a, dict) and a.get("kind") else None


def alert_text(a: dict) -> str:
    subject = f" (« {a['subject']} »)" if a.get("subject") else ""
    if a["kind"] == "uncertain":
        return f"Un envoi est incertain{subject} : le message est peut-être parti. Vérifiez le dossier « Envoyés » avant de le renvoyer."
    if a["kind"] == "nocopy":
        return f"Un message{subject} est bien parti, mais il n'a pas pu être copié dans votre dossier « Envoyés » : vous n'en garderez pas de trace ici."
    return f"Un message{subject} est parti sans être rattaché à sa conversation d'origine. Il est bien envoyé, mais il apparaîtra à part."


def send_stats_panel() -> None:
    """Compteurs provisoires des réponses et transferts rattachés à leur conversation : de quoi voir si la méthode tient chez les utilisateurs."""
    import json
    from . import db
    try:
        s = json.loads(db.kv_get("send_stats") or "{}")
    except ValueError:
        s = {}
    ui.label("Réponses et transferts rattachés à leur conversation").classes("text-subtitle2 q-mt-lg")
    ui.label("Compteurs provisoires, pour vérifier que l'envoi rattaché à la conversation fonctionne sur votre serveur.").classes("text-caption text-grey")
    rows = [("threaded", "Rattachés avec succès", "Le message est dans la conversation de l'original."),
            ("fallback", "Envoyés sans rattachement", "Le rattachement a échoué : le message est parti normalement, dans une conversation à part."),
            ("uncertain", "Envois incertains", "L'envoi a échoué sans qu'on sache si le message est parti : vérifiez le dossier « Envoyés »."),
            ("nocopy", "Envoyés sans copie dans « Envoyés »", "Boîtes IMAP : le message est parti, mais sa copie n'a pas pu être rangée dans le dossier « Envoyés ».")]
    for key, label, hint in rows:
        n = int(s.get(key, 0))
        with ui.row().classes("w-full items-center no-wrap gap-2"):
            ui.label(label).classes("col").tooltip(hint)
            ui.label(str(n)).style("min-width:50px;text-align:right;font-weight:700" + (";color:#d92d3a" if key != "threaded" and n else ""))
    ui.label("Détail et conseils : survolez chaque ligne.").classes("text-caption text-grey")

    def reset() -> None:
        db.kv_set("send_stats", "{}")
        db.kv_set("send_alert", "")
        ui.notify("Compteurs remis à zéro", type="positive")

    ui.button("Remettre à zéro", icon="restart_alt", on_click=reset).props("flat no-caps dense size=sm")


# =====================================================================================
#  Chiffrement de la base et verrouillage de l'application
# =====================================================================================
IDLE_CHOICES = {0: "Jamais (seulement à l'ouverture)", 1: "Après 1 minute d'inactivité", 5: "Après 5 minutes", 15: "Après 15 minutes",
                30: "Après 30 minutes", 60: "Après 1 heure"}


def security_panel(host, lock_now=None) -> None:
    """Sections « Chiffrement » et « Verrouillage » de l'onglet Confidentialité et sécurité. lock_now : fonction qui verrouille l'écran."""
    from nicegui import run
    from . import vault

    # ---------- chiffrement
    ui.label("Chiffrement des mails stockés sur cet ordinateur").classes("text-subtitle2 q-mt-lg")
    state = ui.column().classes("w-full gap-1")

    @ui.refreshable
    def encryption_view() -> None:
        enc = vault.is_encrypted()
        if not vault.available():
            ui.label("Non disponible sur ce système (bibliothèque de chiffrement ou trousseau absents).").classes("text-caption text-grey")
            return
        with ui.row().classes("items-center gap-2"):
            ui.icon("lock" if enc else "lock_open", size="20px").style("color:" + ("var(--accent)" if enc else "var(--muted)"))
            ui.label("Base chiffrée (AES-256) : un fichier copié ou un disque volé reste illisible." if enc else
                     "Base non chiffrée : vos mails sont lisibles par quiconque accède au fichier.").classes("text-body2")
        ui.label("La clé est aléatoire et gardée dans le trousseau du système, jamais à côté du fichier. Vos mails restent aussi sur vos serveurs.").classes(
            "text-caption text-grey")
        if enc:
            ui.button("Afficher la clé de récupération", icon="key", on_click=show_key).props("outline no-caps dense")
            ui.button("Désactiver le chiffrement", icon="lock_open", on_click=turn_off).props("flat no-caps dense color=negative")
        else:
            ui.button("Chiffrer ma base maintenant", icon="lock", on_click=turn_on).props("unelevated no-caps dense color=primary")

    async def recovery_dialog(k: str, must_confirm: bool) -> None:
        with host, ui.dialog().props("persistent" if must_confirm else "") as dlg, ui.card().style("width:560px;max-width:96vw"):
            ui.label("Clé de récupération").classes("text-h6")
            ui.label("Conservez-la dans un gestionnaire de mots de passe ou imprimée, en lieu sûr. Elle sert à rouvrir la base si le trousseau de ce "
                     "Mac est réinitialisé. Sans elle, il faudrait resynchroniser vos mails depuis vos serveurs (ils n'y sont pas perdus).").classes("text-body2")
            ui.label(k).style("font-family:monospace;word-break:break-all;user-select:text;background:var(--surface2);padding:10px;border-radius:8px;font-size:12.5px")
            ok = ui.checkbox("J'ai conservé cette clé", value=not must_confirm)
            with ui.row().classes("justify-end w-full gap-2"):
                ui.button("Copier", icon="content_copy", on_click=lambda: (ui.clipboard.write(k), ui.notify("Clé copiée", type="positive", timeout=1500))).props("flat no-caps")
                close = ui.button("Fermer", on_click=lambda: dlg.submit(True)).props("unelevated no-caps color=primary")
                close.bind_enabled_from(ok, "value")
        await dlg

    async def turn_on() -> None:
        with host, ui.dialog() as dlg, ui.card().style("width:540px;max-width:96vw"):
            ui.label("Chiffrer la base de données ?").classes("text-h6")
            ui.label("L'opération prend quelques secondes (l'appli est figée pendant ce temps). Une clé de récupération vous sera montrée : "
                     "notez-la. L'ancien fichier non chiffré est effacé.").classes("text-body2")
            with ui.row().classes("justify-end w-full gap-2"):
                ui.button("Annuler", on_click=lambda: dlg.submit(False)).props("flat no-caps")
                ui.button("Chiffrer", on_click=lambda: dlg.submit(True)).props("unelevated no-caps color=primary")
        if not await dlg:
            return
        ui.notify("Chiffrement en cours…")
        from . import db
        try:
            k = await run.io_bound(db.exclusive, vault.enable_encryption)
        except Exception as e:
            return ui.notify(f"Chiffrement impossible : {e}", type="negative", multi_line=True)
        encryption_view.refresh()
        await recovery_dialog(k, True)

    async def show_key() -> None:
        k = vault.key()
        if not k:
            return ui.notify("Clé introuvable.", type="negative")
        await recovery_dialog(k, False)

    async def turn_off() -> None:
        with host, ui.dialog() as dlg, ui.card().style("width:500px;max-width:96vw"):
            ui.label("Désactiver le chiffrement ?").classes("text-h6")
            ui.label("La base redevient lisible par tout programme qui accède au fichier.").classes("text-body2")
            with ui.row().classes("justify-end w-full gap-2"):
                ui.button("Annuler", on_click=lambda: dlg.submit(False)).props("flat no-caps")
                ui.button("Désactiver", on_click=lambda: dlg.submit(True)).props("unelevated no-caps color=negative")
        if not await dlg:
            return
        from . import db
        try:
            await run.io_bound(db.exclusive, vault.disable_encryption)
        except Exception as e:
            return ui.notify(f"Opération impossible : {e}", type="negative", multi_line=True)
        ui.notify("Chiffrement désactivé", type="info")
        encryption_view.refresh()

    with state:
        encryption_view()

    # ---------- verrouillage
    from . import db
    ui.label("Verrouillage de l'application").classes("text-subtitle2 q-mt-lg")
    ui.label("Un code (ou Touch ID) est demandé à chaque ouverture. Utile si l'ordinateur est partagé ou laissé sans surveillance.").classes("text-caption text-grey")

    @ui.refreshable
    def lock_view() -> None:
        on = vault.lock_enabled(db)
        ui.switch("Verrouiller avec un code", value=on, on_change=lambda e: toggle(e.value))
        if not on:
            return
        ui.select(IDLE_CHOICES, value=vault.idle_minutes(db), label="Verrouiller automatiquement",
                  on_change=lambda e: db.kv_set("lock_idle", str(e.value or 0))).props("outlined dense").style("max-width:320px")
        if not vault.is_encrypted() and vault.available():
            with ui.row().classes("items-center no-wrap gap-2 q-pa-sm w-full").style("background:#fff4e0;border:1px solid #f3c77a;border-radius:10px;color:#6b4300"):
                ui.icon("warning_amber", size="20px").style("color:#c77800")
                ui.label("Sans chiffrement de la base, ce code ne protège que l'écran : le fichier de vos mails reste lisible par un programme qui y accède. "
                         "Chiffrez la base (section ci-dessus) pour une vraie protection.").classes("text-body2 col")
        if vault.biometric_available():
            ui.switch("Autoriser Touch ID", value=db.kv_get("lock_bio") == "1", on_change=lambda e: db.kv_set("lock_bio", "1" if e.value else "0"))
        with ui.row().classes("gap-2"):
            ui.button("Changer le code", icon="password", on_click=lambda: set_code_dialog(True)).props("outline no-caps dense")
            if lock_now:
                ui.button("Verrouiller maintenant", icon="lock", on_click=lock_now).props("outline no-caps dense")

    async def set_code_dialog(change: bool) -> bool:
        with host, ui.dialog() as dlg, ui.card().style("width:440px;max-width:96vw"):
            ui.label("Nouveau code" if change else "Choisir un code").classes("text-h6")
            a = ui.input("Code (4 caractères au moins)", password=True, password_toggle_button=True).props("outlined dense autofocus").classes("w-full")
            b = ui.input("Confirmer le code", password=True).props("outlined dense").classes("w-full")
            msg = ui.label("").classes("text-negative text-caption")

            def ok() -> None:
                if len(a.value or "") < 4:
                    msg.set_text("Au moins 4 caractères.")
                elif a.value != b.value:
                    msg.set_text("Les deux codes sont différents.")
                else:
                    dlg.submit(a.value)

            b.on("keydown.enter", ok)
            with ui.row().classes("justify-end w-full gap-2"):
                ui.button("Annuler", on_click=lambda: dlg.submit(None)).props("flat no-caps")
                ui.button("Enregistrer", on_click=ok).props("unelevated no-caps color=primary")
        code = await dlg
        if not code:
            return False
        vault.set_code(db, code)
        from nicegui import app
        app.storage.user["unlock"] = vault.BOOT_ID
        ui.notify("Code enregistré. Notez-le : s'il est oublié, il faudra le réinitialiser avec le mot de passe d'une boîte mail.", type="positive", multi_line=True)
        return True

    async def toggle(on: bool) -> None:
        if on:
            if not await set_code_dialog(False):
                pass
        else:
            with host, ui.dialog() as dlg, ui.card().style("width:440px;max-width:96vw"):
                ui.label("Désactiver le verrouillage").classes("text-h6")
                cur = ui.input("Code actuel", password=True).props("outlined dense autofocus").classes("w-full")
                cur.on("keydown.enter", lambda: dlg.submit(cur.value))
                with ui.row().classes("justify-end w-full gap-2"):
                    ui.button("Annuler", on_click=lambda: dlg.submit(None)).props("flat no-caps")
                    ui.button("Désactiver", on_click=lambda: dlg.submit(cur.value)).props("unelevated no-caps color=negative")
            code = await dlg
            if code is not None:
                ok, wait = vault.check_code(db, code)
                if ok:
                    vault.clear_code(db)
                    ui.notify("Verrouillage désactivé", type="info")
                else:
                    ui.notify("Code incorrect." if not wait else f"Trop d'essais : patientez {wait} s.", type="negative")
        lock_view.refresh()

    lock_view()
