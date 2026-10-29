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
            out += [{**e, "ref": {**e["ref"], "account": key}} for e in sync.provider(key).calendar_events(start, end)]
    return out


def _exchange_tasks() -> list[dict]:
    from . import sync
    out = []
    for key, cfg in sync.ACCOUNTS.items():
        if cfg.kind == "ews" and cfg.password:
            out += [{**t, "ref": {**t["ref"], "account": key}} for t in sync.provider(key).tasks()]
    return out


def _merge(parts: dict[str, list[dict]]) -> list[dict]:
    seen: dict[tuple, dict] = {}
    for src, events in parts.items():
        for e in events:
            k = (e["title"].strip().lower(), e["start"], e["end"])
            ref = [e["ref"]] if e.get("ref") else []
            if k in seen:
                seen[k]["src"].add(src)
                seen[k]["refs"] += ref
                seen[k]["editable"] = seen[k]["editable"] and e.get("editable", False)
            else:
                seen[k] = {**e, "src": {src}, "refs": ref, "editable": e.get("editable", False)}
    return sorted(seen.values(), key=lambda e: (e["start"], e["end"], e["title"]))


def describe_error(source: str, e: Exception) -> tuple[str, str]:
    """(texte clair en français, genre) pour une panne de source : « timeout » (passager, réessayé tout seul : on ne l'affiche pas), « reauth » (Google demande de se
    reconnecter), « api » (Google Tasks API pas activée) ou « other »."""
    raw = f"{type(e).__name__} {e}"
    low = raw.lower()
    name = "Exchange" if source == "exchange" else ("Google Tasks" if source == "tasks" else "Google Agenda")
    if any(k in low for k in ("readtimeout", "timed out", "max retries", "connectionerror", "injoignable", "connection aborted", "temporarily", "nameresolution")):
        return f"{name} : le serveur n'a pas répondu à temps (nouvel essai automatique).", "timeout"
    if source != "exchange" and any(k in low for k in ("expired or revoked", "invalid_grant", "refusé la connexion", "reconnectez", "refusé la lecture")):
        return "Google : la connexion a expiré ou manque d'une autorisation. Reconnectez-vous.", "reauth"
    if "api google tasks n'est pas activée" in low:
        return "Google Tasks : activez « Google Tasks API » dans votre projet Google Cloud, puis reconnectez Google Agenda.", "api"
    return f"{name} : {str(e)[:160]}", "other"


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
    parts, errors, problems = {}, [], []
    with ThreadPoolExecutor(max_workers=max(1, len(jobs))) as ex:
        futures = {name: ex.submit(fn, start, end) for name, fn in jobs.items()}
    for name, f in futures.items():
        try:
            parts[name] = f.result()
        except Exception as e:  # noqa: BLE001 — une source en panne ne cache pas l'autre
            log.warning("agenda %s : %s", name, e)
            problems.append(describe_error(name, e))
    tasks = _fetch_tasks(problems)
    errors = list(dict.fromkeys(t for t, kind in problems if kind != "timeout"))  # les pannes passagères ne sont pas affichées (journal seulement)
    res = {"events": _merge(parts), "tasks": tasks, "errors": errors, "reauth": any(kind == "reauth" for _, kind in problems), "transient": any(kind == "timeout" for _, kind in problems)}
    if not problems:
        with _lock:
            _cache[key] = (time.monotonic(), res)
    return res


def _fetch_tasks(problems: list) -> list[dict]:
    """Tâches Exchange et Google avec échéance (les deux sources en parallèle ; une panne n'empêche pas l'autre). Une même tâche présente des deux côtés n'apparaît qu'une fois."""
    src = sources()
    jobs = {name: fn for name, fn in (("exchange", _exchange_tasks), ("google", gcal.list_tasks)) if src[name]}
    if not jobs:
        return []
    with ThreadPoolExecutor(max_workers=len(jobs)) as ex:
        futures = {name: ex.submit(fn) for name, fn in jobs.items()}
    seen: dict[tuple, dict] = {}
    for name, f in futures.items():
        try:
            for t in f.result():
                k = (t["title"].strip().lower(), t["due"])
                ref = [t["ref"]] if t.get("ref") else []
                if k in seen:
                    seen[k]["src"].add(name)
                    seen[k]["refs"] += ref
                else:
                    seen[k] = {**t, "src": {name}, "refs": ref}
        except Exception as e:  # noqa: BLE001
            log.warning("tâches %s : %s", name, e)
            problems.append(describe_error("exchange" if name == "exchange" else "tasks", e))
    return sorted(seen.values(), key=lambda t: (t["due"], t["title"]))


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


# ---------- modifier, terminer, supprimer (une opération par agenda où le rendez-vous ou la tâche existe) ----------
def _dry() -> bool:
    from . import sync
    return sync._dry()


def _each(refs: list[dict], ews_fn, google_fn) -> None:
    """Applique l'opération à chaque exemplaire (Exchange et/ou Google) ; la première erreur est remontée après avoir tout essayé."""
    from . import sync
    errors = []
    for r in refs:
        try:
            if r["src"] == "exchange":
                ews_fn(sync.provider(r["account"]), r)
            else:
                google_fn(r)
        except Exception as e:  # noqa: BLE001
            errors.append(str(e))
    invalidate()
    if errors:
        raise RuntimeError(" ; ".join(errors))


def update_event(ev: dict, title: str, start, end, where: str = "") -> None:
    if _dry():
        return log.warning("MODIFICATION D'ÉVÉNEMENT SIMULÉE : %s", title)
    _each(ev["refs"], lambda p, r: p.calendar_update(r["id"], title, start, end, where), lambda r: gcal.update_event(r["id"], title, start, end, where))


def delete_event(ev: dict) -> None:
    if _dry():
        return log.warning("SUPPRESSION D'ÉVÉNEMENT SIMULÉE : %s", ev["title"])
    _each(ev["refs"], lambda p, r: p.calendar_delete(r["id"]), lambda r: gcal.delete_event(r["id"]))


def update_task(t: dict, title: str, due) -> None:
    if _dry():
        return log.warning("MODIFICATION DE TÂCHE SIMULÉE : %s", title)
    _each(t["refs"], lambda p, r: p.task_update(r["id"], title, due), lambda r: gcal.task_update(r, title, due))


def complete_task(t: dict) -> None:
    if _dry():
        return log.warning("TÂCHE TERMINÉE SIMULÉE : %s", t["title"])
    _each(t["refs"], lambda p, r: p.task_complete(r["id"]), lambda r: gcal.task_complete(r))


def delete_task(t: dict) -> None:
    if _dry():
        return log.warning("SUPPRESSION DE TÂCHE SIMULÉE : %s", t["title"])
    _each(t["refs"], lambda p, r: p.task_delete(r["id"]), lambda r: gcal.task_delete(r))
