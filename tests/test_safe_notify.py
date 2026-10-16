"""Un message à l'utilisateur ne doit jamais échouer parce que la ligne d'où part l'action a été redessinée entre-temps."""
import asyncio
import gc

import pytest

from nicegui import ui
from nicegui.testing import User

from mailia import ui_common  # noqa: F401  (installe la version sûre de ui.notify)


async def test_notify_survives_a_deleted_origin(user: User) -> None:
    holder = {}

    @ui.page("/n")
    def page():
        holder["row"] = ui.element("div")
        ui.label("page")

    await user.open("/n")
    row = holder.pop("row")
    slot = row.default_slot  # le gestionnaire du clic garde le contexte (la « fente ») de sa ligne
    row.delete()  # la synchro a redessiné la ligne pendant l'action : l'élément n'existe plus
    del row
    gc.collect()
    await asyncio.sleep(0.1)
    with pytest.raises(RuntimeError, match="has been deleted"):  # c'est l'erreur du journal : sans protection, notify échoue dans ce contexte
        with slot:
            ui_common._real_notify("x")
    with slot:
        ui_common.safe_notify("Conversation archivée", type="positive")  # avec : le message part quand même, via la page encore connectée
    await user.should_see("page")
    user.notify.contains("Conversation archivée")


async def test_notify_still_works_normally(user: User) -> None:
    @ui.page("/m")
    def page():
        ui.button("ok", on_click=lambda: ui.notify("Fait"))

    await user.open("/m")
    user.find("ok").click()
    user.notify.contains("Fait")


def test_the_app_replaces_ui_notify_with_the_safe_version():
    import subprocess
    import sys
    out = subprocess.run([sys.executable, "-c", "from nicegui import ui; import mailia.ui_common as u; print(ui.notify is u.safe_notify)"],
                         capture_output=True, text=True, check=True).stdout.strip()
    assert out == "True"  # dans l'appli, tous les ui.notify passent par la version qui survit à une ligne supprimée
