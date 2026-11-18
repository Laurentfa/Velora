"""Une opération longue d'un module (analyse, import…) empêche l'installation automatique d'une mise à jour : elle ferme Velora et l'interromprait."""
import asyncio
from pathlib import Path

from mailia import modules, ui_update


def _api(mid: str = "essai") -> modules.ModuleAPI:
    return modules.ModuleAPI({"id": mid, "dir": Path("."), "name": mid})


def test_busy_declaration_lifecycle() -> None:
    modules.busy_tasks.clear()
    a, b = _api("a"), _api("b")
    assert modules.busy_labels() == []
    t1, t2 = a.begin_busy("analyse en cours"), b.begin_busy("import en cours")
    assert sorted(modules.busy_labels()) == ["analyse en cours", "import en cours"] and t1 != t2
    a.end_busy(t1)
    assert modules.busy_labels() == ["import en cours"]
    a.end_busy(t1)  # fin répétée : sans effet
    b.end_busy(t2)
    assert modules.busy_labels() == []


def test_a_forgotten_declaration_does_not_block_updates_forever() -> None:
    modules.busy_tasks.clear()
    t = _api().begin_busy("oubliée")
    modules.busy_tasks[t]["since"] -= modules.BUSY_MAX_SECONDS + 1
    assert modules.busy_labels() == [] and t not in modules.busy_tasks


def test_update_waits_while_a_module_is_busy(monkeypatch) -> None:
    modules.busy_tasks.clear()

    async def js_idle(code, timeout=None):  # la page dit : personne depuis longtemps, rien d'ouvert
        return True
    monkeypatch.setattr(ui_update.ui, "run_javascript", js_idle)
    assert asyncio.run(ui_update._quiet()) is True
    token = _api().begin_busy("analyse en cours")
    assert asyncio.run(ui_update._quiet()) is False  # une analyse tourne : pas d'installation
    _api().end_busy(token)
    assert asyncio.run(ui_update._quiet()) is True
