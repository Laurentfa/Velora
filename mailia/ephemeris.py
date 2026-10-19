"""Éphéméride du brief du jour : phase de la lune (calculée, sans connexion), lever et coucher du soleil et durée du jour (repris de la météo déjà chargée).

La phase se calcule avec la durée moyenne du mois lunaire (29,53059 jours) à partir d'une nouvelle lune connue (6 janvier 2000, 18 h 14 UTC) : exact à environ un jour près,
largement suffisant pour « première quartier » ou « pleine lune »."""
from __future__ import annotations

import math
from datetime import datetime, timezone

SYNODIC = 29.530588853
NEW_MOON_REF = datetime(2000, 1, 6, 18, 14, tzinfo=timezone.utc)

# (borne haute de la phase entre 0 et 1, libellé, icône) : 0 = nouvelle lune, 0,5 = pleine lune
PHASES = ((0.03, "Nouvelle lune", "moon-new"), (0.22, "Premier croissant", "moon-waxing-crescent"), (0.28, "Premier quartier", "moon-first-quarter"),
          (0.47, "Lune gibbeuse croissante", "moon-waxing-gibbous"), (0.53, "Pleine lune", "moon-full"), (0.72, "Lune gibbeuse décroissante", "moon-waning-gibbous"),
          (0.78, "Dernier quartier", "moon-last-quarter"), (0.97, "Dernier croissant", "moon-waning-crescent"), (1.01, "Nouvelle lune", "moon-new"))


def moon(now: datetime | None = None) -> dict:
    """{"name", "icon", "pct", "age"} : phase de la lune à `now` (UTC par défaut) ; pct = part éclairée du disque, 0 à 100."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.astimezone()
    age = ((now - NEW_MOON_REF).total_seconds() / 86400) % SYNODIC
    frac = age / SYNODIC
    name, icon = next((n, i) for limit, n, i in PHASES if frac < limit)
    return {"name": name, "icon": icon, "pct": round((1 - math.cos(2 * math.pi * frac)) / 2 * 100), "age": round(age, 1)}


def day_length(sunrise: str, sunset: str) -> str:
    """« 08:24 », « 18:58 » -> « 10 h 34 »."""
    try:
        h1, m1 = map(int, sunrise.split(":"))
        h2, m2 = map(int, sunset.split(":"))
    except ValueError:
        return ""
    minutes = (h2 * 60 + m2) - (h1 * 60 + m1)
    return f"{minutes // 60} h {minutes % 60:02d}" if minutes > 0 else ""
