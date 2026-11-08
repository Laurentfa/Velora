"""Interface du partage des analyses IA entre ordinateurs (voir aishare) : explication complète, interrupteur par boîte, avis affiché une fois."""
from nicegui import ui

from mailia import aishare, config, db

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
