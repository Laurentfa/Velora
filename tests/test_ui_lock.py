"""Écran de verrouillage : parcours réel dans l'interface (saisie du code, déverrouillage, mauvais code)."""
import pytest
from nicegui import ui
from nicegui.testing import User

from mailia import db, vault
from mailia.ui_lock import render_lock_screen, session_unlocked


@pytest.fixture
def locked_page():
    vault.set_code(db, "4321")

    @ui.page("/lock")
    def page() -> None:
        if session_unlocked():
            ui.label("Boîte ouverte")
        else:
            render_lock_screen()


async def test_good_code_unlocks_the_session(user: User, locked_page) -> None:
    await user.open("/lock")
    await user.should_see("Application verrouillée")
    user.find(ui.input).type("4321").trigger("keydown.enter")
    await user.should_see("Boîte ouverte")


async def test_wrong_code_stays_locked(user: User, locked_page) -> None:
    await user.open("/lock")
    user.find(ui.input).type("0000").trigger("keydown.enter")
    await user.should_see("Code incorrect.")
    await user.should_not_see("Boîte ouverte")
