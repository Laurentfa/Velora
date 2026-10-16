"""Favoris des destinataires : plus de ligne de raccourcis dans la rédaction ; les favoris se gèrent par l'étoile de la liste qui s'ouvre dans les champs À, Cc, Cci."""
import asyncio
from datetime import datetime

import pytest
from nicegui import events, ui
from nicegui.testing import User

import main
from mailia import contacts_ui, db, sync


@pytest.fixture
def box(monkeypatch):
    monkeypatch.setattr(sync, "run_cycle", lambda: None)
    ui.page("/")(main.index_page)
    db.kv_set("brief_day", datetime.now().strftime("%Y-%m-%d"))
    seen = []
    real = main.Mailbox.__init__
    monkeypatch.setattr(main.Mailbox, "__init__", lambda self, *a, **k: (real(self, *a, **k), seen.append(self))[0])
    return seen


async def open_composer(user, box):
    await user.open("/")
    b = box[-1]
    b.new_message()
    await asyncio.sleep(0.3)
    return b


def trigger_fav(user, ref):
    """Simule le clic sur l'étoile d'une ligne de la liste du champ « À » (événement « fav » du champ)."""
    (sel,) = [e for e in user.client.elements.values() if isinstance(e, ui.select) and e.props.get("label") == "À"]
    with user.client:
        for listener in sel._event_listeners.values():
            if listener.type == "fav":
                events.handle_event(listener.handler, events.GenericEventArguments(sender=sel, client=user.client, args=ref))
    return sel


async def test_no_shortcut_line_in_the_composer(user: User, box) -> None:
    db.contact_save("claire@exemple.fr", "Claire Martin", "manual")
    db.contact_favorite("claire@exemple.fr", True)
    await open_composer(user, box)
    await user.should_not_see("Raccourcis")
    await user.should_not_see(marker="shortcut-new")


async def test_the_star_adds_and_removes_a_favorite_contact(user: User, box) -> None:
    db.contact_save("claire@exemple.fr", "Claire Martin", "manual")
    await open_composer(user, box)
    sel = trigger_fav(user, "claire@exemple.fr")
    assert next(c for c in db.contacts_list() if c["email"] == "claire@exemple.fr")["favorite"]
    assert str(sel.options["claire@exemple.fr"]).startswith("⭐")
    trigger_fav(user, "claire@exemple.fr")
    assert not next(c for c in db.contacts_list() if c["email"] == "claire@exemple.fr")["favorite"]


async def test_the_star_works_on_groups(user: User, box) -> None:
    gid = db.group_save(None, "Équipe technique")
    db.group_set_member(gid, "alice@exemple.fr", True)
    await open_composer(user, box)
    trigger_fav(user, f"group:{gid}")
    assert contacts_ui.favorites()[0][1] == "group"


async def test_the_star_sends_the_row_rank_as_the_browser_does(user: User, box) -> None:
    db.contact_save("claire@exemple.fr", "Claire Martin", "manual")
    await open_composer(user, box)
    (sel,) = [e for e in user.client.elements.values() if isinstance(e, ui.select) and e.props.get("label") == "À"]
    rank = sel._values.index("claire@exemple.fr")
    trigger_fav(user, rank)
    assert next(c for c in db.contacts_list() if c["email"] == "claire@exemple.fr")["favorite"]
