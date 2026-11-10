"""La notification « une mise à jour est disponible » doit pouvoir être envoyée à la fenêtre (une fonction Python dans ses actions faisait échouer l'envoi)."""
import json

from nicegui import ui

import main
from mailia import ui_update, updater


async def test_the_update_notice_is_serialisable_and_opens_the_offer(user, monkeypatch) -> None:
    info = {"version": "9.9.9", "notes": "Nouveautés", "url": "x", "digest": "", "sig": ""}
    monkeypatch.setattr(updater, "check", lambda force=False: info)
    monkeypatch.setattr(updater, "auto_install", lambda: False)
    monkeypatch.setattr(ui_update, "_pending", None)
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


async def test_automatic_install_waits_for_a_quiet_moment_and_can_be_switched_off(user, monkeypatch) -> None:
    info = {"version": "9.9.9", "notes": "", "url": "x", "digest": "", "sig": ""}
    checks, installed, notified = [], [], []
    monkeypatch.setattr(updater, "check", lambda force=False: (checks.append(1), info)[1])
    monkeypatch.setattr(updater, "auto_install", lambda: True)
    monkeypatch.setattr(ui_update, "_pending", None)
    monkeypatch.setattr(ui_update, "_installing", False)

    async def fake_install(i):
        installed.append(i["version"])
    monkeypatch.setattr(ui_update, "install", fake_install)
    monkeypatch.setattr(ui_update.ui, "notification", lambda *a, **k: notified.append(a))
    quiet = {"v": False}

    async def fake_quiet():
        return quiet["v"]
    monkeypatch.setattr(ui_update, "_quiet", fake_quiet)
    ui.page("/")(main.index_page)
    await user.open("/")
    checks.clear()  # le simulateur déclenche déjà les minuteurs de la page : on repart de zéro
    monkeypatch.setattr(ui_update, "_pending", None)
    with user:
        await ui_update.auto_check()
        assert installed == [] and not notified and ui_update._pending == info  # trouvée, mais vous travaillez : on attend
        before = len(checks)  # (des minuteurs d'autres pages du simulateur peuvent aussi avoir cherché : on compare avant et après)
        await ui_update.auto_check()
        assert len(checks) == before  # pas de nouvelle recherche tant qu'une version attend
        quiet["v"] = True
        await ui_update.auto_check()
    assert installed == ["9.9.9"]
    # interrupteur coupé : la notification d'avant, aucune installation
    installed.clear()
    monkeypatch.setattr(updater, "auto_install", lambda: False)
    monkeypatch.setattr(ui_update, "_pending", None)
    monkeypatch.setattr(ui_update, "_installing", False)
    with user:
        await ui_update.auto_check()
    assert installed == [] and notified


def test_auto_install_is_on_by_default_and_follows_the_switch(clean_db, monkeypatch) -> None:
    from mailia import db
    monkeypatch.setattr(updater, "can_install", lambda: True)
    assert updater.auto_install()
    db.kv_set("update_install", "0")
    assert not updater.auto_install()
    db.kv_set("update_install", "1")
    monkeypatch.setattr(updater, "can_install", lambda: False)  # copie non remplaçable (dossier Applications…) : jamais d'installation automatique
    assert not updater.auto_install()
