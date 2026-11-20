"""Pages de module (Ventiléa Connect…) : cliquer dans la colonne de gauche sur la page déjà ouverte ne la recharge pas, et en y revenant l'ascenseur est là où on l'avait laissé."""
import time
import types

import pytest
from nicegui import ui
from nicegui.testing import User

import main
from mailia import modules, sync
from tests.conftest import wait_until


@pytest.fixture
def box(monkeypatch, tmp_path):
    from mailia.ui_common import NOLIST_VIEWS, VIEWS
    monkeypatch.setattr(sync, "run_cycle", lambda: None)
    built = []
    va = modules.ModuleAPI({"id": "essai", "dir": tmp_path, "name": "Essai"}).add_view("page", "Page d'essai", "X", lambda api: (built.append(1), ui.label("Contenu de la page d'essai"))[1])
    vb = modules.ModuleAPI({"id": "autre", "dir": tmp_path, "name": "Autre"}).add_view("page", "Autre page", "Y", lambda api: ui.label("Autre contenu"))
    ui.page("/")(main.index_page)
    seen = []
    real = main.Mailbox.__init__
    monkeypatch.setattr(main.Mailbox, "__init__", lambda self, *a, **k: (real(self, *a, **k), seen.append(self))[0])
    yield seen, built, va, vb
    for v in (va, vb):
        modules.views.pop(v, None)
        VIEWS.pop(v, None)
        NOLIST_VIEWS.discard(v)


def scrolled(pos):
    return types.SimpleNamespace(args={"verticalPosition": pos, "verticalContainerSize": 800, "verticalSize": 3000})


async def test_clicking_the_open_module_page_does_not_reload_it(user: User, box) -> None:
    seen, built, va, vb = box
    await user.open("/")
    b = seen[-1]
    await b.go(va, "", "")
    assert b.view == va
    b.limit = 123  # un état que « go » remet à zéro quand il change vraiment de vue
    await b.go(va, "", "")  # clic sur la page déjà ouverte : rien n'est touché (pas de rechargement, donc l'ascenseur reste)
    await b.go(va, "", "")
    assert b.limit == 123 and b.view == va
    await b.go(vb, "", "")  # une autre page : la vue change vraiment
    assert b.view == vb and b.limit == 80


async def test_module_page_remembers_its_scroll_position(user: User, box, monkeypatch) -> None:
    seen, built, va, vb = box
    await user.open("/")
    b = seen[-1]
    calls = []
    await b.go(va, "", "")
    monkeypatch.setattr(b.rows_area, "scroll_to", lambda **k: calls.append(k))
    b._scroll_guard = 0.0
    await b.rows_scrolled(scrolled(640))
    assert b._scroll_pos[va] == 640
    await b.go(vb, "", "")
    b._scroll_guard = time.monotonic() + 30  # la garde posée par « go » (0,8 s) peut être échue sur une machine lente : on la rejoue
    await b.rows_scrolled(scrolled(0))  # défilement tardif de la page quittée, ou remise à zéro : ignoré pendant la garde
    assert b._scroll_pos[va] == 640 and vb not in b._scroll_pos
    calls.clear()
    await b.go(va, "", "")  # retour sur la première page
    await wait_until(lambda: bool(calls), timeout=3)
    assert calls == [{"pixels": 640.0}]


async def test_other_views_are_untouched(user: User, box) -> None:
    seen, built, va, vb = box
    await user.open("/")
    b = seen[-1]
    await b.go(va, "", "")
    await b.go("inbox", "", "")
    assert b.view == "inbox" and b.rows_area is not None
    await b.rows_scrolled(scrolled(900))  # une liste de mails : la position n'est pas retenue
    assert b._scroll_pos == {}
