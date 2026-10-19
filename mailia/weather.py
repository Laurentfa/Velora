"""Météo du brief du jour : une carte avec l'icône animée du moment, la température, la pluie, le vent et les prochaines heures.

Données : Open-Meteo.com (gratuit, sans clé ni compte ; licence CC-BY 4.0, usage non commercial : mention « Weather data by Open-Meteo.com » affichée sous la carte).
Seules les coordonnées de la ville choisie sont envoyées. Icônes : Meteocons de Bas Milius (licence MIT, voir mailia/weather_icons/LICENSE-meteocons.txt).
Aucun appel à l'IA. Sans connexion, la dernière météo connue (moins de 6 h) est montrée, sinon la carte est simplement absente."""
from __future__ import annotations

import json
import logging
import time
import urllib.parse
import urllib.request
from pathlib import Path

from . import db

log = logging.getLogger("weather")
ICONS = Path(__file__).parent / "weather_icons"
GEO_URL = "https://geocoding-api.open-meteo.com/v1/search"
API_URL = "https://api.open-meteo.com/v1/forecast"
FRESH = 30 * 60      # secondes : au-delà, on redemande la météo
STALE_OK = 6 * 3600  # hors connexion : on montre la dernière météo connue si elle a moins de 6 h
ATTRIBUTION = "Weather data by Open-Meteo.com"

# code météo de l'OMM (utilisé par Open-Meteo) -> (libellé, icône du jour, icône de la nuit)
CODES = {
    0: ("Ciel dégagé", "clear-day", "clear-night"),
    1: ("Plutôt dégagé", "partly-cloudy-day", "partly-cloudy-night"),
    2: ("Partiellement nuageux", "partly-cloudy-day", "partly-cloudy-night"),
    3: ("Couvert", "overcast-day", "overcast-night"),
    45: ("Brouillard", "fog-day", "fog-night"),
    48: ("Brouillard givrant", "fog-day", "fog-night"),
    51: ("Bruine légère", "drizzle", "drizzle"),
    53: ("Bruine", "drizzle", "drizzle"),
    55: ("Bruine dense", "overcast-drizzle", "overcast-drizzle"),
    56: ("Bruine verglaçante", "sleet", "sleet"),
    57: ("Bruine verglaçante dense", "sleet", "sleet"),
    61: ("Pluie faible", "rain", "rain"),
    63: ("Pluie", "rain", "rain"),
    65: ("Pluie forte", "overcast-rain", "overcast-rain"),
    66: ("Pluie verglaçante", "sleet", "sleet"),
    67: ("Pluie verglaçante forte", "sleet", "sleet"),
    71: ("Neige faible", "snow", "snow"),
    73: ("Neige", "snow", "snow"),
    75: ("Neige forte", "overcast-snow", "overcast-snow"),
    77: ("Grains de neige", "snow", "snow"),
    80: ("Averses", "partly-cloudy-day-rain", "partly-cloudy-night-rain"),
    81: ("Averses", "partly-cloudy-day-rain", "partly-cloudy-night-rain"),
    82: ("Fortes averses", "overcast-rain", "overcast-rain"),
    85: ("Averses de neige", "partly-cloudy-day-snow", "partly-cloudy-night-snow"),
    86: ("Fortes averses de neige", "partly-cloudy-day-snow", "partly-cloudy-night-snow"),
    95: ("Orage", "thunderstorms-day", "thunderstorms-night"),
    96: ("Orage et grêle", "thunderstorms-day-rain", "thunderstorms-night-rain"),
    99: ("Orage et forte grêle", "thunderstorms-day-rain", "thunderstorms-night-rain"),
}


def describe(code: int, is_day: bool = True) -> tuple[str, str]:
    """(libellé, nom du fichier d'icône sans extension) ; un code inconnu donne « cloudy »."""
    label, day, night = CODES.get(int(code), ("Variable", "cloudy", "cloudy"))
    return label, day if is_day else night


# ---------------------------------------------------------------- réglages
def place() -> dict | None:
    """La ville choisie : {"name", "region", "lat", "lon"} ou None."""
    try:
        p = json.loads(db.kv_get("weather_place") or "")
        return p if isinstance(p, dict) and "lat" in p and "lon" in p else None
    except ValueError:
        return None


def set_place(p: dict | None) -> None:
    db.kv_set("weather_place", json.dumps(p, ensure_ascii=False) if p else "")
    db.kv_set("weather_cache", "")


def enabled() -> bool:
    return (db.kv_get("weather_on") or "1") == "1" and place() is not None


# ---------------------------------------------------------------- réseau
def _get(url: str, params: dict, timeout: float = 8.0) -> dict:
    req = urllib.request.Request(url + "?" + urllib.parse.urlencode(params), headers={"User-Agent": "Velora"})
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 — adresses fixes en https
        return json.loads(r.read().decode("utf-8"))


def search(query: str) -> list[dict]:
    """Villes correspondant à `query` (5 au plus) : [{"name", "region", "country", "lat", "lon"}]."""
    q = (query or "").strip()
    if len(q) < 2:
        return []
    d = _get(GEO_URL, {"name": q, "count": 5, "language": "fr", "format": "json"})
    return [{"name": r.get("name", ""), "region": r.get("admin1", ""), "country": r.get("country", ""),
             "lat": round(float(r["latitude"]), 3), "lon": round(float(r["longitude"]), 3)} for r in d.get("results", []) if "latitude" in r]


def fetch(lat: float, lon: float) -> dict:
    return _get(API_URL, {
        "latitude": lat, "longitude": lon, "timezone": "auto", "forecast_days": 2, "wind_speed_unit": "kmh",
        "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m,is_day",
        "hourly": "temperature_2m,weather_code,precipitation_probability,is_day",
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max,sunrise,sunset"})


def summarize(d: dict, hours: int = 6) -> dict:
    """Les chiffres utiles pour la carte, à partir de la réponse d'Open-Meteo."""
    cur = d["current"]
    label, icon = describe(cur["weather_code"], bool(cur.get("is_day", 1)))
    daily = d["daily"]
    now_key = str(cur.get("time", ""))[:13]  # « 2026-10-19T08 » : l'heure locale du lieu
    h = d["hourly"]
    times = h["time"]
    start = next((i for i, t in enumerate(times) if t[:13] >= now_key), 0) + 1  # à partir de l'heure suivante
    nxt = []
    for i in range(start, min(start + hours, len(times))):
        _, ic = describe(h["weather_code"][i], bool(h.get("is_day", [1] * len(times))[i]))
        nxt.append({"hour": times[i][11:13] + " h", "temp": round(h["temperature_2m"][i]), "icon": ic, "rain": h["precipitation_probability"][i] or 0})
    return {"temp": round(cur["temperature_2m"]), "feels": round(cur["apparent_temperature"]), "label": label, "icon": icon,
            "wind": round(cur["wind_speed_10m"]), "tmin": round(daily["temperature_2m_min"][0]), "tmax": round(daily["temperature_2m_max"][0]),
            "rain": daily["precipitation_probability_max"][0] or 0, "sunrise": str(daily["sunrise"][0])[11:16], "sunset": str(daily["sunset"][0])[11:16], "next": nxt}


def report(now: float | None = None) -> dict | None:
    """La météo de la ville choisie, prête à afficher ; None si désactivée, sans ville, ou sans données (hors connexion et rien de récent en mémoire)."""
    if not enabled():
        return None
    p = place()
    now = time.time() if now is None else now
    try:
        c = json.loads(db.kv_get("weather_cache") or "")
    except ValueError:
        c = None
    same = bool(c) and c.get("lat") == p["lat"] and c.get("lon") == p["lon"]
    if same and now - c["at"] < FRESH:
        return c["summary"]
    try:
        s = summarize(fetch(p["lat"], p["lon"]))
    except Exception as e:  # noqa: BLE001 — pas de réseau, service indisponible ou réponse inattendue
        log.info("météo indisponible : %s", e)
        return c["summary"] if same and now - c["at"] < STALE_OK else None
    db.kv_set("weather_cache", json.dumps({"lat": p["lat"], "lon": p["lon"], "at": now, "summary": s}, ensure_ascii=False))
    return s


def place_label(p: dict) -> str:
    return ", ".join(x for x in (p.get("name"), p.get("region")) if x)
