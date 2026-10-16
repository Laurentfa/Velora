"""Menu de la liste : filtres cumulables, pastilles, tri par date / expéditeur / objet, sélection, affichage."""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from nicegui import events, ui
from nicegui.testing import User

import main
from mailia import db, queries, sync
from tests.conftest import add_message

NOW = datetime.now(timezone.utc)


def iso(**delta):
    return (NOW - timedelta(**delta)).isoformat()


@pytest.fixture
def data():
    add_message(mid="testbox|INBOX|1", subject="Zèbre", sender="zoe@exemple.fr", date=iso(hours=1), is_read=0)
    add_message(mid="testbox|INBOX|2", subject="Abricot", sender="Alice@exemple.fr", date=iso(days=3), is_read=1, has_att=1)
    add_message(mid="testbox|INBOX|3", subject="Mangue", sender="marc@exemple.fr", date=iso(days=20), is_read=0, has_att=1, needs_reply=1, priority=3)
    add_message(mid="testbox|INBOX|4", subject="Cerise", sender="bob@exemple.fr", date=iso(days=2), is_read=1, ckey="testbox|c4")


def subjects(**kw):
    return [r["subject"] for r in queries.list_messages(group=False, **kw)]


def test_filters_combine_and_date_filters(data):
    assert subjects(flags={"att"}) == ["Abricot", "Mangue"]
    assert subjects(flags={"att", "week"}) == ["Abricot"]  # cumulables : pièce jointe ET moins de 7 jours
    assert subjects(flags={"today"}) == ["Zèbre"]
    assert sorted(subjects(flags={"week"})) == ["Abricot", "Cerise", "Zèbre"]
    assert subjects(unread=True, flags={"att"}) == ["Mangue"]


def test_ai_filters_need_the_ai(data, monkeypatch):
    assert subjects(flags={"reply"}) == ["Mangue"] and subjects(flags={"urgent"}) == ["Mangue"]
    monkeypatch.delenv("ANTHROPIC_API_KEY")  # sans IA, ces deux filtres ne filtrent plus rien (ils ne sont d'ailleurs pas proposés)
    assert len(subjects(flags={"reply", "urgent"})) == 4


def test_pinned_filter(data):
    assert subjects(flags={"pinned"}) == []
    with db.conn() as c:
        c.execute("UPDATE messages SET flagged=1 WHERE id='testbox|INBOX|4'")  # épinglé = drapeau posé sur un message de la boîte de réception
    assert subjects(flags={"pinned"}) == ["Cerise"]
    assert subjects(flags={"pinned", "att"}) == []  # cumulable avec les autres filtres


def test_sorting_by_sender_and_subject_is_flat_and_case_insensitive(data):
    assert subjects(sort_by="subject", oldest_first=True) == ["Abricot", "Cerise", "Mangue", "Zèbre"]
    assert subjects(sort_by="subject") == ["Zèbre", "Mangue", "Cerise", "Abricot"]
    assert [r["sender"] for r in queries.list_messages(sort_by="sender", oldest_first=True)] == ["Alice@exemple.fr", "bob@exemple.fr", "marc@exemple.fr", "zoe@exemple.fr"]  # « Alice » avant « bob »
    assert subjects(sort_by="date") == ["Zèbre", "Cerise", "Abricot", "Mangue"]
    assert subjects(sort_by="n'importe quoi") == subjects()  # tri inconnu : par date


# ---------- interface ----------
@pytest.fixture
def box(monkeypatch, data):
    monkeypatch.setattr(sync, "run_cycle", lambda: None)
    ui.page("/")(main.index_page)
    db.kv_set("brief_day", datetime.now().strftime("%Y-%m-%d"))
    seen = []
    real = main.Mailbox.__init__
    monkeypatch.setattr(main.Mailbox, "__init__", lambda self, *a, **k: (real(self, *a, **k), seen.append(self))[0])
    return seen


def click(user, marker):
    (el,) = user.find(marker=marker).elements
    with user.client:
        for listener in el._event_listeners.values():
            if listener.type == "click":
                events.handle_event(listener.handler, events.ClickEventArguments(sender=el, client=user.client))
    return el


def shown(b):
    return [r["subject"] for r in b.rows]


async def test_menu_has_all_filters_and_the_ai_ones_only_with_the_ai(user: User, box, monkeypatch) -> None:
    await user.open("/")
    for k in ("unread", "pinned", "att", "today", "week", "reply", "urgent"):
        await user.should_see(marker=f"filter-{k}")
    for m in ("sort-date", "sort-sender", "sort-subject", "display-threads", "display-compact", "display-reset", "select-toggle"):
        await user.should_see(marker=m)


async def test_menu_without_the_ai_has_no_ai_filters(user: User, box, monkeypatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    await user.open("/")
    await user.should_see(marker="filter-att")
    await user.should_not_see(marker="filter-reply")
    await user.should_not_see(marker="filter-urgent")


async def test_choosing_filters_filters_the_list_and_shows_removable_chips(user: User, box) -> None:
    await user.open("/")
    b = box[-1]
    click(user, "filter-att")
    await asyncio.sleep(0.4)
    assert sorted(shown(b)) == ["Abricot", "Mangue"]
    await user.should_see(marker="filter-bar")
    await user.should_see(marker="chip-att")
    click(user, "filter-unread")  # cumul
    await asyncio.sleep(0.4)
    assert shown(b) == ["Mangue"] and b.active_filters() == ["unread", "att"]
    click(user, "filter-today")
    click(user, "filter-week")  # « aujourd'hui » et « 7 jours » s'excluent
    await asyncio.sleep(0.3)
    assert "today" not in b.filters and "week" in b.filters
    (chip,) = user.find(marker="chip-att").elements  # la pastille se retire d'un clic
    with user.client:
        for listener in chip._event_listeners.values():
            if listener.type == "remove":
                events.handle_event(listener.handler, events.GenericEventArguments(sender=chip, client=user.client, args=None))
    await asyncio.sleep(0.4)
    assert "att" not in b.filters
    click(user, "filter-clear")
    await asyncio.sleep(0.4)
    assert not b.filters and len(shown(b)) == 4
    await user.should_not_see(marker="filter-bar")


async def test_filters_reset_when_changing_folder_but_sort_is_remembered(user: User, box) -> None:
    await user.open("/")
    b = box[-1]
    click(user, "filter-att")
    click(user, "sort-subject")
    click(user, "order-old")
    await asyncio.sleep(0.5)
    assert shown(b) == ["Abricot", "Mangue"] and db.kv_get("list_sort_by") == "subject" and db.kv_get("list_sort") == "oldest"
    await b.go("unread", "")
    assert not b.filters and b.sort_by == "subject"  # le tri est un réglage, pas un filtre


async def test_reset_brings_everything_back(user: User, box) -> None:
    await user.open("/")
    b = box[-1]
    click(user, "filter-att")
    click(user, "sort-sender")
    await asyncio.sleep(0.4)
    await user.should_see(marker="chip-sort")
    click(user, "display-reset")
    await asyncio.sleep(0.5)
    assert not b.filters and b.sort_by == "date" and not b.oldest_first and shown(b)[0] == "Zèbre"


async def test_selection_is_a_toggle_button_and_the_bar_offers_what_the_old_menu_did(user: User, box) -> None:
    await user.open("/")
    b = box[-1]
    click(user, "select-toggle")
    await asyncio.sleep(0.3)
    assert b.select_mode
    click(user, "select-toggle")  # un second clic termine la sélection
    await asyncio.sleep(0.3)
    assert not b.select_mode


async def test_conversations_toggle_changes_the_setting(user: User, box) -> None:
    await user.open("/")
    from mailia import config
    before = config.conversations_enabled()
    click(user, "display-threads")
    await asyncio.sleep(0.4)
    assert config.conversations_enabled() is (not before)
    click(user, "display-threads")
    await asyncio.sleep(0.4)
    assert config.conversations_enabled() is before
