"""Agenda du brief : lecture Exchange + Google réunies, grille de la semaine, affichage dans le brief seulement, ajout d'un rendez-vous."""
import asyncio
from datetime import date, datetime

import pytest
from nicegui import ui

import main
from mailia import agenda, config, db, queries, sync, ui_agenda
from tests.test_brief_refresh import make_brief


def ev(title, a, b, src, all_day=False):
    return {"title": title, "start": a, "end": b, "all_day": all_day, "where": "", "src": {src}}


def test_week_start_is_monday_and_skips_to_next_week_on_weekends() -> None:
    assert agenda.week_start(date(2026, 10, 20)) == date(2026, 10, 19)
    assert agenda.week_start(date(2026, 10, 24)) == date(2026, 10, 26)


def test_an_event_in_both_agendas_appears_once(monkeypatch) -> None:
    a, b = datetime(2026, 10, 20, 9), datetime(2026, 10, 20, 10)
    merged = agenda._merge({"exchange": [{"title": "Point", "start": a, "end": b, "all_day": False, "where": ""}],
                            "google": [{"title": "point ", "start": a, "end": b, "all_day": False, "where": ""}]})
    assert len(merged) == 1 and merged[0]["src"] == {"exchange", "google"}


def test_a_failing_source_does_not_hide_the_other(monkeypatch) -> None:
    agenda.invalidate()
    monkeypatch.setattr(agenda, "sources", lambda: {"exchange": True, "google": True})
    monkeypatch.setattr(agenda, "_exchange", lambda a, b: (_ for _ in ()).throw(RuntimeError("serveur injoignable")))
    monkeypatch.setattr(agenda.gcal, "list_events", lambda a, b: [{"title": "Rdv", "start": datetime(2026, 10, 20, 9), "end": datetime(2026, 10, 20, 10), "all_day": False, "where": ""}])
    r = agenda.fetch(date(2026, 10, 19), 7, force=True)
    assert [e["title"] for e in r["events"]] == ["Rdv"] and "Exchange" in r["errors"][0]


def test_overlapping_events_sit_side_by_side() -> None:
    mk = lambda h1, h2, t: {"title": t, "start": datetime(2026, 10, 20, h1), "end": datetime(2026, 10, 20, h2)}
    got = {e["title"]: (c, n) for e, c, n in agenda.lanes([mk(9, 11, "a"), mk(10, 12, "b"), mk(14, 15, "c")])}
    assert got == {"a": (0, 2), "b": (1, 2), "c": (0, 1)}


def test_grid_shows_five_days_events_and_all_day_banner() -> None:
    data = {"errors": [], "events": [ev("Point équipe", datetime(2026, 10, 20, 9), datetime(2026, 10, 20, 10), "exchange"),
                                       ev("Anniversaire", datetime(2026, 10, 23), datetime(2026, 10, 24), "google", all_day=True)]}
    h = ui_agenda.grid_html(date(2026, 10, 19), data, datetime(2026, 10, 20, 9, 30))
    assert h.count('class="ag-col"') == 5 and "Point équipe" in h and "Anniversaire" in h and "ag-now" in h and "ag-ex" in h and "ag-go" in h
    assert ui_agenda.range_label(date(2026, 10, 19)).startswith("19 – 23")


def test_weekend_events_add_two_columns() -> None:
    data = {"errors": [], "events": [ev("Brocante", datetime(2026, 10, 24, 10), datetime(2026, 10, 24, 12), "google")]}
    assert ui_agenda.grid_html(date(2026, 10, 19), data, datetime(2026, 10, 20, 9)).count('class="ag-col"') == 7


@pytest.fixture
def box(monkeypatch):
    monkeypatch.setattr(sync, "run_cycle", lambda: None)
    monkeypatch.setattr(config, "ai_enabled", lambda: True)
    monkeypatch.setattr(queries, "_refresh_headline", lambda sig, items: None)
    monkeypatch.setattr(agenda, "sources", lambda: {"exchange": False, "google": True})
    monkeypatch.setattr(agenda, "fetch", lambda first, days=7, force=False: {"errors": [], "events": [
        ev("Rendez-vous test", datetime.combine(first, datetime.min.time()).replace(hour=9), datetime.combine(first, datetime.min.time()).replace(hour=10), "google")]})
    ui.page("/")(main.index_page)
    db.kv_set("brief_day", datetime.now().strftime("%Y-%m-%d"))
    seen = []
    real = main.Mailbox.__init__
    monkeypatch.setattr(main.Mailbox, "__init__", lambda self, *a, **k: (real(self, *a, **k), seen.append(self))[0])
    return seen


async def test_agenda_shows_in_the_brief_and_goes_away_with_a_mail(user, box) -> None:
    make_brief(["testbox|INBOX|1"])
    await user.open("/")
    b = box[-1]
    await b.go("brief", "", "")
    await asyncio.sleep(0.8)
    await user.should_see("Rendez-vous test")
    await b.go("inbox", "", "")
    await asyncio.sleep(0.4)
    await user.should_not_see("Rendez-vous test")
    await b.go("brief", "", "")
    await asyncio.sleep(0.8)
    await user.should_see("Rendez-vous test")
    b.cur = queries.get_message("testbox|INBOX|1")  # un mail ouvert remplace l'agenda
    b.reader.refresh()
    await asyncio.sleep(0.4)
    await user.should_not_see("Rendez-vous test")
    b.new = True
    b.cur = None
    b.reader.refresh()
    await asyncio.sleep(0.4)
    await user.should_not_see("Rendez-vous test")


async def test_adding_an_appointment_calls_the_chosen_agenda(user, box, monkeypatch) -> None:
    make_brief(["testbox|INBOX|1"])
    calls = []
    monkeypatch.setattr(sync, "create_event", lambda account, title, a, b, where, body, backend="": calls.append((title, a, b, backend)))
    await user.open("/")
    b = box[-1]
    await b.go("brief", "", "")
    await asyncio.sleep(0.6)
    task = asyncio.create_task(b.agenda_slot({"day": "2026-10-20", "min": 14 * 60 + 30}))
    await asyncio.sleep(0.5)
    user.find(marker="agenda-title").type("Déjeuner")
    user.find(marker="agenda-save").click()
    await task
    assert calls == [("Déjeuner", datetime(2026, 10, 20, 14, 30), datetime(2026, 10, 20, 15, 30), "google")]


RSS = """<?xml version="1.0"?><rss version="2.0" xmlns:media="http://search.yahoo.com/mrss/"><channel><title>Le Monde</title>
<item><title>Titre un</title><link>https://exemple.fr/a</link><description>&lt;p&gt;Résumé &amp;amp; suite&lt;/p&gt;</description><pubDate>Tue, 20 Oct 2026 05:30:00 +0200</pubDate>
<media:content url="https://img.exemple.fr/a.jpg"/></item><item><title>Lien dangereux</title><link>javascript:alert(1)</link></item></channel></rss>""".encode()


def test_feed_parses_rss_and_skips_unsafe_links() -> None:
    from mailia import feed
    r = feed.parse(RSS)
    assert r["title"] == "Le Monde" and len(r["items"]) == 1
    it = r["items"][0]
    assert it["title"] == "Titre un" and "<" not in it["summary"] and it["image"].startswith("https://") and it["when"]


def test_feed_refuses_entities_and_non_feeds_and_http() -> None:
    from mailia import feed
    with pytest.raises(ValueError):
        feed.parse(b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]><rss><channel><title>&a;</title></channel></rss>')
    with pytest.raises(ValueError):
        feed.parse(b"<html></html>")
    assert feed.valid("https://www.lemonde.fr/rss/une.xml") and not feed.valid("http://x.fr/a") and not feed.valid("file:///etc/passwd")


async def test_feed_shows_under_the_agenda(user, box, monkeypatch) -> None:
    from mailia import feed
    make_brief(["testbox|INBOX|1"])
    monkeypatch.setattr(feed, "fetch", lambda force=False: feed.parse(RSS))
    await user.open("/")
    b = box[-1]
    await b.go("brief", "", "")
    await asyncio.sleep(0.9)
    await user.should_see("Rendez-vous test")
    await user.should_see("Titre un")
