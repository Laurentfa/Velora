"""Éphéméride du brief : phase de la lune calculée (vérifiée sur des dates connues), durée du jour, ligne sous la date."""
import asyncio
import json
from datetime import datetime

import pytest
from nicegui import ui

import main
from mailia import config, db, ephemeris, queries, sync, ui_weather, weather
from tests.conftest import add_message


def at(day: str) -> datetime:
    return datetime.fromisoformat(day + "T12:00:00+00:00")


def test_moon_phases_match_known_dates_of_october_2026():
    # nouvelle lune le 10 octobre 2026, premier quartier le 18, pleine lune le 26 (éphémérides publiées) : le calcul tombe à un jour près
    assert ephemeris.moon(at("2026-10-10"))["name"] == "Nouvelle lune"
    assert ephemeris.moon(at("2026-10-18"))["name"] == "Premier quartier"
    full = ephemeris.moon(at("2026-10-26"))
    assert full["name"] == "Pleine lune" and full["pct"] >= 98 and full["icon"] == "moon-full"
    assert ephemeris.moon(at("2026-11-02"))["name"] in ("Lune gibbeuse décroissante", "Dernier quartier")
    assert ephemeris.moon(at("2026-10-14"))["name"] in ("Premier croissant", "Nouvelle lune")


def test_the_lit_share_grows_to_full_then_shrinks():
    pct = [ephemeris.moon(at(f"2026-10-{d:02d}"))["pct"] for d in range(11, 27)]
    assert pct == sorted(pct)  # croissant de la nouvelle à la pleine lune
    after = [ephemeris.moon(at(f"2026-10-{d:02d}"))["pct"] for d in range(26, 32)]
    assert after == sorted(after, reverse=True)
    assert all(0 <= p <= 100 for p in pct + after)


def test_every_phase_has_an_existing_icon():
    from pathlib import Path
    icons = Path(weather.ICONS)
    for _, name, icon in ephemeris.PHASES:
        assert name and (icons / f"{icon}.svg").is_file()
    assert {i for _, _, i in ephemeris.PHASES} >= {"moon-new", "moon-full", "moon-first-quarter", "moon-last-quarter"}


def test_a_naive_date_is_accepted():
    assert ephemeris.moon(datetime(2026, 10, 26, 12, 0))["name"] in ("Pleine lune", "Lune gibbeuse croissante", "Lune gibbeuse décroissante")


def test_day_length():
    assert ephemeris.day_length("08:24", "18:58") == "10 h 34"
    assert ephemeris.day_length("07:05", "19:00") == "11 h 55" and ephemeris.day_length("9:00", "9:00") == "" and ephemeris.day_length("x", "y") == ""


def test_the_line_shows_the_moon_and_the_sun_when_the_weather_is_known():
    html = ui_weather.ephemeris_html(at("2026-10-26"), {"sunrise": "08:24", "sunset": "18:58"})
    assert "Pleine lune · 1" in html and "/weather-icons/moon-full.svg" in html and "08:24" in html and "18:58" in html and "jour 10 h 34" in html
    assert "sunrise.svg" in html and "sunset.svg" in html


def test_without_weather_only_the_moon_is_shown():
    html = ui_weather.ephemeris_html(at("2026-10-26"), None)
    assert "Pleine lune" in html and "sunrise" not in html and "jour" not in html


def test_the_line_follows_the_interface_language():
    db.kv_set("ui_lang", "en")
    html = ui_weather.ephemeris_html(at("2026-10-26"), {"sunrise": "08:24", "sunset": "18:58"})
    assert "Full moon" in html and "day 10 h 34" in html
    db.kv_set("ui_lang", "fr")


@pytest.fixture
def box(monkeypatch):
    monkeypatch.setattr(sync, "run_cycle", lambda: None)
    monkeypatch.setattr(config, "ai_enabled", lambda: True)
    monkeypatch.setattr(queries, "_refresh_headline", lambda sig, items: None)
    ui.page("/")(main.index_page)
    db.kv_set("brief_day", datetime.now().strftime("%Y-%m-%d"))
    seen = []
    real = main.Mailbox.__init__
    monkeypatch.setattr(main.Mailbox, "__init__", lambda self, *a, **k: (real(self, *a, **k), seen.append(self))[0])
    return seen


async def test_the_brief_header_shows_the_ephemeris_and_hides_it_elsewhere(user, box, monkeypatch) -> None:
    add_message("testbox|INBOX|1", ai_at="2026-10-19T08:00:00+00:00", category="action", priority=3, needs_reply=1, summary="x")
    db.kv_set("brief:headline", json.dumps({"intro": "Intro", "urgent": [], "waiting": [], "info": ""}))
    rows = queries.brief()["todo"]
    db.kv_set("brief:sig", f"{len(rows)}:{rows[0]['id']}")
    monkeypatch.setattr(weather, "cached_summary", lambda now=None: {"sunrise": "08:24", "sunset": "18:58"})
    await user.open("/")
    b = box[-1]
    await b.go("brief", "", "")
    await asyncio.sleep(0.6)
    assert b.ephem.visible and "moon-" in b.ephem.content and "10 h 34" in b.ephem.content
    await b.go("inbox", "", "")
    await asyncio.sleep(0.4)
    assert not b.ephem.visible and b.ephem.content == ""


async def test_the_header_picks_up_the_sun_times_once_the_weather_is_loaded(user, box, monkeypatch) -> None:
    place = {"name": "Rouen", "region": "", "lat": 49.44, "lon": 1.09}
    weather.set_place(place)
    summary = {"v": weather.VERSION, "temp": 10, "feels": 8, "label": "Couvert", "icon": "cloudy", "wind": 5, "tmin": 1, "tmax": 11, "rain": 5, "sunrise": "08:24",
               "sunset": "18:58", "next": [], "days": [], "details": {}}
    monkeypatch.setattr(weather, "report", lambda now=None: summary)
    add_message("testbox|INBOX|1", ai_at="2026-10-19T08:00:00+00:00", category="action", priority=3, needs_reply=1, summary="x")
    db.kv_set("brief:headline", json.dumps({"intro": "Intro", "urgent": [], "waiting": [], "info": ""}))
    rows = queries.brief()["todo"]
    db.kv_set("brief:sig", f"{len(rows)}:{rows[0]['id']}")
    await user.open("/")
    b = box[-1]
    await b.go("brief", "", "")
    await b.reload(force=True)
    await asyncio.sleep(0.8)
    # la météo vient d'être chargée en arrière-plan : le haut du brief doit connaître lever et coucher
    monkeypatch.setattr(weather, "cached_summary", lambda now=None: summary)
    b.refresh_title()
    assert "08:24" in b.ephem.content and "jour 10 h 34" in b.ephem.content
