"""Raccourcis destinataires de la rédaction : groupes et favoris en un clic vers À, Cc ou Cci ; création d'un raccourci depuis les destinataires saisis."""
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


def make_shortcuts():
    gid = db.group_save(None, "Équipe technique")
    for a in ("alice@exemple.fr", "bob@exemple.fr"):
        db.group_set_member(gid, a, True)
    db.group_favorite(gid, True)
    db.contact_save("claire@exemple.fr", "Claire Martin", "manual")
    db.contact_favorite("claire@exemple.fr", True)
    return gid


def click(user, marker):
    (el,) = user.find(marker=marker).elements
    with user.client:
        for listener in el._event_listeners.values():
            if listener.type == "click":
                events.handle_event(listener.handler, events.ClickEventArguments(sender=el, client=user.client))
    return el


def focus(user, b, target):
    """Simule un clic dans un champ de destinataires (l'événement « focus » du champ)."""
    fld = b.rcpt_fields[target]
    with user.client:
        for listener in fld._event_listeners.values():
            if listener.type == "focus":
                events.handle_event(listener.handler, events.GenericEventArguments(sender=fld, client=user.client, args=None))


async def open_composer(user, box):
    await user.open("/")
    b = box[-1]
    b.new_message()
    await asyncio.sleep(0.3)
    return b


async def test_bar_lists_groups_and_favorite_contacts(user: User, box) -> None:
    gid = make_shortcuts()
    await open_composer(user, box)
    await user.should_see("Raccourcis")
    await user.should_see("Équipe technique")
    await user.should_see("Claire Martin")
    assert user.find(marker=f"shortcut-group-{gid}").elements


async def test_a_click_adds_the_group_to_the_chosen_field(user: User, box) -> None:
    gid = make_shortcuts()
    b = await open_composer(user, box)
    click(user, f"shortcut-group-{gid}")  # par défaut : « À »
    await asyncio.sleep(0.2)
    assert set(contacts_ui.split_addrs(b.draft["to"])) == {"alice@exemple.fr", "bob@exemple.fr"} and not b.draft["cc"]
    focus(user, b, "cc")  # on clique dans le champ Cc : le raccourci suivant ira là
    click(user, "shortcut-contact-claire@exemple.fr")
    await asyncio.sleep(0.2)
    assert contacts_ui.split_addrs(b.draft["cc"]) == ["claire@exemple.fr"]
    assert set(contacts_ui.split_addrs(b.draft["to"])) == {"alice@exemple.fr", "bob@exemple.fr"}  # « À » n'a pas bougé


async def test_the_hint_shows_where_the_shortcut_will_go_and_no_selector_is_left(user: User, box) -> None:
    b = await open_composer(user, box)
    await user.should_not_see(marker="rcpt-mode")  # plus de sélecteur À / Cc / Cci : le dernier champ cliqué suffit
    assert b.rcpt_hint.text == "→ À"
    focus(user, b, "cc")
    assert b.rcpt_hint.text == "→ Cc" and b.rcpt_target == "cc"
    focus(user, b, "to")
    assert b.rcpt_target == "to"


async def test_bcc_choice_reveals_the_hidden_field(user: User, box) -> None:
    make_shortcuts()
    b = await open_composer(user, box)
    assert not b.bcc_field.visible
    b.bcc_field.set_visibility(True)
    focus(user, b, "bcc")
    click(user, "shortcut-contact-claire@exemple.fr")
    await asyncio.sleep(0.2)
    assert b.bcc_field.visible and contacts_ui.split_addrs(b.draft["bcc"]) == ["claire@exemple.fr"]


async def test_without_shortcuts_the_bar_still_offers_to_create_one(user: User, box) -> None:
    await open_composer(user, box)
    await user.should_see("Raccourcis")
    await user.should_see("Aucun pour l'instant")
    assert user.find(marker="shortcut-new").elements


async def test_saving_current_recipients_makes_a_new_shortcut(user: User, box) -> None:
    b = await open_composer(user, box)
    b.draft.update(to="marie@exemple.fr; paul@exemple.fr", cc="chef@exemple.fr")
    task = asyncio.create_task(b.save_shortcut())
    await asyncio.sleep(0.3)
    (name,) = user.find(marker="shortcut-name").elements
    name.set_value("Projet Alpha")
    click(user, "shortcut-save")
    await task
    g = next(g for g in db.groups_list() if g["name"] == "Projet Alpha")
    assert g["favorite"] and set(g["members"]) == {"marie@exemple.fr", "paul@exemple.fr", "chef@exemple.fr"}
    await asyncio.sleep(0.2)
    await user.should_see("Projet Alpha")  # le raccourci apparaît aussitôt dans la barre


async def test_saving_under_an_existing_name_completes_that_shortcut(user: User, box) -> None:
    make_shortcuts()
    b = await open_composer(user, box)
    b.draft.update(to="nouveau@exemple.fr", cc="")
    task = asyncio.create_task(b.save_shortcut())
    await asyncio.sleep(0.3)
    (name,) = user.find(marker="shortcut-name").elements
    name.set_value("équipe TECHNIQUE")  # casse différente : c'est le même raccourci
    click(user, "shortcut-save")
    await task
    teams = [g for g in db.groups_list() if g["name"].lower() == "équipe technique"]
    assert len(teams) == 1 and set(teams[0]["members"]) == {"alice@exemple.fr", "bob@exemple.fr", "nouveau@exemple.fr"}


async def test_nothing_is_saved_without_recipients(user: User, box) -> None:
    b = await open_composer(user, box)
    await b.save_shortcut()
    assert db.groups_list() == []
