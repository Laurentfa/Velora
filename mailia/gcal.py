"""Google Agenda : création de rendez-vous (et ajout d'une invitation acceptée) avec l'API Calendar, par OAuth.

Portée demandée : « calendar.events » seulement (créer et modifier des rendez-vous ; pas de lecture des autres agendas ni des contacts).
Il faut un identifiant d'application Google (OAUTH_GOOGLE_CLIENT_ID / OAUTH_GOOGLE_CLIENT_SECRET, de type « application de bureau »)
dont l'API Google Agenda est activée : voir Paramètres › Agenda. Si l'application Google est en mode « test », Google fait expirer
la connexion au bout de 7 jours : il suffit de se reconnecter."""
import logging
from datetime import datetime

import httpx

from . import config, oauth

log = logging.getLogger("gcal")
API = "https://www.googleapis.com/calendar/v3/calendars/primary"


class GCalError(Exception):
    """Message destiné à l'utilisateur."""


def configured() -> bool:
    return oauth.configured("google")


def connected() -> bool:
    return bool(config.env("GCAL_REFRESH_TOKEN"))


def connect(email: str = "", open_browser=None, cancel=None) -> None:
    """Ouvre le navigateur pour autoriser l'accès à l'agenda ; mémorise le jeton de renouvellement (trousseau si activé). Bloquant."""
    kw = {"scope": oauth.CALENDAR_SCOPE + " " + oauth.TASKS_SCOPE, "cancel": cancel}
    if open_browser:
        kw["open_browser"] = open_browser
    packed = oauth.authorize("google", email, **kw)
    config.write_env({"GCAL_REFRESH_TOKEN": oauth.unpack(packed)[1]})


def disconnect() -> None:
    config.forget_env("GCAL_REFRESH_TOKEN")


def _token(scope: str = oauth.CALENDAR_SCOPE) -> str:
    refresh = config.env("GCAL_REFRESH_TOKEN")
    if not refresh:
        raise GCalError("Google Agenda n'est pas connecté (Paramètres › Agenda).")
    try:
        access, new = oauth.refresh_access("google", refresh, scope)
    except oauth.OAuthError as e:
        if scope == oauth.TASKS_SCOPE:
            raise GCalError("Google n'a pas encore l'autorisation de lire vos tâches : reconnectez Google Agenda (Paramètres › Agenda › Se connecter).") from e
        raise GCalError(str(e)) from e
    if new != refresh:
        config.write_env({"GCAL_REFRESH_TOKEN": new})
    return access


def _error(r: httpx.Response) -> GCalError:
    try:
        msg = (r.json().get("error") or {}).get("message") or ""
    except ValueError:
        msg = ""
    if (r.status_code in (401, 403) and "has not been used" in msg) or "accessNotConfigured" in r.text:
        return GCalError("L'API Google Agenda n'est pas activée dans votre projet Google Cloud (voir l'aide, Paramètres › Agenda).")
    if r.status_code == 401:
        return GCalError("Google a refusé l'accès : reconnectez l'agenda (Paramètres › Agenda).")
    if r.status_code == 403:
        return GCalError("Google a refusé l'opération : " + (msg or "autorisation insuffisante") + ".")
    return GCalError(f"Google Agenda a répondu une erreur (code {r.status_code}) : {msg[:200]}")


def _call(method: str, path: str, **kw) -> dict:
    try:
        r = httpx.request(method, API + path, headers={"Authorization": f"Bearer {_token()}"}, timeout=30, **kw)
    except httpx.HTTPError as e:
        raise GCalError(f"Google Agenda injoignable : {type(e).__name__}") from e
    if r.status_code >= 300:
        raise _error(r)
    return r.json() if r.content else {}


def create_event(title: str, start: datetime, end: datetime, location: str = "", body: str = "", tentative: bool = False) -> str:
    """Ajoute un rendez-vous sans invité (personne n'est prévenu). Heures naïves = fuseau de l'ordinateur. Retourne le lien de l'événement."""
    aware = lambda d: (d if d.tzinfo else d.astimezone()).isoformat(timespec="seconds")
    ev = {"summary": title, "start": {"dateTime": aware(start)}, "end": {"dateTime": aware(end)}}
    if location:
        ev["location"] = location
    if body:
        ev["description"] = body
    if tentative:
        ev["status"] = "tentative"
    return _call("POST", "/events", params={"sendUpdates": "none"}, json=ev).get("htmlLink", "")


def list_events(start: datetime, end: datetime) -> list[dict]:
    """Rendez-vous de l'agenda principal entre deux dates (heures locales naïves), séries dépliées. Lecture seule."""
    aware = lambda d: d.astimezone().isoformat(timespec="seconds")
    local = lambda s: datetime.fromisoformat(s).astimezone().replace(tzinfo=None)
    out, token = [], None
    for _ in range(4):  # 4 pages de 250 au plus
        params = {"timeMin": aware(start), "timeMax": aware(end), "singleEvents": "true", "orderBy": "startTime", "maxResults": 250}
        if token:
            params["pageToken"] = token
        d = _call("GET", "/events", params=params)
        for e in d.get("items", []):
            if e.get("status") == "cancelled":
                continue
            s, f = e.get("start") or {}, e.get("end") or {}
            all_day = "date" in s
            try:
                a, b = (datetime.fromisoformat(s["date"]), datetime.fromisoformat(f["date"])) if all_day else (local(s["dateTime"]), local(f["dateTime"]))
            except (KeyError, ValueError):
                continue
            out.append({"title": e.get("summary") or "", "start": a, "end": b, "all_day": all_day, "where": e.get("location") or ""})
        token = d.get("nextPageToken")
        if not token:
            break
    return out


TASKS_API = "https://tasks.googleapis.com/tasks/v1"


def _tasks_call(path: str, **params) -> dict:
    try:
        r = httpx.get(TASKS_API + path, params=params, headers={"Authorization": f"Bearer {_token(oauth.TASKS_SCOPE)}"}, timeout=30)
    except httpx.HTTPError as e:
        raise GCalError(f"Google Tasks injoignable : {type(e).__name__}") from e
    if r.status_code == 403 and ("has not been used" in r.text or "accessNotConfigured" in r.text or "SERVICE_DISABLED" in r.text):
        raise GCalError("L'API Google Tasks n'est pas activée dans votre projet Google Cloud (activez « Google Tasks API »).")
    if r.status_code in (401, 403):
        raise GCalError("Google a refusé la lecture des tâches : reconnectez Google Agenda (Paramètres › Agenda › Se connecter).")
    if r.status_code >= 300:
        raise GCalError(f"Google Tasks a répondu une erreur (code {r.status_code}).")
    return r.json()


def list_tasks() -> list[dict]:
    """Tâches Google non terminées qui ont une échéance (listes de tâches réunies) : [{"title", "due": date}]. Lecture seule."""
    from datetime import date
    out = []
    for lst in _tasks_call("/users/@me/lists", maxResults=20).get("items", []):
        d = _tasks_call(f"/lists/{lst['id']}/tasks", showCompleted="false", showHidden="false", maxResults=100)
        for t in d.get("items", []):
            if t.get("status") == "completed" or not t.get("due"):
                continue
            try:
                out.append({"title": t.get("title") or "", "due": date.fromisoformat(t["due"][:10])})
            except ValueError:
                continue
    return out


def test() -> tuple[bool, str]:
    """Vérifie la connexion : lit l'agenda principal (un seul rendez-vous demandé). (ok, message)"""
    try:
        d = _call("GET", "/events", params={"maxResults": 1})
    except GCalError as e:
        return False, str(e)
    return True, f"Connexion réussie : agenda « {d.get('summary') or 'principal'} »."
