"""Panneau « API et assistants » : la fenêtre de nouvelle clé s'ouvre et le changement de profil met à jour les droits (régression « scope_box »)."""
from nicegui import ui
from nicegui.testing import User

from mailia import apikeys
from mailia.api_ui import api_panel


async def test_new_key_dialog_and_profile_change(user: User) -> None:
    @ui.page("/api")
    def page() -> None:
        api_panel(ui.element("div"), lambda *_: None)

    await user.open("/api")
    user.find("Nouvelle clé").click()
    await user.should_see("Droits détaillés")
    profiles = list(apikeys.PRESETS)
    other = next(p for p in profiles if p != "Rédaction (brouillons à valider)")
    select = next(s for s in user.find(ui.select).elements if s.props.get("label") == "Profil")
    select.set_value(other)
    box = next(s for s in user.find(ui.select).elements if s.props.get("label") == "Droits détaillés")
    assert sorted(box.value) == sorted(apikeys.PRESETS[other])
