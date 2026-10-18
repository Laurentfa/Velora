"""Fenêtre des paramètres (onglets : boîtes, affichage, dossiers, IA, consignes, règles, absence, automatismes, confidentialité, API, utilisation)."""
import asyncio
import html
import json
import re
from datetime import datetime
from pathlib import Path

from nicegui import run, ui

from mailia import ai, api_ui, apple_writer, dictation, folder_order, gcal, onemin, outgoing, compose, config, db, htmlmail, i18n, logs, notify, perf, queries, settings_ui, sync, wizard
from mailia.textutil import html_to_text

from mailia.ui_account import delete_account_dialog, friendly_error, open_account
from mailia.ui_common import CAT, SERVER_MODE, default_account, make_editor, open_folder, reveal
from mailia.ui_lock import lock_screen_now
from mailia.ui_update import panel as update_panel
from mailia import ui_zoom


def state(ok: bool) -> str:
    return "défini ✓ (laisser vide pour conserver)" if ok else "non défini"



def _tab_api(host) -> None:
    """Onglet « API et assistants »."""
    api_ui.api_panel(host, lambda url: ui.navigate.to(url, new_tab=True))


def _tab_dictation(host) -> None:
    """Onglet « Dictée vocale » : le moteur utilisé (Apple sur Mac ; Whisper ou Parakeet ailleurs), ses modèles à télécharger, le micro et un diagnostic."""
    from . import dictation, parakeet_engine, whisper_engine
    from .ui_dictation import whisper_download_dialog
    ui.label("Dictée vocale").classes("text-h6").style("font-weight:700")
    if dictation.engine() == "apple":
        ui.label("Velora utilise la reconnaissance vocale d'Apple, sur ce Mac : rien n'est téléchargé par Velora, rien ne quitte votre ordinateur et l'IA n'est pas utilisée.").classes("text-body2")
        ui.label("Le micro se trouve dans la rédaction : à côté de « ✦ Rédiger » pour dicter une consigne à l'IA, et sous l'objet pour dicter le message.").classes("rmeta")
        return
    ui.label("La reconnaissance se fait sur cet ordinateur : rien ne quitte votre ordinateur et l'IA n'est pas utilisée. "
             "Le modèle de langue se télécharge une fois, avec votre accord.").classes("text-body2")

    @ui.refreshable
    def engine_section() -> None:
        current = dictation.engine()
        if whisper_engine.libs_ok() and parakeet_engine.libs_ok():
            def pick_engine(e) -> None:
                db.kv_set("dictation_engine", e.value)
                engine_section.refresh()
            ui.radio({"whisper": "Whisper (OpenAI) — de 60 à 540 Mo ; sait recevoir vos noms propres",
                      "parakeet": f"Parakeet (NVIDIA) — {whisper_engine.human_mb(parakeet_engine.TOTAL)} ; plus précis en français et plus rapide, plus sensible au bruit de fond"},
                     value=current, on_change=pick_engine).props("dense").mark("dictation-engine")
        mod = dictation.engine_module(current)
        if len(mod.MODELS) > 1:
            def pick(e) -> None:
                mod.set_chosen(e.value)
                engine_section.refresh()
            ui.radio({k: f"{m.title} — {whisper_engine.human_mb(m.size)} ({m.hint})" for k, m in mod.MODELS.items()}, value=mod.chosen(), on_change=pick).props("dense").mark("dictation-models")
        if current == "parakeet":
            level = parakeet_engine.reactivity()
            title = ui.label(f"Réactivité : {parakeet_engine.PRESETS[level][0]}").classes("text-body2 q-mt-md").style("font-weight:600")

            def set_level(e) -> None:
                db.kv_set("dictation_reactivity", str(int(e.value)))
                title.set_text(f"Réactivité : {parakeet_engine.PRESETS[int(e.value)][0]}")
            ui.slider(min=1, max=4, step=1, value=level, on_change=set_level).props("markers snap label-always").classes("w-full").style("max-width:420px").mark("dictation-reactivity")
            with ui.row().classes("justify-between").style("max-width:420px"):
                ui.label("Économe").classes("rmeta")
                ui.label("Très réactif").classes("rmeta")
            ui.label("Plus la dictée est réactive, plus le texte s'affiche tôt, mais plus le processeur travaille pendant que vous parlez. S'applique à la prochaine dictée.").classes("rmeta")
        key = mod.chosen()
        with ui.row().classes("items-center gap-3 q-mt-sm"):
            if mod.is_installed(key):
                ui.icon("check_circle", size="20px").style("color:#1a8f3c")
                ui.label("Installé sur cet ordinateur").classes("text-body2")

                def drop() -> None:
                    mod.remove(key)
                    engine_section.refresh()
                ui.button("Supprimer le modèle", icon="delete_outline", on_click=drop).props("flat no-caps dense").mark("dictation-remove")
            else:
                ui.icon("cloud_download", size="20px").style("color:var(--muted)")
                ui.label("Pas encore téléchargé").classes("text-body2")

                async def fetch() -> None:
                    await whisper_download_dialog(host, key, mod)
                    engine_section.refresh()
                ui.button("Télécharger", icon="download", on_click=fetch).props("unelevated no-caps dense color=primary").mark("dictation-download")
    engine_section()
    ui.switch("Charger le modèle à l'ouverture de la rédaction (dictée immédiate, mais le modèle occupe environ 1 Go de mémoire pendant 10 minutes)",
              value=(db.kv_get("dictation_preload") or "1") == "1",
              on_change=lambda e: db.kv_set("dictation_preload", "1" if e.value else "0")).classes("q-mt-md").mark("dictation-preload")
    ui.separator().classes("q-my-md")
    ui.label("Micro et diagnostic").classes("text-subtitle1").style("font-weight:600")
    try:
        devices = whisper_engine.input_devices()
    except Exception:  # noqa: BLE001 — pas de bibliothèque audio ou aucun micro
        devices = []
    default = whisper_engine.device_name()
    opts = {"": f"Micro par défaut du système ({default})", **{str(i): n for i, n in devices}}
    cur = db.kv_get("dictation_device") or ""
    ui.select(opts, value=cur if cur in opts else "", label="Micro utilisé pour la dictée",
              on_change=lambda e: db.kv_set("dictation_device", e.value or "")).props("outlined dense").classes("w-full").mark("dictation-device")
    result = ui.column().classes("gap-0 q-mt-sm")
    level = ui.linear_progress(value=0, show_value=False).classes("w-full").mark("dictation-level")
    level.set_visibility(False)

    async def record(job, *args) -> dict:
        result.clear()
        state = {"p": 0.0}
        level.set_visibility(True)
        timer = ui.timer(0.15, lambda: level.set_value(min(1.0, state["p"])))
        test_btn.disable()
        compare_btn.disable()
        with result:
            ui.label("Parlez maintenant pendant 5 secondes…").classes("text-body2")
        r = await run.io_bound(job, *args, lambda peak, frac: state.update(p=peak * 2))
        timer.cancel()
        level.set_visibility(False)
        test_btn.enable()
        compare_btn.enable()
        result.clear()
        return r

    def describe_level(peak: float) -> str:
        return "très faible : parlez plus fort, rapprochez-vous ou choisissez un autre micro" if peak < 0.03 else "trop fort (saturé) : baissez le volume d'entrée" if peak > 0.98 else "correct"

    async def test() -> None:
        mod = dictation.engine_module()
        if not mod.is_installed():
            return ui.notify("Téléchargez d'abord le modèle.", type="warning")
        r = await record(lambda on_level: whisper_engine.test_microphone(5.0, None, None, on_level))
        with result:
            if r.get("error"):
                ui.label(r["error"]).classes("text-negative text-body2")
                return
            ui.label(f"Micro : {r['device']}").classes("text-body2")
            ui.label(f"Niveau : {describe_level(r['peak'])}").classes("text-body2")
            ui.label(f"Texte entendu : « {r['text'] or '(rien)'} »").classes("text-body2")
            live = r.get("fast_took")
            ratio = (live if live is not None else r["took"]) / max(r["audio_s"], 0.1)
            verdict = " — trop lent pour un texte en direct : prenez un modèle plus léger" if ratio > 0.6 else " — un peu lent : le texte provisoire sera décalé" if ratio > 0.35 else " — assez rapide"
            ui.label(f"Vitesse : {r['took']:.1f} s pour {r['audio_s']:.0f} s de parole{verdict}").classes("text-body2")
            if live is not None:
                ui.label(f"Texte provisoire (en direct) : {live:.1f} s pour {r['audio_s']:.0f} s de parole").classes("text-body2")
            if r.get("tuned"):
                ui.label(f"Réglage automatique : {r['best_threads']} fils de calcul (avant : {r['took_before']:.1f} s)").classes("text-body2")
            if r.get("engine"):
                ui.label(f"Moteur : {r['engine']}").classes("rmeta").style("word-break:break-word")

    async def compare() -> None:
        r = await record(lambda on_level: whisper_engine.compare_microphone(5.0, None, on_level))
        with result:
            if r.get("error"):
                ui.label(r["error"]).classes("text-negative text-body2")
                return
            ui.label(f"Micro : {r['device']} · niveau : {describe_level(r['peak'])}").classes("text-body2")
            for x in r["results"]:
                ui.label(f"{'Parakeet' if x['engine'] == 'parakeet' else 'Whisper'} ({x['took']:.1f} s pour {r['audio_s']:.0f} s) : « {x['text'] or '(rien)'} »").classes("text-body2")
    test_btn = ui.button("Tester le micro (5 s)", icon="mic", on_click=test).props("outline no-caps dense color=primary").classes("q-mt-sm").mark("dictation-test")
    compare_btn = ui.button("Comparer Whisper et Parakeet (5 s)", icon="compare", on_click=compare).props("outline no-caps dense color=primary").classes("q-mt-sm").mark("dictation-compare")
    ui.separator().classes("q-my-md")
    ui.label("Dernière dictée : détails").classes("text-subtitle1").style("font-weight:600")
    ui.label("Ce que le moteur de reconnaissance a renvoyé (brut) et ce que Velora a gardé, passage par passage. Utile pour comprendre une répétition ou une erreur.").classes("rmeta")

    @ui.refreshable
    def trace() -> None:
        rows = list(whisper_engine.TRACE)[-14:]
        if not rows:
            ui.label("Rien encore : dictez un message, puis revenez ici.").classes("text-body2")
        for r in rows:
            cut = lambda t: (t[:170] + "…") if len(t) > 170 else t
            ui.label(f"{r['kind']} · {r['audio_s']} s · calculé en {r['took']} s · niveau {r['rms']}").classes("rmeta").style("margin-top:6px")
            ui.label(f"brut : « {cut(r['raw'])} »").classes("text-caption").style("font-family:monospace;word-break:break-word")
            if r["kept"] != r["raw"]:
                ui.label(f"gardé : « {cut(r['kept'])} »").classes("text-caption").style("font-family:monospace;word-break:break-word;color:var(--accent)")
    trace()
    ui.button("Actualiser", icon="refresh", on_click=trace.refresh).props("flat no-caps dense").mark("dictation-trace-refresh")
    ui.label("Whisper (OpenAI), au format whisper.cpp : licence MIT. Parakeet TDT 0.6B v3 : © NVIDIA, licence CC-BY 4.0 (conversion ONNX d'istupakov).").classes("rmeta q-mt-md")


def _tab_about() -> None:
    """Onglet « À propos » : nom, version et auteur."""
    from .brand import logo_img
    from .version import APP_VERSION, UPDATE_REPO
    ui.label("À propos de Velora").classes("text-h6").style("font-weight:700")
    with ui.row().classes("items-center gap-4 no-wrap q-mt-sm"):
        ui.html(logo_img(64), sanitize=False)
        with ui.column().classes("gap-1"):
            ui.label("Velora").classes("text-h5").style("font-weight:700;color:var(--accent)")
            ui.label("Messagerie personnelle avec assistant IA intégré").classes("text-body2").style("color:var(--muted)")
    with ui.column().classes("gap-2 q-mt-md"):
        for k, v in (("Version", APP_VERSION), ("Auteur", "Laurent Fauvel")):
            with ui.row().classes("items-baseline gap-3 no-wrap"):
                ui.label(k).classes("rmeta").style("width:70px;font-weight:600")
                ui.label(v).classes("text-body1").mark(f"about-{k.lower()}")
        if UPDATE_REPO:
            with ui.row().classes("items-baseline gap-3 no-wrap"):
                ui.label("Projet").classes("rmeta").style("width:70px;font-weight:600")
                ui.link(f"github.com/{UPDATE_REPO}", f"https://github.com/{UPDATE_REPO}", new_tab=True)
    ui.label("© Laurent Fauvel. Logiciel gratuit.").classes("rmeta q-mt-lg")


def _tab_auto() -> None:
    """Onglet « Automatismes »."""
    settings_ui.automations_panel()


def _tab_priv(host) -> None:
    """Onglet « Confidentialité et sécurité »."""
    settings_ui.privacy_panel(lambda: settings_ui.security_panel(host, lock_screen_now))


def _tab_boites(dlg, host) -> None:
    """Onglet « Boîtes mail »."""
    ui.label("Boîtes mail").classes("text-h6").style("font-weight:700")

    async def go_edit(k: str | None) -> None:
        dlg.close()
        if k is None:
            await wizard.run_wizard(host, False, friendly_error)  # nouvelle boîte : l'assistant
        else:
            open_account(host, k)

    async def go_delete(k: str) -> None:
        dlg.close()
        await delete_account_dialog(host, k)

    async def toggle_account(k: str, on: bool) -> None:
        config.set_enabled(k, on)
        sync.last_error.pop(k, None)
        sync.reload()
        ui.notify("Boîte activée : synchronisation en cours." if on else "Boîte désactivée : plus aucune connexion, mails masqués.", type="positive")
        ui.navigate.reload()

    all_accounts = config.load_accounts(enabled_only=False)
    if not all_accounts:
        ui.label("Aucune boîte configurée.").classes("text-caption text-grey")
    for k, c in all_accounts.items():
        with ui.row().classes("w-full items-center no-wrap gap-2").style(
                "border:1px solid var(--line);border-radius:12px;padding:8px 10px" + ("" if c.enabled else ";opacity:.62")):
            ui.icon("alternate_email" if c.kind == "imap" else "business", size="22px").style("color:var(--accent)")
            with ui.column().classes("gap-0 col").style("min-width:0"):
                ui.label(c.label + ("" if c.enabled else " · désactivée")).style("font-weight:600")
                ui.label(f"{c.email} · {'Exchange' if c.kind == 'ews' else 'IMAP / SMTP'}" + ("" if c.password else " · mot de passe manquant")
                         ).classes("rmeta").style("overflow:hidden;text-overflow:ellipsis;white-space:nowrap")
                if sync.last_error.get(k):
                    ui.label("⚠ " + friendly_error(Exception(sync.last_error[k]))).classes("rmeta").style("color:var(--hi);white-space:normal")
            ui.switch(value=c.enabled, on_change=lambda e, k=k: toggle_account(k, e.value)).props("dense").tooltip(
                "Boîte activée : synchronisée et affichée" if c.enabled else "Boîte désactivée : suspendue, mails masqués")
            ui.button(icon="edit", on_click=lambda _, k=k: go_edit(k)).props("flat dense round").tooltip("Modifier")
            ui.button(icon="delete_outline", on_click=lambda _, k=k: go_delete(k)).props("flat dense round color=negative").tooltip("Retirer cette boîte")
    ui.button("Ajouter une boîte mail", icon="add", on_click=lambda: go_edit(None)).props("outline no-caps dense").classes("q-mt-xs")
    active = {k: c.label for k, c in all_accounts.items() if c.enabled}
    if len(active) > 1:
        ui.select({"": "Toutes les boîtes", **active}, value=default_account() if default_account() in active else "", label="Boîte affichée à l'ouverture",
                  on_change=lambda e: db.kv_set("default_account", e.value or "")).props("outlined dense stack-label").classes("w-full q-mt-md")
        ui.label("À chaque lancement de l'appli, c'est la boîte de réception de cette boîte qui s'affiche. Les autres restent accessibles dans la colonne "
                 "de gauche, quand vous les sélectionnez.").classes("text-caption text-grey")
    kinds = {"ews": "Exchange", "imap": "IMAP"}
    present = [k for k in kinds if any(c.kind == k for c in all_accounts.values())] or list(kinds)
    ui.label("Mise à jour automatique : " + ", ".join(f"{kinds[k]} toutes les {config.SYNC_INTERVAL[k]} s" for k in present)
             + " (synchro incrémentale).").classes("text-caption text-grey")


def _tab_aff(f) -> None:
    """Onglet « Affichage et signature »."""
    ui.label("Affichage et signature").classes("text-h6").style("font-weight:700")
    ui.input("Votre nom", value=config.user_name(), placeholder="ex : Marie Dupont",
             on_change=lambda e: db.kv_set("user_name", (e.value or "").strip())).props("outlined dense stack-label").classes("w-full")
    ui.label("Utilisé par l'IA (« rédige pour Marie ») et pour la signature par défaut quand vous n'en avez pas écrit.").classes("text-caption text-grey")

    def change_language(e) -> None:
        if e.value == i18n.setting():
            return
        i18n.set_language(e.value)
        ui.notify("Language changed. Restart the app to update dates everywhere." if e.value == "en" else
                  "Langue modifiée. Redémarrez l'application pour mettre à jour partout les dates.", type="positive")
        ui.navigate.reload()

    ui.select({"auto": "Automatique (langue du système)", **i18n.AVAILABLE}, value=i18n.setting(), label="Langue de l'interface",
              on_change=change_language).props("outlined dense stack-label").classes("w-full")
    ui.label("Le contenu de vos mails n'est jamais traduit automatiquement. Les résumés de l'IA suivent la langue choisie.").classes("text-caption text-grey")
    def change_theme(e) -> None:
        db.kv_set("theme", e.value)
        ui.run_javascript("Quasar.Dark.set(" + {"light": "false", "dark": "true"}.get(e.value, "'auto'") + ")")

    ui.select({"auto": "Automatique (suit le système)", "light": "Clair", "dark": "Sombre"}, value=db.kv_get("theme") or "auto",
              label="Thème", on_change=change_theme).props("outlined dense stack-label").classes("w-full q-mt-sm")
    status = ui.label(ui_zoom.status()).classes("text-caption text-grey")

    async def change_zoom(e) -> None:
        ui_zoom.set_zoom(e.value)
        await asyncio.sleep(1.2)  # la fenêtre applique le réglage dans la seconde ; on affiche ce qu'elle a répondu
        status.set_text(ui_zoom.status())

    ui.select({z: ui_zoom.label(z) for z in ui_zoom.ZOOMS}, value=ui_zoom.zoom(), label="Zoom de l'interface",
              on_change=change_zoom).props("outlined dense stack-label").classes("w-full q-mt-sm").mark("ui-zoom")
    ui.label("Réduire le zoom affiche plus de choses à l'écran. Raccourcis : ⌘ + et ⌘ − (Ctrl sous Windows), ⌘ 0 pour revenir à 100 %.").classes("text-caption text-grey")
    ui.switch("Liste de mails compacte (plus de mails à l'écran)", value=ui_zoom.compact(),
              on_change=lambda e: ui_zoom.set_compact(bool(e.value))).props("dense").classes("q-mt-sm").mark("density-switch")
    ui.label("Une ligne par mail : expéditeur, objet et date, sans résumé de l'IA ni pastilles (sauf « Urgent »). Les actions apparaissent au survol.").classes("text-caption text-grey")
    ui.switch("Regrouper les mails en conversations").bind_value(f, "CONVERSATIONS")
    ui.label("Un seul élément par échange (avec le nombre de messages), les messages envoyés inclus.").classes("text-caption text-grey")

    ui.select({0: "Aucun (envoi immédiat)", 5: "5 secondes", 10: "10 secondes", 20: "20 secondes", 30: "30 secondes"},
              value=min((0, 5, 10, 20, 30), key=lambda v: abs(v - outgoing.undo_delay())), label="Délai pour annuler un envoi",
              on_change=lambda e: db.kv_set("undo_delay", str(e.value))).props("outlined dense stack-label").classes("w-full q-mt-sm")
    ui.label("Après « Envoyer », le message attend ce délai : le bouton « Annuler » le rappelle comme brouillon.").classes("text-caption text-grey")

    ui.label("Signature").classes("text-subtitle2 q-mt-md")
    ui.switch("Ajouter ma signature à mes messages et à mes réponses").bind_value(f, "SIG_ON")
    make_editor(f["SIG_HTML"], lambda e: f.__setitem__("SIG_HTML", e.value or ""), "110px")
    ui.label("Mise en forme libre (police, couleurs, liens, image de logo…). Elle est insérée dans l'éditeur, où vous pouvez la modifier.").classes("text-caption text-grey")


def _tab_dos(cfgs, dlg, on_folders) -> None:
    """Onglet « Dossiers »."""
    ui.label("Dossiers").classes("text-h6").style("font-weight:700")
    ui.label("Choisissez les dossiers affichés dans l'application, boîte par boîte :").classes("text-caption text-grey")

    async def open_folders(a: str) -> None:
        dlg.close()
        await on_folders(a)

    with ui.row().classes("gap-2"):
        for k, c in cfgs.items():
            ui.button(f"Dossiers de {c.label}…", icon="folder_open", on_click=lambda _, a=k: open_folders(a)).props("outline no-caps dense")
    def reset_order() -> None:
        folder_order.reset_all()
        ui.notify("Dossiers remis dans l'ordre alphabétique.", type="positive")
        ui.navigate.reload()

    ui.button("Remettre les dossiers dans l'ordre alphabétique", icon="sort_by_alpha", on_click=reset_order).props("outline no-caps dense").classes("q-mt-sm").mark("folder-order-reset")
    ui.label("Dans la colonne de gauche, glissez un dossier avant ou après un autre dossier du même niveau pour le ranger comme vous voulez.").classes("text-caption text-grey")
    ui.switch("Chercher aussi sur le serveur de messagerie", value=sync.server_search_on(),
              on_change=lambda e: db.kv_set("server_search", "1" if e.value else "0")).props("dense").classes("q-mt-md").mark("server-search")
    ui.label("Une recherche regarde d'abord les mails copiés dans l'application, puis interroge aussi votre messagerie (Exchange, IMAP) pour retrouver des "
             "mails plus anciens que ceux gardés ici. Les mots cherchés ne sont envoyés qu'à votre propre serveur, jamais à l'IA. Les mails trouvés sont "
             "ajoutés à la liste, leur texte est chargé quand vous les ouvrez.").classes("text-caption text-grey")


def _tab_ia(f) -> None:
    """Onglet « Intelligence artificielle »."""
    ui.label("Intelligence artificielle").classes("text-h6").style("font-weight:700")
    ui.switch("Activer les fonctions IA (résumés, tri, brouillons, brief)").bind_value(f, "AI_ENABLED")
    if not config.ai_has_key():
        ui.label("Aucune clé d'API n'est enregistrée : les fonctions IA restent masquées. Elles apparaissent dès que vous ajoutez une clé ci-dessous "
                 "(Claude ou 1min.AI), puis « Enregistrer ».").classes("text-caption text-negative")
    ui.label("Désactivées : plus aucun envoi à un fournisseur d\'IA, et les éléments IA disparaissent de l'écran.").classes("text-caption text-grey")
    if apple_writer.supported():
        ui.switch("Boutons Apple Intelligence dans la rédaction (corriger, reformuler)", value=apple_writer.enabled(),
                  on_change=lambda e: db.kv_set("apple_writer", "1" if e.value else "0")).mark("apple-writer-switch")
        ui.label("Apple Intelligence tourne sur ce Mac : gratuit, rien n'est envoyé, et ces boutons restent disponibles même si les fonctions IA ci-dessus sont désactivées "
                 "ou sans clé. Il faut l'activer dans Réglages Système › Apple Intelligence et Siri.").classes("text-caption text-grey")
    with ui.row().classes("w-full items-center no-wrap gap-2"):
        ui.switch("Notification pour les mails urgents, les messages à revoir et les relances", value=notify.enabled(),
                  on_change=lambda e: db.kv_set("notify_urgent", "1" if e.value else "0")).classes("col")
        ui.button("Tester", icon="notifications", on_click=lambda: ui.notify(
            "Notification envoyée." if notify.send("Velora", "Voici à quoi ressemblera une notification.") else
            "Les notifications ne sont pas encore prises en charge sur ce système.", type="positive")).props("outline dense no-caps")
    ui.label("Fournisseur d'IA").classes("text-caption q-mt-sm")
    ui.toggle(config.PROVIDERS, value=f["AI_PROVIDER"]).bind_value(f, "AI_PROVIDER").props("no-caps unelevated")
    ui.label("Fournisseur utilisé par toutes les fonctions IA, sauf celles que vous réglez séparément plus bas.").classes("text-caption text-grey")
    ui.input("Clé API Claude", password=True, password_toggle_button=True,
             placeholder=state(bool(config.env("ANTHROPIC_API_KEY")))).bind_value(f, "ANTHROPIC_API_KEY").props("outlined dense stack-label").classes("w-full")
    ui.input("Clé API 1min.AI", password=True, password_toggle_button=True,
             placeholder=state(bool(config.env("ONEMIN_API_KEY")))).bind_value(f, "ONEMIN_API_KEY").props("outlined dense stack-label").classes("w-full")
    model_selects: list = []

    def known_ids() -> list[str]:
        try:
            ids = json.loads(db.kv_get("onemin_models") or "{}").get("ids") or []
        except ValueError:
            ids = []
        return sorted({*ids, config.ONEMIN_DEFAULT_MODEL, *(f[f"MODEL1_{t}"] for t in config.TASKS if f[f"MODEL1_{t}"])})

    async def test_onemin() -> None:
        key = (f["ONEMIN_API_KEY"] or "").strip()
        ok, msg = await run.io_bound(onemin.test, key)  # essai avec la clé saisie (sinon l'enregistrée), sans l'enregistrer
        ui.notify(msg, type="positive" if ok else "negative", multi_line=True)
        if ok:
            for s in model_selects:
                s.set_options(known_ids())

    with ui.row().classes("items-center gap-2"):
        ui.button("Tester 1min.AI et charger ses modèles", icon="bolt", on_click=test_onemin).props("outline dense no-caps")
        ui.button("Obtenir une clé (1min.ai)", icon="open_in_new",
                  on_click=lambda: ui.navigate.to("https://app.1min.ai/api", new_tab=True)).props("flat dense no-caps size=sm")
    ui.label("1min.AI donne aussi accès à Claude et à Gemini (modèles « us.anthropic… », « gemini… »). Les photos et documents numérisés sont "
             "envoyés à 1min.AI (stockage chez eux) pour être lus. La consommation se déduit de vos crédits 1min.AI ; elle n'entre pas dans "
             "le budget en euros ci-dessous. Limite : 60 requêtes par minute.").classes("text-caption text-grey")
    ui.switch("Si le fournisseur choisi est en panne ou sans crédit, essayer l'autre").bind_value(f, "AI_FALLBACK")
    ui.select(["neutre", "chaleureux", "très formel", "bref et direct", "ferme"], label="Ton par défaut des brouillons IA",
              value=db.kv_get("ai_tone") or "neutre", on_change=lambda e: db.kv_set("ai_tone", e.value or "neutre")
              ).props("outlined dense").classes("w-full q-mt-sm")
    ui.label("Fournisseur et modèle par tâche").classes("text-caption q-mt-sm")
    prov_opts = {"default": "Fournisseur général", **config.PROVIDERS}
    for task, name in config.TASKS.items():
        with ui.row().classes("w-full items-center no-wrap gap-2"):
            ui.select(prov_opts, label=name).bind_value(f, f"PROVIDER_{task}").props("outlined dense options-dense").style("width:210px")
            ui.select(config.MODEL_CHOICES, label="Modèle Claude").bind_value(f, f"MODEL_{task}").props("outlined dense options-dense").classes("col")
            sel = ui.select(known_ids(), label="Modèle 1min.AI", with_input=True, new_value_mode="add-unique").bind_value(
                f, f"MODEL1_{task}").props("outlined dense options-dense").classes("col")
            model_selects.append(sel)
    ui.label("Analyse par boîte (uniquement la boîte de réception)").classes("text-caption q-mt-sm")
    ui.label("L'analyse IA se règle boîte par boîte : bouton « Modifier » de la boîte concernée.").classes("text-caption text-grey")
    ui.label("Quels mails analyser automatiquement ?").classes("text-caption q-mt-sm")
    ui.switch("Seulement les mails non lus").bind_value(f, "AI_UNREAD_ONLY")
    ui.number("Ignorer les mails de plus de … jours (0 = aucune limite)", min=0, max=3650, step=1, precision=0).bind_value(f, "AI_MAX_AGE_DAYS").props("outlined dense").classes("w-full")
    ui.label("Les autres mails restent analysables à la demande (bouton « Analyser ce message »).").classes("text-caption text-grey")


def _tab_prm(f, host) -> None:
    """Onglet « Consignes de l'IA »."""
    ui.label("Consignes de l'IA").classes("text-h6").style("font-weight:700")
    if not config.ai_enabled():
        return ui.label("Ces consignes règlent le travail de l'IA. Elles apparaissent dès que les fonctions IA sont actives : ajoutez une clé d'API dans "
                        "l'onglet « Intelligence artificielle ».").classes("text-caption text-grey")
    with ui.column().classes("w-full gap-1"):
        ui.label("Vous décidez des critères. Le format des réponses de l'IA est géré par l'application, pour que rien ne casse.").classes("text-caption text-grey")
        boxes: dict = {}
        for key, title, hint in (
                ("analyze", "Tri et résumé des mails reçus", ""),
                ("brief", "Brief du jour : quoi mettre en priorité", ""),
                ("draft", "Style des réponses et des nouveaux mails", ""),
                ("rules", "Mes règles personnelles (tri, brief et réponses)",
                 "ex : Un mail de mon banquier est toujours urgent.\nLes messages de Dupont sans question sont de l'information.")):
            boxes[key] = ui.textarea(title, placeholder=hint).bind_value(f, f"P_{key}").props("outlined autogrow stack-label").classes("w-full q-mt-sm")
            ui.button("Rétablir l'origine", icon="restart_alt",
                      on_click=lambda _, k=key: boxes[k].set_value(ai.default_text(k))).props("flat dense no-caps size=sm")

        with db.conn() as c:
            recent = {r["id"]: f"{(r['sender_name'] or r['sender'])[:22]} — {r['subject'][:42]}" for r in c.execute(
                f"SELECT id, sender, sender_name, subject FROM messages WHERE folder='INBOX' AND {queries.enabled_sql()} ORDER BY is_read ASC, date DESC LIMIT 12")}
        ui.label("Essayer ces consignes avant de les enregistrer").classes("text-caption q-mt-md")
        f["TEST_MAIL"] = next(iter(recent), None)
        ui.select(recent, label="Mail d'essai").bind_value(f, "TEST_MAIL").props("outlined dense options-dense stack-label").classes("w-full")

        async def test_prompts() -> None:
            m = queries.get_message(f["TEST_MAIL"]) if f["TEST_MAIL"] else None
            if not m:
                return ui.notify("Aucun mail à essayer.", type="warning")
            ui.notify("Essai en cours…")
            try:
                d = await run.io_bound(ai.test_analyze, m, f["P_analyze"], f["P_rules"])
            except ai.AIError as e:
                return ui.notify(str(e), type="negative")
            with host, ui.dialog() as td, ui.card().style("width:860px;max-width:96vw"):
                ui.label("Essai de vos consignes").classes("text-h6")
                ui.label(f"Mail : {m['subject']}").classes("text-caption text-grey")
                ui.label(d["summary"]).classes("q-mt-sm")
                ui.label(f"Catégorie : {CAT.get(d['category'], d['category'])} · Priorité : {d['priority']} · "
                         f"Réponse attendue : {'oui' if d['needs_reply'] else 'non'}").classes("text-body2")
                ui.label("Suggestion : " + d["suggested"]).classes("text-body2")
                ui.label("Rien n'est enregistré : c'est un simple essai.").classes("text-caption text-grey")
                ui.button("Fermer", on_click=td.close).props("flat no-caps")
            td.open()

        async def reanalyze() -> None:
            n = queries.reanalyze_count()
            pin, pout = (0.0, 0.0) if config.model("ANALYZE").startswith(config.ONEMIN_PREFIX) else ai.PRICES.get(config.model("ANALYZE"), (1.0, 5.0))
            cost = n * (1500 * pin + 150 * pout) / 1_000_000
            with host, ui.dialog() as rd, ui.card().classes("w-[460px] max-w-full"):
                ui.label(f"Réanalyser {n} mail(s) avec vos consignes enregistrées ?").classes("text-subtitle1")
                ui.label(f"Coût estimé : environ {cost:.2f} $ ({config.model('ANALYZE')}). L'analyse se fait en arrière-plan.").classes("text-caption text-grey")
                ui.label("Enregistrez d'abord vos consignes : seules les consignes enregistrées sont utilisées.").classes("text-caption text-grey")
                with ui.row().classes("justify-end w-full"):
                    ui.button("Annuler", on_click=lambda: rd.submit(False)).props("flat no-caps")
                    ui.button("Réanalyser", on_click=lambda: rd.submit(True)).props("unelevated no-caps color=primary")
            if n and await rd:
                queries.reanalyze_reset()
                ui.notify(f"{n} mails remis en file d'attente.", type="positive")

        with ui.row().classes("gap-2 q-mt-sm"):
            ui.button("Essayer", icon="science", on_click=test_prompts).props("outline no-caps dense")
            ui.button("Réanalyser les mails…", icon="refresh", on_click=reanalyze).props("outline no-caps dense")


def _tab_rules(cfgs, host) -> None:
    """Onglet « Règles de tri automatiques »."""
    from mailia import rules as rules_mod
    ui.label("Règles de tri automatiques").classes("text-h6").style("font-weight:700")
    ui.label("Écrivez une règle comme vous la diriez à un assistant. L'IA la traduit une seule fois en règle précise, que vous vérifiez avant de "
             "l'enregistrer. Ensuite elle s'applique sans IA, aux nouveaux mails seulement : marquer comme lu, déplacer vers un dossier existant, "
             "épingler. Aucune règle ne supprime de mail.").classes("text-caption text-grey")

    @ui.refreshable
    def rules_list() -> None:
        rs = rules_mod.list_rules()
        if not rs:
            ui.label("Aucune règle pour l'instant.").classes("rmeta")
        for r in rs:
            with ui.card().classes("w-full").props("flat bordered"):
                ui.label(f"« {r['text']} »").style("font-weight:600")
                ui.label(rules_mod.describe(r["spec"])).classes("text-body2")
                with ui.row().classes("w-full items-center no-wrap gap-2"):
                    ui.switch("Active", value=bool(r["active"]), on_change=lambda e, i=r["id"]: rules_mod.set_active(i, bool(e.value))).props("dense")
                    ui.label(f"appliquée {r['hits']} fois").classes("rmeta col")

                    async def backlog(r=r) -> None:
                        n = await run.io_bound(rules_mod.preview_count, r["spec"], 500)
                        if not n:
                            return ui.notify("Aucun mail déjà présent ne correspond à cette règle.")
                        with host, ui.dialog() as cd, ui.card().style("width:480px;max-width:96vw"):
                            ui.label(f"Appliquer cette règle à {n} mail(s) déjà présent(s) ?").classes("text-h6")
                            ui.label(rules_mod.describe(r["spec"])).classes("text-body2")
                            with ui.row().classes("justify-end w-full gap-2"):
                                ui.button("Annuler", on_click=lambda: cd.submit(False)).props("flat no-caps")
                                ui.button("Appliquer", on_click=lambda: cd.submit(True)).props("unelevated no-caps color=primary")
                        if await cd:
                            done = await run.io_bound(rules_mod.apply_existing, r["id"])
                            ui.notify(f"Règle appliquée à {done} mail(s).", type="positive")
                            rules_list.refresh()

                    ui.button("Appliquer aux mails existants", icon="playlist_add_check", on_click=backlog).props("flat dense no-caps size=sm")
                    ui.button(icon="delete_outline", on_click=lambda _, i=r["id"]: (rules_mod.delete(i), rules_list.refresh())).props(
                        "flat dense round color=negative").tooltip("Supprimer la règle")

    rules_list()
    if not config.ai_enabled():
        ui.label("Créer une règle passe par l'IA, qui traduit votre phrase en règle précise. Ajoutez une clé d'API dans l'onglet « Intelligence artificielle » "
                 "pour l'activer.").classes("text-caption text-grey q-mt-md")
    else:
        ui.label("Nouvelle règle").classes("text-subtitle2 q-mt-md")
        rule_text = ui.textarea(placeholder="ex : Les newsletters de BUT, je les marque comme lues et je les range dans Archive").props(
            "outlined autogrow stack-label").classes("w-full")
        proposal = ui.column().classes("w-full gap-1")

        async def rule_interpret() -> None:
            if not config.ai_enabled():
                return ui.notify("Activez d'abord les fonctions IA.", type="warning")
            if not (rule_text.value or "").strip():
                return
            proposal.clear()
            with proposal:
                ui.spinner(size="20px")
            try:
                spec = await run.io_bound(rules_mod.interpret, rule_text.value)
            except ai.AIError as e:
                proposal.clear()
                with proposal:
                    ui.label(str(e)).classes("text-negative")
                return
            n = await run.io_bound(rules_mod.preview_count, spec)
            proposal.clear()
            with proposal:
                with ui.element("div").classes("aicard"):
                    ui.label("✦ Voici comment j'ai compris la règle").classes("tag")
                    ui.label(rules_mod.describe(spec)).classes("q-mt-xs").style("font-weight:600")
                    if spec["note"]:
                        ui.label("Remarque : " + spec["note"]).classes("sub")
                    ui.label(f"Parmi vos mails récents, {n} auraient été concernés.").classes("sub")
                    with ui.row().classes("gap-2 q-mt-sm"):
                        def keep() -> None:
                            rules_mod.save(rule_text.value, spec)
                            rule_text.set_value("")
                            proposal.clear()
                            rules_list.refresh()
                            ui.notify("Règle enregistrée : elle s'applique aux nouveaux mails.", type="positive")
                        ui.button("Enregistrer cette règle", icon="check", on_click=keep).props("unelevated no-caps dense color=primary")
                        ui.button("Annuler", on_click=proposal.clear).props("flat no-caps dense")

        ui.button("✦ Interpréter la règle", on_click=rule_interpret).props("outline no-caps color=primary")

    ews_rules = {k: c.label for k, c in cfgs.items() if c.kind == "ews" and c.password}
    if ews_rules:
        ui.separator().classes("q-mt-md")
        ui.label("Règles définies sur le serveur Exchange").classes("text-h6").style("font-weight:700")
        ui.label("Ce sont les règles d'Outlook, appliquées par le serveur même quand l'appli est fermée. Elles s'affichent ici en lecture seule : "
                 "pour les modifier, utilisez Outlook.").classes("text-caption text-grey")
        srv = ui.column().classes("w-full gap-2")

        async def srv_load(acct: str) -> None:
            srv.clear()
            with srv:
                ui.spinner(size="20px")
            try:
                rows = await run.io_bound(sync.exchange_rules, acct)
            except Exception as e:
                srv.clear()
                with srv:
                    ui.label(f"Lecture impossible : {e}").classes("text-negative")
                return
            srv.clear()
            with srv:
                if not rows:
                    ui.label("Aucune règle sur ce serveur.").classes("rmeta")
                for r in rows:
                    with ui.card().classes("w-full").props("flat bordered"):
                        with ui.row().classes("items-center no-wrap gap-2 w-full"):
                            ui.label(r["name"]).style("font-weight:600").classes("col")
                            ui.label("active" if r["enabled"] else "désactivée").classes("badge" + ("" if r["enabled"] else " p3"))
                        if r["if"] or r["then"]:
                            txt = "Si " + (" ET ".join(r["if"]) or "tout message") + " → " + (", ".join(r["then"]) or "aucune action") + "."
                            if r["except"]:
                                txt += " Sauf si " + " OU ".join(r["except"]) + "."
                            ui.label(txt).classes("text-body2")
                        if r["unsupported"]:
                            ui.label("Cette règle n'est pas entièrement lisible depuis l'appli ou comporte une erreur : vérifiez-la dans Outlook.").classes("rmeta")

        with ui.row().classes("items-center gap-2"):
            if len(ews_rules) > 1:
                srv_acct = ui.select(ews_rules, value=next(iter(ews_rules)), label="Boîte").props("outlined dense stack-label")
                ui.button("Afficher", icon="visibility", on_click=lambda: srv_load(srv_acct.value)).props("outline no-caps dense color=primary")
            else:
                ui.button("Afficher les règles du serveur", icon="visibility", on_click=lambda: srv_load(next(iter(ews_rules)))).props(
                    "outline no-caps dense color=primary")


def _tab_abs(cfgs, host, tabs) -> None:
    """Onglet « Absence (Exchange) »."""
    ui.label("Réponse automatique d'absence").classes("text-h6").style("font-weight:700")
    ews = {k: c.label for k, c in cfgs.items() if c.kind == "ews" and c.password}
    if not ews:
        ui.label("Disponible pour les boîtes Exchange configurées.").classes("text-caption text-grey")
    else:
        oof = {"acct": next(iter(ews)), "state": "Disabled", "audience": "All", "start": "", "end": "", "internal": "", "external": "", "loaded": False}
        ui.label("Ces réglages sont ceux du serveur Exchange : ils s'appliquent aussi dans Outlook. Ils s'enregistrent avec leur propre bouton.").classes("text-caption text-grey")
        sel_acct = ui.select(ews, value=oof["acct"], label="Boîte").props("outlined dense stack-label").classes("w-full").bind_value(oof, "acct")
        ui.select({"Disabled": "Désactivée", "Enabled": "Activée maintenant", "Scheduled": "Programmée (de … à …)"}, label="Réponse d'absence"
                       ).props("outlined dense stack-label").classes("w-full").bind_value(oof, "state")
        with ui.row().classes("w-full no-wrap gap-2"):
            ui.input("Du").props("outlined dense stack-label type=datetime-local").classes("col").bind_value(oof, "start")
            ui.input("Au").props("outlined dense stack-label type=datetime-local").classes("col").bind_value(oof, "end")
        ui.select({"All": "Tous les expéditeurs", "Known": "Mes contacts seulement", "None": "Mon organisation seulement"},
                  label="Répondre aux expéditeurs externes").props("outlined dense stack-label").classes("w-full").bind_value(oof, "audience")
        ui.textarea("Message pour les collègues (interne)").props("outlined autogrow stack-label").classes("w-full").bind_value(oof, "internal")
        ui.textarea("Message pour les personnes extérieures").props("outlined autogrow stack-label").classes("w-full").bind_value(oof, "external")
        oof_msg = ui.label().classes("text-body2")

        async def oof_load() -> None:
            oof_msg.set_text("Lecture sur le serveur…")
            try:
                r = await run.io_bound(sync.oof_get, oof["acct"])
            except Exception as e:
                return oof_msg.set_text(f"Lecture impossible : {friendly_error(e)}")
            plain = lambda h: re.sub(r"\n{3,}", "\n\n", html_to_text(h or "")).strip()
            oof.update(state=r["state"], audience=r["audience"], start=r["start"], end=r["end"],
                       internal=plain(r["internal"]), external=plain(r["external"]), loaded=True)
            oof_msg.set_text("")

        async def oof_save() -> None:
            if not oof["loaded"]:
                return ui.notify("Lisez d'abord les réglages actuels du serveur.", type="warning")
            if oof["state"] != "Disabled":
                if not (oof["internal"].strip() and oof["external"].strip()):
                    return ui.notify("Les deux messages (interne et externe) sont nécessaires.", type="warning")
                with host, ui.dialog() as cd, ui.card().style("width:480px;max-width:96vw"):
                    ui.label("Activer la réponse d'absence ?").classes("text-h6")
                    ui.label("Les personnes qui vous écrivent recevront cette réponse automatique, depuis le serveur, même quand l'application est fermée.").classes("text-body2")
                    with ui.row().classes("justify-end w-full gap-2"):
                        ui.button("Annuler", on_click=lambda: cd.submit(False)).props("flat no-caps")
                        ui.button("Enregistrer", on_click=lambda: cd.submit(True)).props("unelevated no-caps color=primary")
                if not await cd:
                    return
            html_of = lambda t: "<body>" + html.escape(t).replace("\n", "<br>") + "</body>" if t.strip() else ""
            try:
                await run.io_bound(lambda: sync.oof_set(
                    oof["acct"], state=oof["state"], audience=oof["audience"],
                    start=datetime.fromisoformat(oof["start"]) if oof["start"] else None,
                    end=datetime.fromisoformat(oof["end"]) if oof["end"] else None,
                    internal=html_of(oof["internal"]), external=html_of(oof["external"])))
            except Exception as e:
                return ui.notify(f"Enregistrement impossible : {friendly_error(e)}", type="negative", multi_line=True)
            ui.notify("Réponse d'absence enregistrée sur le serveur", type="positive")

        with ui.row().classes("gap-2"):
            ui.button("Relire depuis le serveur", icon="sync", on_click=oof_load).props("outline no-caps dense")
            ui.button("Enregistrer l'absence", icon="save", on_click=oof_save).props("unelevated no-caps dense color=primary")
        sel_acct.on_value_change(lambda e: oof_load())
        tabs.on_value_change(lambda e: oof_load() if (e.value == "abs" and not oof["loaded"]) else None)


def _tab_cal() -> None:
    """Onglet « Agenda »."""
    ui.label("Agenda").classes("text-h6").style("font-weight:700")
    ui.label("Où l'appli ajoute vos rendez-vous (bouton « agenda » d'un message, invitations acceptées).").classes("text-caption text-grey")
    cal_state = {"backend": config.calendar_backend(), "cid": config.env("OAUTH_GOOGLE_CLIENT_ID"), "csec": "", "busy": False}

    def set_backend(e) -> None:
        config.write_env({"CALENDAR_BACKEND": e.value})
        ui.notify("Agenda : " + ("Google Agenda" if e.value == "google" else "Exchange"), type="positive")
        google_box.set_visibility(e.value == "google")

    ui.toggle({"exchange": "Exchange", "google": "Google Agenda"}, value=cal_state["backend"], on_change=set_backend).props("no-caps unelevated")
    ui.label("Exchange : l'agenda de votre boîte Exchange (nécessite un compte Exchange). Google Agenda : fonctionne avec n'importe quelle boîte mail.").classes("text-caption text-grey")
    google_box = ui.column().classes("w-full gap-2 q-mt-sm")
    google_box.set_visibility(cal_state["backend"] == "google")
    with google_box:
        status = ui.label().style("font-weight:600")

        def refresh_status() -> None:
            status.set_text("✓ Google Agenda est connecté." if gcal.connected() else "Google Agenda n'est pas connecté.")

        refresh_status()
        ui.input("Identifiant client Google (ID client OAuth)").bind_value(cal_state, "cid").props("outlined dense stack-label").classes("w-full")
        ui.input("Secret client Google", password=True, password_toggle_button=True,
                 placeholder=state(bool(config.env("OAUTH_GOOGLE_CLIENT_SECRET")))).bind_value(cal_state, "csec").props("outlined dense stack-label").classes("w-full")

        async def connect_google() -> None:
            if cal_state["busy"]:
                return
            cid = (cal_state["cid"] or "").strip()
            if not cid or not (cal_state["csec"].strip() or config.env("OAUTH_GOOGLE_CLIENT_SECRET")):
                return ui.notify("Renseignez l'ID client et le secret client Google (voir l'aide ci-dessous).", type="warning")
            config.write_env({"OAUTH_GOOGLE_CLIENT_ID": cid, **({"OAUTH_GOOGLE_CLIENT_SECRET": cal_state["csec"].strip()} if cal_state["csec"].strip() else {})})
            cal_state["busy"] = True
            ui.notify("Le navigateur s'ouvre : autorisez l'accès à votre agenda, puis revenez ici.", multi_line=True)
            try:
                await run.io_bound(gcal.connect)
            except Exception as e:
                ui.notify(f"Connexion impossible : {e}", type="negative", multi_line=True)
            else:
                ok, msg = await run.io_bound(gcal.test)
                ui.notify(msg, type="positive" if ok else "negative", multi_line=True)
            finally:
                cal_state["busy"] = False
                cal_state["csec"] = ""
                refresh_status()

        async def test_google() -> None:
            ok, msg = await run.io_bound(gcal.test)
            ui.notify(msg, type="positive" if ok else "negative", multi_line=True)

        def disconnect_google() -> None:
            gcal.disconnect()
            refresh_status()
            ui.notify("Google Agenda déconnecté.", type="positive")

        with ui.row().classes("gap-2"):
            ui.button("Connecter Google Agenda", icon="link", on_click=connect_google).props("unelevated no-caps color=primary")
            ui.button("Tester", icon="bolt", on_click=test_google).props("outline no-caps")
            ui.button("Déconnecter", icon="link_off", on_click=disconnect_google).props("flat no-caps")
        ui.label("Comment obtenir l'ID client (une fois, gratuit) : console.cloud.google.com › créer un projet › « API et services » › "
                 "activer « Google Calendar API » › « Écran de consentement OAuth » (type externe, vous comme utilisateur test) › « Identifiants » › "
                 "créer un « ID client OAuth » de type « Application de bureau ». Copiez l'ID et le secret ici. Tant que l'écran de consentement est en "
                 "mode « test », Google coupe la connexion après 7 jours : il suffit de reconnecter. La permission demandée est limitée aux "
                 "rendez-vous (« calendar.events »).").classes("text-caption text-grey")
        ui.button("Ouvrir la console Google Cloud", icon="open_in_new",
                  on_click=lambda: ui.navigate.to("https://console.cloud.google.com/apis/credentials", new_tab=True)).props("flat no-caps dense size=sm")


def _tab_use(host) -> None:
    """Onglet « Utilisation et données »."""
    ui.label("Utilisation et données").classes("text-h6").style("font-weight:700")
    settings_ui.usage_panel()
    settings_ui.send_stats_panel()
    ui.label(f"Données : {config.DATA_DIR}").classes("text-caption text-grey")
    update_panel()

    async def backup_now() -> None:
        try:
            path = await run.io_bound(db.backup_now)
        except Exception as e:
            return ui.notify(f"Sauvegarde impossible : {e}", type="negative", multi_line=True)
        ui.notify(f"Copie de la base enregistrée : {Path(path).name}", type="positive")
        reveal(Path(path))

    with ui.row().classes("items-center gap-3"):
        ui.switch("Copie automatique quotidienne de la base (5 dernières gardées)", value=db.kv_get("db_backup") != "0",
                  on_change=lambda e: db.kv_set("db_backup", "1" if e.value else "0"))
        ui.button("Sauvegarder maintenant", icon="save_alt", on_click=backup_now).props("outline no-caps dense")
    def copy_diag() -> None:
        ui.clipboard.write(logs.diagnostic())
        ui.notify("Diagnostic copié (adresses et clés masquées) : collez-le dans votre message d'assistance.", type="positive", multi_line=True)

    with ui.row().classes("items-center gap-3"):
        ui.button("Copier le diagnostic", icon="bug_report", on_click=copy_diag).props("outline no-caps dense").tooltip(
            "Version, système, réglages généraux et dernières lignes du journal, sans aucun contenu de mail")
        ui.button("Ouvrir le dossier du journal", icon="folder_open", on_click=lambda: reveal(logs.log_dir() / "mailia.log")).props("outline no-caps dense")
        ui.button("Ouvrir le dossier des données", icon="folder_special", on_click=lambda: open_folder(config.DATA_DIR)).props("outline no-caps dense").mark(
            "open-data").tooltip(str(config.DATA_DIR))

    async def measure() -> None:
        ui.notify("Mesure en cours (quelques secondes)…")
        bench = await run.io_bound(perf.benchmark)
        try:
            browser = await ui.run_javascript("navigator.userAgent", timeout=5)
        except Exception:
            browser = ""
        text = logs.scrub(perf.report(bench, browser))
        try:
            (logs.log_dir() / "performance.txt").write_text(text, encoding="utf-8")
        except OSError:
            pass
        with host, ui.dialog() as pd, ui.card().style("width:min(820px,96vw)"):
            ui.label("Performances").classes("text-h6")
            ui.label("Rapport sans aucun contenu de mail. Il est aussi enregistré dans le dossier du journal (performance.txt).").classes("text-caption text-grey")
            ui.html("<pre style='margin:0;font-size:12px;white-space:pre-wrap;max-height:56vh;overflow:auto'>" + html.escape(text) + "</pre>")
            with ui.row().classes("justify-end w-full gap-2"):
                ui.button("Copier", icon="content_copy", on_click=lambda: (ui.clipboard.write(text), ui.notify("Rapport copié.", type="positive"))).props("outline no-caps")
                ui.button("Fermer", on_click=pd.close).props("flat no-caps")
        pd.open()

    with ui.row().classes("items-center gap-3"):
        ui.button("Mesurer les performances", icon="speed", on_click=measure).props("outline no-caps dense").mark("perf-measure").tooltip(
            "Chronomètre quelques opérations de cet ordinateur et affiche où le temps est perdu (démarrage, base de données, disque, antivirus…)")
    ui.label("Les copies sont dans le dossier « backups » des données. Pour restaurer : fermez l'appli et remplacez le fichier mail.db par une copie.").classes("text-caption text-grey")
    if SERVER_MODE:
        ui.label(f"Jeton d'accès mobile : {config.ensure('APP_TOKEN')}").classes("text-caption")

    from mailia import backup
    ui.label("Sauvegarde de vos données").classes("text-subtitle2 q-mt-lg")
    ui.label("Réglages, signatures, modèles, contacts favoris, groupes, tâches et règles de tri : un fichier à garder en lieu sûr. "
             "Vos mots de passe et votre clé d'API n'y sont jamais. Vos mails restent sur vos serveurs.").classes("text-caption text-grey")

    async def do_export() -> None:
        raw = backup.dumps(await run.io_bound(backup.export_data))
        name = f"velora-sauvegarde-{datetime.now():%Y-%m-%d}.json"
        if SERVER_MODE:
            ui.download.content(raw, name)
            return
        folder = Path.home() / "Downloads"
        folder.mkdir(exist_ok=True)
        path = folder / name
        path.write_bytes(raw)
        ui.notify(f"Sauvegarde enregistrée dans Téléchargements : {name}", type="positive")
        reveal(path)

    async def do_import(e) -> None:
        try:
            data = json.loads((await e.file.read()).decode("utf-8"))
            summary = backup.summary(data)
            if data.get("app") not in ("Velora", "Mail IA"):
                raise ValueError("Ce fichier n'est pas une sauvegarde de Velora.")
        except Exception as err:
            e.sender.reset()
            return ui.notify(f"Fichier illisible : {err}", type="negative")
        e.sender.reset()
        with host, ui.dialog() as cd, ui.card().style("width:520px;max-width:96vw"):
            ui.label("Restaurer cette sauvegarde ?").classes("text-h6")
            ui.label(summary).classes("text-body2")
            ui.label("Elle est fusionnée avec vos données actuelles : rien n'est supprimé, ce qui existe déjà est conservé.").classes("text-caption text-grey")
            with ui.row().classes("justify-end w-full gap-2"):
                ui.button("Annuler", on_click=lambda: cd.submit(False)).props("flat no-caps")
                ui.button("Restaurer", on_click=lambda: cd.submit(True)).props("unelevated no-caps color=primary")
        if await cd:
            res = await run.io_bound(backup.import_data, data)
            sync.reload()
            ui.notify("Restauré : " + ", ".join(f"{v} {k}" for k, v in res.items() if v) or "rien de nouveau.", type="positive", multi_line=True)

    up = ui.upload(on_upload=do_import, auto_upload=True).props("hide-upload-btn accept=.json").style(
        "position:absolute;left:-9999px;width:1px;height:1px;overflow:hidden")
    with ui.row().classes("gap-2"):
        ui.button("Exporter mes données", icon="download", on_click=do_export).props("outline no-caps dense")
        ui.button("Importer une sauvegarde…", icon="upload").props("outline no-caps dense").on(
            "click", js_handler=f"() => getElement({up.id}).$refs.qRef.pickFiles()")

    ui.label("Mots de passe des boîtes").classes("text-subtitle2 q-mt-lg")
    ui.label("Par défaut, ils sont dans un fichier de configuration réservé à votre session. Vous pouvez les confier au Trousseau de votre système "
             "(macOS : Trousseau d'accès ; Windows : Gestionnaire d'identifiants). Après une mise à jour de l'appli, macOS peut demander "
             "de réautoriser l'accès : choisissez « Toujours autoriser ».").classes("text-caption text-grey")

    async def keychain_toggle(e) -> None:
        moved, errors = await run.io_bound(config.use_keychain, bool(e.value))
        if errors:
            ui.notify("Trousseau : " + " ; ".join(errors), type="negative", multi_line=True)
        else:
            ui.notify(f"{moved} mot(s) de passe déplacé(s) vers " + ("le Trousseau." if e.value else "le fichier de configuration."), type="positive")
        sync.reload()

    ui.switch("Stocker mes mots de passe et ma clé API dans le Trousseau", value=config.keychain_enabled(), on_change=keychain_toggle)


def open_settings(host, on_folders) -> None:
    """Dossiers, IA (interrupteur, modèles, limites), clé API et mots de passe : écrits dans le .env de l'utilisateur."""
    cfgs = sync.ACCOUNTS
    f = {"ANTHROPIC_API_KEY": "", "ONEMIN_API_KEY": "", "AI_ENABLED": config.ai_switch(),
         "AI_PROVIDER": config.ai_provider(), "AI_FALLBACK": (db.kv_get("ai_fallback") or "1") == "1",
         **{f"MODEL_{t}": config.model(t, "anthropic") for t in config.TASKS},
         **{f"MODEL1_{t}": config.model(t, "onemin")[len(config.ONEMIN_PREFIX):] for t in config.TASKS},
         **{f"PROVIDER_{t}": (config.env(f"PROVIDER_{t}") if config.env(f"PROVIDER_{t}") in config.PROVIDERS else "default") for t in config.TASKS},
         "AI_UNREAD_ONLY": config.ai_unread_only(), "AI_MAX_AGE_DAYS": config.ai_max_age_days(),
         "CONVERSATIONS": config.conversations_enabled(), "SIG_ON": compose.signature_on(), "SIG_HTML": compose.signature_html(),
         **{f"P_{k}": ai.prompt_text(k) for k in ai.DEFAULTS}, "TEST_MAIL": None}

    with host, ui.dialog() as dlg, ui.card().classes("no-wrap q-pa-none").style(
            "width:min(1280px,96vw);max-width:96vw;height:min(90vh,840px)"):
        with ui.row().classes("w-full items-center q-px-lg q-py-md"):
            ui.icon("settings", size="26px").style("color:var(--accent)")
            ui.label("Paramètres").classes("text-h6").style("font-weight:700")
        with ui.row().classes("w-full col no-wrap").style("min-height:0;border-top:1px solid var(--line);border-bottom:1px solid var(--line)"):
            with ui.tabs().props("vertical no-caps align=left inline-label").classes("settings-tabs").style("width:230px;flex-shrink:0;background:var(--side)") as tabs:
                ui.tab("boites", label="Boîtes mail", icon="alternate_email")
                ui.tab("aff", label="Affichage et signature", icon="view_quilt")
                ui.tab("dos", label="Dossiers", icon="folder_open")
                ui.tab("ia", label="Intelligence artificielle", icon="auto_awesome")
                ui.tab("prm", label="Consignes de l'IA", icon="tune")
                ui.tab("rules", label="Règles de tri", icon="rule")
                ui.tab("abs", label="Absence (Exchange)", icon="beach_access")
                ui.tab("cal", label="Agenda", icon="event")
                ui.tab("auto", label="Automatismes", icon="bolt")
                ui.tab("priv", label="Confidentialité et sécurité", icon="lock")
                ui.tab("api", label="API et assistants", icon="hub")
                ui.tab("use", label="Utilisation et données", icon="insights")
                if dictation.usable():
                    ui.tab("dict", label="Dictée vocale", icon="mic")
                ui.tab("about", label="À propos", icon="info")
            with ui.tab_panels(tabs, value="boites").props("vertical").classes("col").style("min-width:0;height:100%;background:transparent"):
                sec = {k: ui.tab_panel(k).classes("q-pa-lg").style("overflow-y:auto") for k in ("boites", "aff", "dos", "ia", "prm", "rules", "abs", "cal", "auto", "priv", "api", "use", "about", *(("dict",) if dictation.usable() else ()))}
        if "dict" in sec:
            with sec["dict"], ui.column().classes("w-full gap-2").style("max-width:700px"):
                _tab_dictation(host)
        with sec["about"], ui.column().classes("w-full gap-1").style("max-width:700px"):
            _tab_about()
        with sec["api"], ui.column().classes("w-full gap-1").style("max-width:760px"):
            _tab_api(host)

        with sec["auto"], ui.column().classes("w-full gap-1").style("max-width:700px"):
            _tab_auto()
        with sec["priv"], ui.column().classes("w-full gap-1").style("max-width:700px"):
            _tab_priv(host)

        with sec["boites"], ui.column().classes("w-full gap-2").style("max-width:700px"):
            _tab_boites(dlg, host)

        with sec["aff"], ui.column().classes("w-full gap-2").style("max-width:none"):
            _tab_aff(f)

        with sec["dos"], ui.column().classes("w-full gap-2").style("max-width:700px"):
            _tab_dos(cfgs, dlg, on_folders)

        with sec["ia"], ui.column().classes("w-full gap-2").style("max-width:700px"):
            _tab_ia(f)

        with sec["prm"], ui.column().classes("w-full gap-2").style("max-width:700px"):
            _tab_prm(f, host)

        with sec["rules"], ui.column().classes("w-full gap-2").style("max-width:760px"):
            _tab_rules(cfgs, host)

        with sec["abs"], ui.column().classes("w-full gap-2").style("max-width:700px"):
            _tab_abs(cfgs, host, tabs)

        with sec["cal"], ui.column().classes("w-full gap-2").style("max-width:700px"):
            _tab_cal()

        with sec["use"], ui.column().classes("w-full gap-2").style("max-width:700px"):
            _tab_use(host)

        def save() -> None:
            db.kv_set("ai_fallback", "1" if f["AI_FALLBACK"] else "0")
            config.write_env({**{k: v for k, v in f.items() if k in ("ANTHROPIC_API_KEY", "ONEMIN_API_KEY", "AI_PROVIDER")
                                 or k.startswith(("MODEL_", "MODEL1_", "PROVIDER_"))},
                              "AI_ENABLED": "1" if f["AI_ENABLED"] else "0",
                              "AI_UNREAD_ONLY": "1" if f["AI_UNREAD_ONLY"] else "0",
                              "AI_MAX_AGE_DAYS": str(int(f["AI_MAX_AGE_DAYS"] or 0)),
                              "CONVERSATIONS": "1" if f["CONVERSATIONS"] else "0",
                              })
            db.kv_set("signature_on", "1" if f["SIG_ON"] else "0")
            db.kv_set("signature_html", htmlmail.sanitize_outgoing(f["SIG_HTML"] or ""))
            brief_changed = False
            for key in ai.DEFAULTS:  # une consigne identique à celle d'origine n'est pas stockée
                text = (f[f"P_{key}"] or "").strip()[:4000]
                stored = "" if text == ai.default_text(key).strip() else text
                if (db.kv_get(f"prompt:{key}") or "") != stored:
                    db.kv_set(f"prompt:{key}", stored)
                    brief_changed = brief_changed or key in ("brief", "rules")
            if brief_changed:
                db.kv_set("brief:sig", "")  # le brief est réécrit avec les nouvelles consignes
            sync.reload()
            dlg.close()
            ui.notify("Paramètres enregistrés", type="positive")
            ui.navigate.reload()  # l'affichage dépend de l'IA et des dossiers choisis

        with ui.row().classes("justify-end w-full q-px-lg q-py-sm gap-2"):
            ui.button("Annuler", on_click=dlg.close).props("flat no-caps")
            ui.button("Enregistrer", on_click=save).props("unelevated no-caps color=primary").mark("settings-save")
    dlg.open()
