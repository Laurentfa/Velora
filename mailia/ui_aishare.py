"""Interface du partage des analyses IA entre ordinateurs (voir aishare) : explication complète, interrupteur par boîte, avis affiché une fois."""
from nicegui import ui

from mailia import aishare, config, db, settingsync

EXPLAIN = [
    ("Ce que ça fait",
     "Quand Velora analyse un mail (résumé, catégorie, priorité, « à répondre », action suggérée), elle enregistre ce résultat sur votre serveur Exchange, "
     "attaché à ce mail. Si vous utilisez Velora sur plusieurs ordinateurs (ou si plusieurs personnes lisent la même boîte), les autres reprennent ce résultat "
     "au lieu de relancer l'IA : le mail n'est analysé et payé qu'une seule fois."),
    ("Où c'est stocké",
     "Uniquement dans votre boîte Exchange, sur votre serveur, dans une propriété cachée du mail : Outlook ne l'affiche pas. Rien n'est envoyé à un tiers "
     "pour cela. Le résumé tient en une ou deux phrases ; il peut citer des noms (par exemple de patients) : il est donc aussi sensible que le mail lui-même, "
     "et il reste au même endroit que lui. Il disparaît avec le mail."),
    ("Qui peut le lire",
     "Les personnes qui ont accès à la boîte et utilisent Velora. Ce qui est partagé est le résultat brut de l'IA : vos corrections de catégorie et vos "
     "expéditeurs prioritaires restent propres à chaque ordinateur."),
    ("Quand une analyse est reprise",
     "Seulement si elle a été faite avec les mêmes consignes de tri, pour la même personne et dans la même langue. Sinon, Velora analyse le mail elle-même, "
     "comme avant. Si le serveur refuse l'écriture (boîte partagée en lecture seule, par exemple), Velora continue sans partager."),
    ("Le désactiver",
     "Un interrupteur par boîte, ci-dessous. Désactivé, plus rien n'est écrit ni repris. Les analyses déjà publiées restent attachées à leurs mails "
     "(invisibles) jusqu'à la suppression de ces mails."),
]


EXPLAIN_SYNC = [
    ("Pourquoi",
     "Pour que deux ordinateurs partagent leurs analyses, ils doivent avoir les mêmes consignes de tri, les mêmes règles personnelles et le même nom. Plutôt que de les "
     "recopier à la main, Velora les fait suivre d'un ordinateur à l'autre : une consigne modifiée sur l'un s'applique aussi sur l'autre, en quelques minutes."),
    ("Ce qui est synchronisé",
     "Les consignes de l'IA (tri et résumé, brief du jour, style des réponses, règles personnelles), votre nom et vos corrections de classement (« Toujours classer ainsi les "
     "mails de… », par adresse ou par domaine) : une correction faite sur un ordinateur s'applique aux autres, et une correction retirée est retirée partout. Rien d'autre : "
     "ni vos mots de passe, ni vos clés, ni vos budgets, ni vos signatures."),
    ("Où c'est stocké",
     "Dans un élément caché de la boîte de réception de votre première boîte Exchange avec IA (par adresse), sur votre serveur. Outlook ne l'affiche pas. Si vos consignes "
     "contiennent des informations sensibles, elles sont au même endroit que vos mails."),
    ("Si deux ordinateurs changent la même consigne",
     "La modification la plus récente l'emporte. Au tout premier contact, si une consigne personnalisée existe ici et une autre ailleurs, Velora n'écrase rien : "
     "elle vous demande laquelle garder."),
    ("Le désactiver",
     "L'interrupteur ci-dessous. Désactivé, chaque ordinateur garde ses propres consignes (et le partage des analyses ne marche alors qu'entre ordinateurs qui ont les mêmes)."),
]


def explanation() -> None:
    for title, text in EXPLAIN:
        ui.label(title).classes("text-caption text-weight-bold q-mt-xs")
        ui.label(text).classes("text-caption text-grey")


def settings() -> None:
    """Bloc de l'onglet « Intelligence artificielle »."""
    ui.label("Partager les analyses entre vos ordinateurs (Exchange)").classes("text-subtitle2 q-mt-md").mark("ai-share-title")
    ui.label("Activé par défaut : pas de double facture d'IA quand plusieurs ordinateurs lisent la même boîte.").classes("text-body2")
    accounts = [(k, c) for k, c in config.load_accounts(enabled_only=False).items() if c.kind == "ews" and c.ai_enabled]
    if accounts:
        for key, cfg in accounts:
            ui.switch(f"{cfg.label or cfg.email} : publier et reprendre les analyses", value=aishare.enabled(key),
                      on_change=lambda e, k=key: aishare.set_enabled(k, bool(e.value))).mark(f"ai-share-{key}")
    else:
        ui.label("Aucune boîte Exchange avec l'IA activée. (Les boîtes IMAP ne proposent pas encore cette fonction.)").classes("text-caption text-grey")
    st = aishare.stats()
    if st.get("reused") or st.get("published"):
        ui.label(f"Sur cet ordinateur : {st.get('reused', 0)} analyse(s) reprise(s) d'un autre ordinateur, {st.get('published', 0)} publiée(s).").classes("text-caption")
    with ui.expansion("Comment ça marche, où c'est stocké, qui peut le lire", icon="info").classes("w-full").props("dense"):
        explanation()
    follow_settings()


def follow_settings() -> None:
    """Bloc « Faire suivre mes consignes d'un ordinateur à l'autre » de l'onglet « Intelligence artificielle »."""
    ui.label("Faire suivre mes réglages d'IA d'un ordinateur à l'autre (Exchange)").classes("text-subtitle2 q-mt-md").mark("ssync-title")
    ui.label("Activé par défaut : vos consignes de l'IA, votre nom et vos corrections de classement sont les mêmes sur tous vos ordinateurs, sans rien recopier.").classes("text-body2")
    acc = settingsync.account()
    if not acc:
        ui.label("Aucune boîte Exchange avec l'IA activée : rien à synchroniser.").classes("text-caption text-grey")
    else:
        ui.switch("Synchroniser mes consignes de l'IA, mon nom et mes corrections de classement", value=db.kv_get("settingsync_on") != "0",
                  on_change=lambda e: db.kv_set("settingsync_on", "1" if e.value else "0")).mark("ssync-switch")
        at, err = db.kv_get("ssync:at"), db.kv_get("ssync:error")
        if err:
            ui.label(f"Dernière tentative en échec ({err}) : Velora réessaie toute seule.").classes("text-caption text-negative")
        elif at:
            ui.label(f"Dernière vérification de la boîte : {at[11:16]} (UTC).").classes("text-caption text-grey")
    for key, remote in settingsync.conflicts().items():
        with ui.card().classes("w-full q-pa-sm").style("border:1px solid var(--line)").mark(f"ssync-conflict-{key}"):
            ui.label(f"« {settingsync.LABELS[key]} » diffère de celle de vos autres ordinateurs.").classes("text-body2")
            ui.label("Choisissez laquelle garder : rien n'est écrasé tant que vous n'avez pas choisi.").classes("text-caption text-grey")
            with ui.row().classes("gap-2"):
                ui.button("Utiliser celle de la boîte", on_click=lambda k=key: (settingsync.resolve(k, True), ui.notify("Consigne de la boîte reprise.", type="positive"))
                          ).props("outline no-caps dense").mark(f"ssync-remote-{key}")
                ui.button("Garder celle de cet ordinateur", on_click=lambda k=key: (settingsync.resolve(k, False), ui.notify("Votre consigne sera envoyée aux autres ordinateurs.", type="positive"))
                          ).props("outline no-caps dense").mark(f"ssync-local-{key}")
    with ui.expansion("Comment ça marche, ce qui est synchronisé, où c'est stocké", icon="info").classes("w-full").props("dense"):
        for title, text in EXPLAIN_SYNC:
            ui.label(title).classes("text-caption text-weight-bold q-mt-xs")
            ui.label(text).classes("text-caption text-grey")


def notice_settings_once() -> None:
    """Une seule fois : prévient que les consignes de l'IA et le nom suivent désormais d'un ordinateur à l'autre."""
    if db.kv_get("ssync_notice") == "1" or not settingsync.account():
        return
    db.kv_set("ssync_notice", "1")
    ui.notification("Nouveau : vos consignes de l'IA et votre nom sont désormais les mêmes sur tous vos ordinateurs, par votre boîte Exchange (un élément caché, invisible dans "
                    "Outlook). Une consigne modifiée sur l'un s'applique sur l'autre. Détails et interrupteur : Paramètres › Intelligence artificielle.",
                    type="info", multi_line=True, timeout=0, close_button="Compris")


def notice_once() -> None:
    """Une seule fois, pour qui a une boîte Exchange avec l'IA : prévient que la fonction existe, qu'elle est active, et où la régler."""
    if db.kv_get("ai_share_notice") == "1" or not config.ai_enabled():
        return
    if not any(c.kind == "ews" and c.ai_enabled for c in config.load_accounts(enabled_only=False).values()):
        return
    db.kv_set("ai_share_notice", "1")
    ui.notification("Nouveau : Velora partage désormais ses analyses IA entre vos ordinateurs, par votre boîte Exchange. Un mail déjà analysé ailleurs n'est pas "
                    "payé deux fois. Le résultat est stocké sur votre serveur, attaché au mail, invisible dans Outlook. Réglage et détails : Paramètres › Intelligence "
                    "artificielle (un interrupteur par boîte).", type="info", multi_line=True, timeout=0, close_button="Compris")
