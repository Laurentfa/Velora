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
    monkeypatch.setattr(agenda, "_exchange", lambda a, b: (_ for _ in ()).throw(RuntimeError("erreur interne")))
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
    task = asyncio.create_task(b.agenda_slot({"detail": {"day": "2026-10-20", "min": 14 * 60 + 30}}))
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


def test_tasks_from_both_sources_are_merged_and_failures_reported(monkeypatch) -> None:
    monkeypatch.setattr(agenda, "sources", lambda: {"exchange": True, "google": True})
    monkeypatch.setattr(agenda, "_exchange_tasks", lambda: [{"title": "Payer", "due": date(2026, 10, 21)}])
    monkeypatch.setattr(agenda.gcal, "list_tasks", lambda: [{"title": "payer", "due": date(2026, 10, 21)}, {"title": "Appeler", "due": date(2026, 10, 22)}])
    errs: list = []
    got = agenda._fetch_tasks(errs)
    assert [(t["title"].lower(), sorted(t["src"])) for t in got] == [("payer", ["exchange", "google"]), ("appeler", ["google"])] and not errs
    monkeypatch.setattr(agenda.gcal, "list_tasks", lambda: (_ for _ in ()).throw(RuntimeError("non autorisé")))
    errs = []
    assert len(agenda._fetch_tasks(errs)) == 1 and "Google Tasks" in errs[0][0]


def test_grid_shows_tasks_on_their_day_and_overdue_on_today() -> None:
    data = {"errors": [], "events": [], "tasks": [{"title": "Rendre le devis", "due": date(2026, 10, 22), "src": {"google"}},
                                                  {"title": "Tâche oubliée", "due": date(2026, 10, 15), "src": {"exchange"}},
                                                  {"title": "Hors semaine", "due": date(2026, 11, 20), "src": {"google"}}]}
    h = ui_agenda.grid_html(date(2026, 10, 19), data, datetime(2026, 10, 20, 9))
    assert "Rendre le devis" in h and "Tâche oubliée" in h and "ag-late" in h and "Hors semaine" not in h and h.count("ag-task") >= 2
    assert "1 tâche en retard" in h and "<details" in h and "15/10" in h
    assert h.count("ag-dh") >= 5 and "<span>mar.</span> 20" in h  # l'en-tête des jours reste en place  # les retards tiennent en une seule ligne repliable


def test_merge_keeps_every_reference_and_editable_needs_all_sources() -> None:
    a, b = datetime(2026, 10, 20, 9), datetime(2026, 10, 20, 10)
    base = {"title": "Point", "start": a, "end": b, "all_day": False, "where": ""}
    m = agenda._merge({"exchange": [{**base, "editable": True, "ref": {"src": "exchange", "account": "x", "id": "E1"}}],
                       "google": [{**base, "editable": False, "ref": {"src": "google", "id": "G1"}}]})
    assert len(m) == 1 and [r["id"] for r in m[0]["refs"]] == ["E1", "G1"] and m[0]["editable"] is False


def test_changes_are_applied_to_every_agenda_and_errors_reported(monkeypatch) -> None:
    calls = []

    class Prov:
        def calendar_update(self, i, *a): calls.append(("ews-up", i, a[0]))
        def calendar_delete(self, i): raise RuntimeError("serveur refuse")
        def task_complete(self, i): calls.append(("ews-done", i))

    monkeypatch.setattr(sync, "provider", lambda key: Prov())
    monkeypatch.setattr(agenda.gcal, "update_event", lambda i, *a: calls.append(("g-up", i, a[0])))
    monkeypatch.setattr(agenda.gcal, "delete_event", lambda i: calls.append(("g-del", i)))
    monkeypatch.setattr(agenda.gcal, "task_complete", lambda r: calls.append(("g-done", r["id"])))
    monkeypatch.setattr(sync, "_dry", lambda: False)
    refs = [{"src": "exchange", "account": "x", "id": "E1"}, {"src": "google", "id": "G1"}]
    ev = {"title": "Point", "refs": refs}
    agenda.update_event(ev, "Nouveau", datetime(2026, 10, 20, 9), datetime(2026, 10, 20, 10), "")
    agenda.complete_task({"title": "T", "refs": [{"src": "exchange", "account": "x", "id": "T1"}, {"src": "google", "list": "L", "id": "T2"}]})
    with pytest.raises(RuntimeError, match="serveur refuse"):
        agenda.delete_event(ev)
    assert ("ews-up", "E1", "Nouveau") in calls and ("g-up", "G1", "Nouveau") in calls and ("g-del", "G1") in calls  # Google est supprimé malgré l'échec d'Exchange
    assert ("ews-done", "T1") in calls and ("g-done", "T2") in calls


async def test_clicking_an_event_opens_its_card_and_delete_asks_confirmation(user, box, monkeypatch) -> None:
    make_brief(["testbox|INBOX|1"])
    got = []
    monkeypatch.setattr(agenda, "delete_event", lambda ev: got.append(ev["title"]))
    monkeypatch.setattr(agenda, "fetch", lambda first, days=7, force=False: {"errors": [], "tasks": [], "events": [{
        "title": "Rdv modifiable", "start": datetime.combine(first, datetime.min.time()).replace(hour=9), "end": datetime.combine(first, datetime.min.time()).replace(hour=10),
        "all_day": False, "where": "", "src": {"google"}, "refs": [{"src": "google", "id": "G1"}], "editable": True}]})
    await user.open("/")
    b = box[-1]
    await b.go("brief", "", "")
    await asyncio.sleep(0.8)
    assert "e0" in b._agenda_reg
    task = asyncio.create_task(b.agenda_item({"detail": {"ref": "e0"}}))
    await asyncio.sleep(0.4)
    user.find(marker="agenda-delete").click()
    await asyncio.sleep(0.3)
    assert not got  # pas de suppression avant la confirmation
    user.find(marker="agenda-confirm").click()
    await task
    assert got == ["Rdv modifiable"]


async def test_read_only_events_have_no_save_or_delete(user, box, monkeypatch) -> None:
    make_brief(["testbox|INBOX|1"])
    monkeypatch.setattr(agenda, "fetch", lambda first, days=7, force=False: {"errors": [], "tasks": [], "events": [{
        "title": "Série hebdo", "start": datetime.combine(first, datetime.min.time()).replace(hour=9), "end": datetime.combine(first, datetime.min.time()).replace(hour=10),
        "all_day": False, "where": "", "src": {"exchange"}, "refs": [{"src": "exchange", "account": "x", "id": "E1"}], "editable": False}]})
    await user.open("/")
    b = box[-1]
    await b.go("brief", "", "")
    await asyncio.sleep(0.8)
    asyncio.create_task(b.agenda_item({"detail": {"ref": "e0"}}))
    await asyncio.sleep(0.4)
    await user.should_see("Série hebdo")
    await user.should_not_see(marker="agenda-delete")
    await user.should_not_see(marker="agenda-save")


async def test_a_task_can_be_completed_from_its_card(user, box, monkeypatch) -> None:
    make_brief(["testbox|INBOX|1"])
    done = []
    monkeypatch.setattr(agenda, "complete_task", lambda t: done.append(t["title"]))
    monkeypatch.setattr(agenda, "fetch", lambda first, days=7, force=False: {"errors": [], "events": [], "tasks": [
        {"title": "Rendre le devis", "due": first, "src": {"google"}, "refs": [{"src": "google", "list": "L", "id": "T"}]}]})
    await user.open("/")
    b = box[-1]
    await b.go("brief", "", "")
    await asyncio.sleep(0.8)
    task = asyncio.create_task(b.agenda_item({"detail": {"ref": "t0"}}))
    await asyncio.sleep(0.4)
    user.find(marker="agenda-done").click()
    await task
    assert done == ["Rendre le devis"]


def test_exchange_calendar_and_task_fields_exist_in_exchangelib() -> None:
    from exchangelib.items import CalendarItem, Task
    cal, task = {f.name for f in CalendarItem.FIELDS}, {f.name for f in Task.FIELDS}
    assert {"subject", "start", "end", "location", "is_all_day", "type", "is_meeting"} <= cal  # les noms de champs demandés à Exchange doivent exister
    assert {"subject", "due_date", "status"} <= task


async def test_feed_survives_the_panel_being_rebuilt_while_loading(user, box, monkeypatch) -> None:
    import time
    from mailia import feed
    make_brief(["testbox|INBOX|1"])

    def slow(force=False):
        time.sleep(0.5)
        return feed.parse(RSS)
    monkeypatch.setattr(feed, "fetch", slow)
    await user.open("/")
    b = box[-1]
    await b.go("brief", "", "")
    await asyncio.sleep(0.2)
    b.reader.refresh()  # le brief se redessine pendant que le flux se charge
    await asyncio.sleep(0.2)
    b.reader.refresh()
    await asyncio.sleep(1.6)
    await user.should_see("Titre un")


async def test_feed_failure_is_shown_not_hidden(user, box, monkeypatch) -> None:
    from mailia import feed
    make_brief(["testbox|INBOX|1"])
    monkeypatch.setattr(feed, "fetch", lambda force=False: (_ for _ in ()).throw(OSError("réseau coupé")))
    await user.open("/")
    b = box[-1]
    await b.go("brief", "", "")
    await asyncio.sleep(0.9)
    await user.should_see("Flux indisponible pour le moment.")


def test_errors_are_explained_in_french_and_transient_ones_stay_hidden(monkeypatch) -> None:
    d = agenda.describe_error
    assert d("exchange", RuntimeError("Reraised from ReadTimeout(HTTPSConnectionPool(host='x'): Read timed out. (read timeout=120))"))[1] == "timeout"
    text, kind = d("google", RuntimeError("Le fournisseur a refusé la connexion : Token has been expired or revoked."))
    assert kind == "reauth" and "Reconnectez" in text and "Token" not in text
    assert d("tasks", RuntimeError("Google n'a pas encore l'autorisation de lire vos tâches : reconnectez Google Agenda"))[1] == "reauth"
    assert d("tasks", RuntimeError("L'API Google Tasks n'est pas activée dans votre projet"))[1] == "api"
    assert d("exchange", RuntimeError("erreur interne")) == ("Exchange : erreur interne", "other")


def test_fetch_reports_a_single_reconnect_request_and_hides_a_timeout(monkeypatch) -> None:
    agenda.invalidate()
    monkeypatch.setattr(agenda, "sources", lambda: {"exchange": True, "google": True})
    monkeypatch.setattr(agenda, "_exchange", lambda a, b: (_ for _ in ()).throw(RuntimeError("Read timed out")))
    monkeypatch.setattr(agenda.gcal, "list_events", lambda a, b: (_ for _ in ()).throw(RuntimeError("Token has been expired or revoked")))
    monkeypatch.setattr(agenda, "_exchange_tasks", lambda: [])
    monkeypatch.setattr(agenda.gcal, "list_tasks", lambda: (_ for _ in ()).throw(RuntimeError("reconnectez Google Agenda")))
    r = agenda.fetch(date(2026, 10, 19), 7, force=True)
    assert r["reauth"] is True and r["transient"] is True
    assert len(r["errors"]) == 1 and "Reconnectez" in r["errors"][0]  # deux pannes Google = un seul message ; le délai Exchange n'est pas affiché
