"""Une même fenêtre ne s'ouvre jamais deux fois (clics répétés sur une icône)."""
from nicegui import ui
from nicegui.testing import User

from mailia import nicegui_tweaks

nicegui_tweaks.install()


answers: list = []


async def test_repeated_clicks_open_the_window_once(user: User) -> None:
    @ui.page("/once")
    def page() -> None:
        def show() -> None:
            with ui.dialog() as d, ui.card():
                ui.label("Fenêtre A")
            d.open()

        async def ask() -> None:
            with ui.dialog() as d, ui.card():
                ui.label("Question B")
                ui.button("Oui", on_click=lambda: d.submit(True)).mark("yes")
            answers.append(await d)
        ui.button("ouvrir", on_click=show).mark("show")
        ui.button("demander", on_click=ask).mark("ask")

    await user.open("/once")
    for _ in range(3):
        user.find(marker="show").click()
    assert len(user.find(content="Fenêtre A").elements) == 1
    for _ in range(3):
        user.find(marker="ask").click()
    await user.should_see("Question B")
    assert len(user.find(content="Question B").elements) == 1
    user.find(marker="yes").click()
    from tests.conftest import wait_until
    await wait_until(lambda: len(answers) == 3)
    assert answers == [None, None, True]  # les doublons rendent None (comme une annulation), la vraie fenêtre rend sa réponse
