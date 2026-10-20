"""Agenda en lecture : rendez-vous Exchange (EWS) et Google Agenda réunis pour le panneau du brief du jour.

Les deux sources sont lues en parallèle ; une source en panne n'empêche pas l'autre (l'erreur est rapportée à part). Un rendez-vous présent dans les deux
(invitation acceptée ajoutée aux deux agendas) n'apparaît qu'une fois. Résultat gardé 5 minutes."""
from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta

from . import gcal

log = logging.getLogger("agenda")
TTL = 300
_cache: dict = {}
_lock = threading.Lock()


def week_start(day: date) -> date:
    """Lundi de la semaine affichée (le week-end, on montre la semaine qui vient)."""
    return day - timedelta(days=day.weekday()) + (timedelta(days=7) if day.weekday() >= 5 else timedelta())


def sources() -> dict[str, bool]:
    """Agendas lisibles sur ce poste."""
    from . import sync
    ews = any(c.kind == "ews" and c.password for c in sync.ACCOUNTS.values())
    return {"exchange": ews, "google": gcal.connected()}


def _exchange(start: datetime, end: datetime) -> list[dict]:
    from . import sync
    out = []
    for key, cfg in sync.ACCOUNTS.items():
        if cfg.kind == "ews" and cfg.password:
            out += sync.provider(key).calendar_events(start, end)
    return out


def _merge(parts: dict[str, list[dict]]) -> list[dict]:
    seen: dict[tuple, dict] = {}
    for src, events in parts.items():
        for e in events:
            k = (e["title"].strip().lower(), e["start"], e["end"])
            if k in seen:
                seen[k]["src"].add(src)
            else:
                seen[k] = {**e, "src": {src}}
    return sorted(seen.values(), key=lambda e: (e["start"], e["end"], e["title"]))


def fetch(first: date, days: int = 7, force: bool = False) -> dict:
    """{"events": [...], "errors": [...]} pour les `days` jours à partir de `first`. Bloquant : à lancer hors de l'interface."""
    key = (first, days)
    with _lock:
        hit = _cache.get(key)
    if hit and not force and time.monotonic() - hit[0] < TTL:
        return hit[1]
    start = datetime(first.year, first.month, first.day)
    end = start + timedelta(days=days)
    jobs = {name: fn for name, fn in (("exchange", _exchange), ("google", gcal.list_events)) if sources()[name]}
    parts, errors = {}, []
    with ThreadPoolExecutor(max_workers=max(1, len(jobs))) as ex:
        futures = {name: ex.submit(fn, start, end) for name, fn in jobs.items()}
    for name, f in futures.items():
        try:
            parts[name] = f.result()
        except Exception as e:  # noqa: BLE001 — une source en panne ne cache pas l'autre
            log.warning("agenda %s : %s", name, e)
            errors.append(f"{'Exchange' if name == 'exchange' else 'Google Agenda'} : {e}")
    res = {"events": _merge(parts), "errors": errors}
    if not errors:
        with _lock:
            _cache[key] = (time.monotonic(), res)
    return res


def invalidate() -> None:
    with _lock:
        _cache.clear()


def lanes(events: list[dict]) -> list[tuple[dict, int, int]]:
    """Place les rendez-vous d'une même journée côte à côte quand ils se chevauchent : (rendez-vous, colonne, nombre de colonnes du groupe)."""
    placed: list[tuple[dict, int]] = []
    groups: list[list[int]] = []
    ends: list[datetime] = []
    cur_end = None
    for e in sorted(events, key=lambda e: (e["start"], e["end"])):
        if cur_end is None or e["start"] >= cur_end:
            groups.append([])
            ends = []
        col = next((i for i, x in enumerate(ends) if x <= e["start"]), len(ends))
        if col == len(ends):
            ends.append(e["end"])
        else:
            ends[col] = e["end"]
        cur_end = max(cur_end, e["end"]) if cur_end and e["start"] < cur_end else e["end"]
        groups[-1].append(len(placed))
        placed.append((e, col))
    out = []
    for g in groups:
        n = max(placed[i][1] for i in g) + 1
        out += [(placed[i][0], placed[i][1], n) for i in g]
    return out
