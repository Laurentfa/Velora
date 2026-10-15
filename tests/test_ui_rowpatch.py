"""Liste de gauche : seules les lignes modifiées sont reconstruites ; le menu clic droit n'est créé qu'au survol de la ligne."""
from datetime import datetime

import pytest
from nicegui import events, ui
from nicegui.testing import User

import main
from mailia import db, sync
from tests.conftest import add_message


@pytest.fixture
def mailbox(monkeypatch):
    monkeypatch.setattr(sync, "run_cycle", lambda: None)
    ui.page("/")(main.index_page)  # NiceGUI repart de zéro à chaque test : on réenregistre la page principale
    db.kv_set("brief_day", datetime.now().strftime("%Y-%m-%d"))
    add_message(mid="testbox|INBOX|1", subject="Devis chaudière", body="Voici le devis demandé.", ckey="testbox|c1")
    add_message(mid="testbox|INBOX|2", subject="Réunion jeudi", body="On se voit jeudi.", ckey="testbox|c2", date="2026-10-01T11:00:00+00:00")


@pytest.fixture
def box(monkeypatch):
    seen = []
    real = main.Mailbox.__init__

    def init(self, *a, **k):
        real(self, *a, **k)
        seen.append(self)
    monkeypatch.setattr(main.Mailbox, "__init__", init)
    return seen


def row_of(user, mid):
    (el,) = user.find(marker=f"row-{mid}").elements
    return el


async def test_only_the_changed_row_is_rebuilt(user: User, mailbox, box) -> None:
    await user.open("/")
    await user.should_see("Devis chaudière")
    b = box[-1]
    first, second = row_of(user, "testbox|INBOX|1"), row_of(user, "testbox|INBOX|2")
    order = lambda: [e.props["data-id"] for e in b._row_els[first.props.get("id", "testbox|INBOX|1")].parent_slot.parent.default_slot.children if e.props.get("data-id")]  # noqa: E731
    before = order()
    with db.conn() as c:
        c.execute("UPDATE messages SET summary='Résumé tout neuf', ai_at='2026-10-15T08:00:00+00:00' WHERE id='testbox|INBOX|2'")
    await b.reload()
    await user.should_see("Résumé tout neuf")
    assert row_of(user, "testbox|INBOX|1") is first  # la ligne inchangée n'a pas été reconstruite
    assert row_of(user, "testbox|INBOX|2") is not second  # la ligne modifiée, si
    assert order() == before and len(before) == 2  # l'ordre des lignes est resté le même


async def test_structure_change_redraws_everything(user: User, mailbox, box) -> None:
    await user.open("/")
    await user.should_see("Devis chaudière")
    b = box[-1]
    first = row_of(user, "testbox|INBOX|1")
    add_message(mid="testbox|INBOX|3", subject="Nouveau venu", body="Bonjour", ckey="testbox|c3", date="2026-10-02T09:00:00+00:00")
    await b.reload()
    await user.should_see("Nouveau venu")
    assert row_of(user, "testbox|INBOX|1") is not first  # un mail de plus : redessin complet


async def test_context_menu_is_built_on_first_hover(user: User, mailbox) -> None:
    await user.open("/")
    await user.should_see("Devis chaudière")
    await user.should_not_see("Transférer…")  # aucune entrée de menu n'existe tant qu'on n'a pas survolé une ligne
    wrap = next(e for e in user.find(kind=main.ui.element).elements if e.props.get("data-id") == "testbox|INBOX|1")
    with user.client:
        for listener in wrap._event_listeners.values():
            if listener.type == "mouseenter.once":
                events.handle_event(listener.handler, events.GenericEventArguments(sender=wrap, client=user.client, args=None))
    await user.should_see("Transférer…")
    assert len(user.find("Répondre", kind=main.ui.menu_item).elements) == 1  # seule la ligne survolée a son menu
