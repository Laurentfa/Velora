"""Mes modèles de signature : créer, modifier, dupliquer et supprimer (Paramètres › Affichage et signature, et menu « Modèle de signature » de la rédaction)."""
from nicegui import ui

from mailia import compose, htmlmail
from mailia.ui_common import make_editor


def signature_models_dialog(host=None, on_change=None) -> None:
    """Fenêtre de gestion. host : élément sous lequel l'ouvrir (la rédaction) ; on_change : appelée après chaque modification."""
    with (host if host is not None else ui.element()), ui.dialog() as dlg, ui.card().style("width:760px;max-width:96vw"):
        ui.label("Mes modèles de signature").classes("text-h6")
        ui.label("Variable utilisable : {{nom}} (votre nom, réglé plus haut). Les modèles fournis avec Velora ne se modifient pas : dupliquez-en un pour le personnaliser.").classes("text-caption text-grey")

        @ui.refreshable
        def listing() -> None:
            for key, (name, _) in compose.SIGNATURE_MODELS.items():
                with ui.row().classes("w-full items-center no-wrap gap-2").style("border:1px solid var(--line);border-radius:12px;padding:6px 10px"):
                    ui.label(name).classes("col").style("font-weight:600")
                    ui.label("fourni").classes("rmeta")
                    ui.button(icon="content_copy", on_click=lambda _, k=key: duplicate(k)).props("flat dense round").tooltip("Dupliquer pour le modifier").mark(f"sigm-dup-{key}")
            mine = compose.user_models()
            if not mine:
                ui.label("Vous n'avez pas encore créé de modèle.").classes("text-caption text-grey")
            for key, (name, html) in mine.items():
                with ui.row().classes("w-full items-center no-wrap gap-2").style("border:1px solid var(--line);border-radius:12px;padding:6px 10px"):
                    ui.label(name).classes("col").style("font-weight:600")
                    ui.button(icon="edit", on_click=lambda _, k=key: edit(k)).props("flat dense round").tooltip("Modifier").mark(f"sigm-edit-{key}")
                    ui.button(icon="delete_outline", on_click=lambda _, k=key: drop(k)).props("flat dense round color=negative").tooltip("Supprimer").mark(f"sigm-del-{key}")

        def changed() -> None:
            listing.refresh()
            if on_change:
                on_change()

        def drop(key: str) -> None:
            compose.delete_user_model(key)
            changed()

        def duplicate(key: str) -> None:
            name, _ = compose.all_models()[key]
            compose.save_user_model(None, f"{name} (copie)", compose.model_html(key))
            changed()

        async def edit(key: str | None) -> None:
            cur = compose.user_models().get(key) if key else None
            with ui.dialog() as ed, ui.card().style("width:760px;max-width:96vw"):
                ui.label("Modifier le modèle" if cur else "Nouveau modèle").classes("text-h6")
                name = ui.input("Nom du modèle", value=cur[0] if cur else "").props("outlined dense stack-label").classes("w-full").mark("sigm-name")
                box = {"html": cur[1] if cur else "<p>{{nom}}</p>"}
                make_editor(box["html"], lambda e: box.__setitem__("html", e.value or ""), "160px")
                with ui.row().classes("justify-end w-full"):
                    ui.button("Annuler", on_click=lambda: ed.submit(False)).props("flat no-caps")
                    ui.button("Enregistrer", on_click=lambda: ed.submit(True)).props("unelevated no-caps color=primary").mark("sigm-save")
            if await ed and name.value.strip():
                compose.save_user_model(key, name.value, htmlmail.sanitize_outgoing(box["html"]))
                changed()

        listing()
        with ui.row().classes("justify-between w-full"):
            ui.button("Nouveau modèle", icon="add", on_click=lambda: edit(None)).props("outline no-caps dense").mark("sigm-new")
            ui.button("Fermer", on_click=lambda: dlg.submit(None)).props("flat no-caps")
    dlg.open()
