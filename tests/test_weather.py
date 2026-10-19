"""Météo du brief : correspondance des codes, résumé d'Open-Meteo, mémoire et hors connexion, carte du brief, réglage de la ville."""
import asyncio
import json

import pytest
from nicegui import events, ui
from nicegui.testing import User

import main
from mailia import config, db, sync, ui_weather, weather

def _forecast():
    days = [f"2026-10-{19 + i}" for i in range(7)]
    return {
        "current": {"time": "2026-10-19T08:15", "temperature_2m": 10.4, "apparent_temperature": 7.6, "weather_code": 61, "wind_speed_10m": 12.2, "is_day": 1,
                    "relative_humidity_2m": 92, "pressure_msl": 1019.6, "cloud_cover": 100, "wind_direction_10m": 73, "visibility": 16420.0},
        "hourly": {"time": [f"2026-10-{19 + h // 24}T{h % 24:02d}:00" for h in range(48)], "temperature_2m": [9 + (h % 24) / 4 for h in range(48)],
                   "weather_code": ([3] * 12 + [61] * 12) * 2, "precipitation_probability": ([10] * 12 + [70] * 12) * 2, "is_day": ([0] * 7 + [1] * 12 + [0] * 5) * 2},
        "daily": {"time": days, "weather_code": [61, 61, 3, 0, 95, 71, 2], "temperature_2m_max": [14.6, 13, 12, 15, 16, 4, 11], "temperature_2m_min": [6.2, 7, 5, 6, 8, -1, 3],
                  "precipitation_probability_max": [80, 90, 20, 5, 60, 70, 10], "precipitation_sum": [1.46, 6.3, 0.0, 0, 12.04, 3, 0], "uv_index_max": [2.55, 1.1, 3, 5, 7, 0.5, 4],
                  "wind_gusts_10m_max": [24.4, 30, 20, 10, 55, 40, 12], "sunshine_duration": [22680, 3600, 30000, 36000, 7200, 0, 28800],
                  "sunrise": [f"{d}T08:22" for d in days], "sunset": [f"{d}T18:57" for d in days]},
    }


FORECAST = _forecast()
AIR = {"european_aqi": 37, "pm2_5": 13.7}
PLACE = {"name": "Rouen", "region": "Normandie", "lat": 49.44, "lon": 1.09}


def test_every_weather_code_has_an_existing_animated_icon():
    for code, (label, day, night) in weather.CODES.items():
        assert label and (weather.ICONS / f"{day}.svg").is_file() and (weather.ICONS / f"{night}.svg").is_file(), code
    for extra in ("raindrop", "sunrise", "sunset", "cloudy"):
        assert (weather.ICONS / f"{extra}.svg").is_file()
    assert "MIT" in (weather.ICONS / "LICENSE-meteocons.txt").read_text(encoding="utf-8")  # la licence des icônes est livrée avec elles


def test_describe_picks_day_or_night_and_survives_an_unknown_code():
    assert weather.describe(0, True) == ("Ciel dégagé", "clear-day") and weather.describe(0, False) == ("Ciel dégagé", "clear-night")
    assert weather.describe(61)[1] == "rain" and weather.describe(95, False)[1] == "thunderstorms-night"
    assert weather.describe(9999)[1] == "cloudy"


def test_summary_reads_the_open_meteo_answer():
    s = weather.summarize(FORECAST, air=AIR)
    assert (s["temp"], s["feels"], s["wind"], s["tmin"], s["tmax"], s["rain"]) == (10, 8, 12, 6, 15, 80)
    assert s["label"] == "Pluie faible" and s["icon"] == "rain" and (s["sunrise"], s["sunset"]) == ("08:22", "18:57")
    assert s["next"][0]["hour"] == "09 h" and s["next"][0]["icon"] == "overcast-day" and s["next"][3]["icon"] == "rain" and s["next"][3]["rain"] == 70
    assert s["next"][-1]["hour"] == "22 h" and len(s["next"]) == 14  # jusqu'à ce soir : 14 heures au plus


def test_the_hours_run_to_tonight_but_never_fewer_than_six():
    late = json.loads(json.dumps(FORECAST))
    late["current"]["time"] = "2026-10-19T21:15"
    s = weather.summarize(late)
    assert [h["hour"] for h in s["next"]] == ["22 h", "23 h", "00 h", "01 h", "02 h", "03 h"]  # tard le soir : on passe minuit pour garder 6 heures
    mid = json.loads(json.dumps(FORECAST))
    mid["current"]["time"] = "2026-10-19T15:10"
    assert [h["hour"] for h in weather.summarize(mid)["next"]] == [f"{h:02d} h" for h in range(16, 23)]


def test_seven_days_with_readable_labels():
    days = weather.summarize(FORECAST)["days"]
    assert len(days) == 7 and [d["label"] for d in days][:3] == ["Aujourd'hui", "Demain", "mer. 21"] and days[4]["label"] == "ven. 23"
    assert (days[4]["icon"], days[4]["tmax"], days[4]["tmin"], days[4]["rain"], days[4]["mm"]) == ("thunderstorms-day", 16, 8, 60, 12.0)
    assert days[5]["tmin"] == -1 and days[5]["icon"] == "snow"


def test_details_and_air_quality():
    d = weather.summarize(FORECAST, air=AIR)["details"]
    assert (d["mm"], d["gust"], d["uv"], d["uv_label"], d["sun_h"]) == (1.5, 24, 3, "modéré", 6.3)  # l'indice affiché et son libellé concordent
    assert (d["humidity"], d["pressure"], d["clouds"], d["wind_dir"], d["visibility_km"]) == (92, 1020, 100, "E", 16)
    assert (d["aqi"], d["aqi_label"]) == (37, "bon")
    assert weather.summarize(FORECAST)["details"]["aqi"] is None  # service de l'air indisponible : le reste s'affiche


def test_scales():
    assert [weather._scale(v, weather.AQI_SCALE) for v in (10, 30, 50, 70, 90, 150)] == ["très bon", "bon", "moyen", "médiocre", "mauvais", "très mauvais"]
    assert [weather._scale(v, weather.UV_SCALE) for v in (0, 3, 6, 8, 11)] == ["faible", "modéré", "élevé", "très élevé", "extrême"]
    assert [weather.compass(x) for x in (0, 45, 90, 180, 270, 350)] == ["N", "NE", "E", "S", "O", "N"]


def test_search_parses_the_places(monkeypatch):
    monkeypatch.setattr(weather, "_get", lambda url, params: {"results": [
        {"name": "Rouen", "admin1": "Normandie", "country": "France", "latitude": 49.4431, "longitude": 1.0993}, {"name": "sans coordonnées"}]})
    r = weather.search("rouen")
    assert r == [{"name": "Rouen", "region": "Normandie", "country": "France", "lat": 49.443, "lon": 1.099}]
    assert weather.search("a") == [] and weather.search("") == []  # trop court : pas d'appel


def test_nothing_is_shown_without_a_city_or_when_turned_off():
    assert not weather.enabled() and weather.report() is None
    weather.set_place(PLACE)
    assert weather.enabled()
    db.kv_set("weather_on", "0")
    assert not weather.enabled() and weather.report() is None


def test_the_forecast_is_remembered_for_half_an_hour(monkeypatch):
    weather.set_place(PLACE)
    calls = []
    monkeypatch.setattr(weather, "fetch", lambda lat, lon: calls.append((lat, lon)) or FORECAST)
    monkeypatch.setattr(weather, "fetch_air", lambda lat, lon: AIR)
    t0 = 1_000_000.0
    a = weather.report(now=t0)
    b = weather.report(now=t0 + 600)  # moins de 30 minutes : pas de nouvel appel
    assert a == b and len(calls) == 1
    weather.report(now=t0 + 2000)
    assert len(calls) == 2


def test_offline_the_last_known_weather_is_shown_for_six_hours_then_nothing(monkeypatch):
    weather.set_place(PLACE)
    monkeypatch.setattr(weather, "fetch", lambda lat, lon: FORECAST)
    monkeypatch.setattr(weather, "fetch_air", lambda lat, lon: AIR)
    t0 = 1_000_000.0
    good = weather.report(now=t0)

    def offline(lat, lon):
        raise OSError("pas de réseau")
    monkeypatch.setattr(weather, "fetch", offline)
    assert weather.report(now=t0 + 3600) == good  # hors connexion : la dernière météo connue
    assert weather.report(now=t0 + 7 * 3600) is None  # trop ancienne : rien plutôt qu'une fausse météo


def test_changing_the_city_forgets_the_old_forecast(monkeypatch):
    weather.set_place(PLACE)
    monkeypatch.setattr(weather, "fetch", lambda lat, lon: FORECAST)
    monkeypatch.setattr(weather, "fetch_air", lambda lat, lon: AIR)
    weather.report(now=1_000_000.0)
    weather.set_place({"name": "Lyon", "region": "", "lat": 45.76, "lon": 4.84})
    assert json.loads(db.kv_get("weather_cache") or "null") is None or db.kv_get("weather_cache") == ""


def test_the_card_shows_the_figures_the_attribution_and_escapes_the_place():
    html = ui_weather.card_html(weather.summarize(FORECAST, air=AIR), "<b>Rouen</b>, Normandie")
    assert "10°" in html and "Pluie faible" in html and "min 6°" in html and "max 15°" in html and "vent 12 km/h" in html
    assert "/weather-icons/rain.svg" in html and "Weather data by Open-Meteo.com" in html and "Meteocons" in html
    assert "<b>Rouen</b>" not in html and "&lt;b&gt;Rouen" in html
    assert "Demain" in html and "ven. 23" in html and html.count('class="wx-d"') == 7  # les 7 jours
    import html as htmllib
    text = htmllib.unescape(html)  # les apostrophes sont protégées dans le HTML
    assert "<details" in html and "Détails" in html and "Qualité de l'air" in text and "37 · bon" in html and "1,5 mm" in html and "Indice UV" in html


def test_the_card_without_details_still_renders():
    s = weather.summarize(FORECAST)
    s.pop("details")
    s.pop("days")
    html = ui_weather.card_html(s, "Rouen")
    assert "10°" in html and "<details" not in html and "wx-d\"" not in html


@pytest.fixture
def box(monkeypatch):
    from datetime import datetime
    monkeypatch.setattr(sync, "run_cycle", lambda: None)
    ui.page("/")(main.index_page)
    db.kv_set("brief_day", datetime.now().strftime("%Y-%m-%d"))
    seen = []
    real = main.Mailbox.__init__
    monkeypatch.setattr(main.Mailbox, "__init__", lambda self, *a, **k: (real(self, *a, **k), seen.append(self))[0])
    return seen


async def test_the_brief_shows_the_weather_card(user: User, box, monkeypatch) -> None:
    weather.set_place(PLACE)
    monkeypatch.setattr(weather, "fetch", lambda lat, lon: FORECAST)
    monkeypatch.setattr(weather, "fetch_air", lambda lat, lon: AIR)
    monkeypatch.setattr(config, "ai_enabled", lambda: True)
    await user.open("/")
    b = box[-1]
    await b.go("brief", "", "")
    await asyncio.sleep(1.2)
    await user.should_see("Pluie faible")
    await user.should_see("Rouen, Normandie")
    await user.should_see("Weather data by Open-Meteo.com")


async def test_the_brief_has_no_card_without_a_city(user: User, box, monkeypatch) -> None:
    monkeypatch.setattr(config, "ai_enabled", lambda: True)
    await user.open("/")
    b = box[-1]
    await b.go("brief", "", "")
    await asyncio.sleep(0.8)
    await user.should_not_see("Open-Meteo")


async def test_settings_let_you_search_and_pick_a_city(user: User, monkeypatch) -> None:
    from mailia.ui_settings import open_settings
    monkeypatch.setattr(weather, "search", lambda q: [{"name": "Rouen", "region": "Normandie", "country": "France", "lat": 49.443, "lon": 1.099},
                                                      {"name": "Rouen", "region": "Île-de-France", "country": "France", "lat": 48.9, "lon": 2.3}])

    @ui.page("/settings")
    def page() -> None:
        open_settings(ui.element("div"), lambda acct: None)
    await user.open("/settings")
    await user.should_see("Météo dans le brief du jour")
    await user.should_see("Aucune ville choisie")
    user.find(marker="weather-query").type("Rouen")
    (btn,) = user.find(marker="weather-search").elements
    with user.client:
        for listener in btn._event_listeners.values():
            if listener.type == "click":
                events.handle_event(listener.handler, events.ClickEventArguments(sender=btn, client=user.client))
    await user.should_see("Rouen, Normandie, France")
    first = next(e for e in user.find(marker="weather-result").elements if e.text.startswith("Rouen, Normandie"))
    with user.client:
        for listener in first._event_listeners.values():
            if listener.type == "click":
                events.handle_event(listener.handler, events.ClickEventArguments(sender=first, client=user.client))
    await asyncio.sleep(0.3)
    p = weather.place()
    assert p and p["name"] == "Rouen" and p["region"] == "Normandie" and p["lat"] == 49.443
    await user.should_see("Seules les coordonnées de la ville sont envoyées à Open-Meteo.com")


def test_the_card_follows_the_interface_language():
    db.kv_set("ui_lang", "en")
    html = ui_weather.card_html(weather.summarize(FORECAST, air=AIR), "Rouen")
    assert "Light rain" in html and "Feels like" in html and "wind 12 km/h" in html and "icons by Meteocons" in html
    assert "Tomorrow" in html and "Wed 21" in html and "Details" in html and "UV index" in html and "37 · good" in html and "Rain today" in html
    db.kv_set("ui_lang", "fr")
    assert "Pluie faible" in ui_weather.card_html(weather.summarize(FORECAST), "Rouen")
