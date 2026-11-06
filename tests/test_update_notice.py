"""La notification « une mise à jour est disponible » doit pouvoir être envoyée à la fenêtre (une fonction Python dans ses actions faisait échouer l'envoi)."""
import json

from nicegui import ui

import main
from mailia import ui_update, updater


async def test_the_update_notice_is_serialisable_and_opens_the_offer(user, monkeypatch) -> None:
    info = {"version": "9.9.9", "notes": "Nouveautés", "url": "x", "digest": "", "sig": ""}
    monkeypatch.setattr(updater, "check", lambda force=False: info)
    offered = []
    monkeypatch.setattr(ui_update, "offer", lambda i: offered.append(i["version"]))
    seen = []
    real = ui.notification

    def spy(*a, **k):
        n = real(*a, **k)
        seen.append(n)
        return n
    monkeypatch.setattr(ui_update.ui, "notification", spy)
    ui.page("/")(main.index_page)
    await user.open("/")
    with user:
        await ui_update.auto_check()
    assert seen, "aucune notification créée"
    json.dumps({k: v for k, v in seen[0]._props.items()}, default=str)  # aucun objet Python non sérialisable dans les propriétés
    assert not any(callable(v) for v in seen[0]._props.values())
