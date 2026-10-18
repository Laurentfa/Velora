"""Boutons Apple Intelligence de la rédaction (Mac) : corriger, reformuler, adoucir, raccourcir le texte, gratuitement et sur le Mac.

Disponibles même quand les fonctions IA de Velora sont désactivées ou sans clé : aucun texte ne quitte le Mac. Le résultat est montré à côté de l'original avant d'être appliqué."""
import json

from nicegui import run, ui

from mailia import apple_writer


class AppleWriterMixin:
    apple_busy = False

    def apple_tool(self) -> None:
        if not (apple_writer.enabled() and apple_writer.available()):
            return
        with ui.button("Apple Intelligence", icon="auto_fix_high").props("flat dense no-caps size=sm").tooltip(
                "Corriger ou reformuler le texte avec Apple Intelligence : gratuit, sur ce Mac, rien n'est envoyé. S'applique à la sélection, sinon à tout votre message.").mark("apple-tool"):
            with ui.menu():
                for key, (title, hint) in apple_writer.MODES.items():
                    with ui.menu_item(on_click=lambda _, k=key: self.apple_rewrite(k)).mark(f"apple-{key}"):
                        ui.label(title).style("font-weight:600")
                        ui.label(hint).classes("rmeta")

    async def apple_rewrite(self, mode: str) -> None:
        if self.apple_busy:
            return
        info = await ui.run_javascript(f"mailiaTarget({json.dumps(self.editor.html_id)})")
        text = ((info or {}).get("text") or "").strip()
        if not text:
            return ui.notify("Rien à traiter : écrivez ou sélectionnez du texte.", type="warning")
        scope = "la sélection" if info.get("kind") == "selection" else "votre message"
        self.apple_busy = True
        ui.notify(f"Apple Intelligence travaille sur {scope}…", timeout=2000)
        try:
            new = await run.io_bound(apple_writer.rewrite, mode, text)
        except apple_writer.WriterError as e:
            self.apple_busy = False
            return ui.notify(str(e), type="negative", multi_line=True)
        self.apple_busy = False
        if not new or new.strip() == text:
            return ui.notify("Rien à changer : le texte est déjà bon." if mode == "correct" else "Apple Intelligence n'a rien changé.")
        with self.host, ui.dialog() as dlg, ui.card().style("width:min(900px,94vw);max-width:94vw"):
            ui.label(f"{apple_writer.MODES[mode][0]} — proposition d'Apple Intelligence").classes("text-h6")
            ui.label("Relisez avant d'appliquer : le modèle d'Apple est petit et peut parfois changer le sens.").classes("rmeta")
            with ui.row().classes("w-full no-wrap gap-4"):
                for title, body, marker in (("Avant", text, "apple-before"), ("Après", new, "apple-after")):
                    with ui.column().classes("col gap-1"):
                        ui.label(title).style("font-weight:600")
                        ui.label(body).style("white-space:pre-wrap;max-height:50vh;overflow:auto;border:1px solid var(--line);border-radius:8px;padding:8px").mark(marker)
            with ui.row().classes("justify-end w-full gap-2"):
                ui.button("Annuler", on_click=lambda: dlg.submit(False)).props("flat no-caps").mark("apple-cancel")
                ui.button("Remplacer", on_click=lambda: dlg.submit(True)).props("unelevated no-caps color=primary").mark("apple-apply")
        ok = await dlg
        dlg.delete()
        if not ok:
            return
        done = await ui.run_javascript(f"mailiaApply({json.dumps(self.editor.html_id)}, {json.dumps(new)})")
        ui.notify("Texte remplacé (Ctrl/⌘ Z pour annuler)." if done else "Le texte a changé entre-temps : recommencez.", type="positive" if done else "warning")
