"""Vos ordinateurs se font connaître les uns aux autres, par la boîte Exchange (comme les consignes : voir settingsync).

Chaque copie de Velora y dépose une petite fiche : son nom, sa version, le moment où elle a été vue pour la dernière fois et les crédits 1min.AI qu'elle a dépensés
aujourd'hui et ce mois-ci. Aucun contenu de mail. Les autres copies relisent la fiche, ce qui permet :
  - de voir la consommation de TOUS les ordinateurs dans la fenêtre principale (chaque copie ne connaît que ses propres appels) ;
  - d'être prévenu quand un ordinateur est resté sur une ancienne version alors qu'une plus récente est installée ailleurs depuis plus d'un jour.
Actif en même temps que la synchronisation des réglages (même interrupteur, même boîte)."""
import json
import logging
import time
from datetime import datetime, timedelta, timezone

from . import config, db, settingsync, updater
from .version import APP_VERSION

log = logging.getLogger("fleet")
BLOB = "Velora.Machines"
VERSION = 1
INTERVAL = 300  # secondes entre deux échanges avec la boîte
REFRESH = 1800  # une fiche inchangée est tout de même réécrite toutes les 30 minutes : c'est ce qui dit « vu récemment »
MIN_WRITE = 600  # jamais plus d'une écriture toutes les 10 minutes (sauf changement de version)
FORGET_DAYS = 60  # un ordinateur qu'on n'a plus vu depuis si longtemps n'est plus listé
STALE_HOURS = 24  # une version plus récente doit être installée ailleurs depuis ce temps avant d'alerter
_last = {"t": 0.0, "w": 0.0}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(d: datetime) -> str:
    return d.strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(s: str | None) -> datetime | None:
    try:
        return datetime.strptime(s or "", "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


# ---------- cette installation ----------
def month_credits(month: str | None = None) -> int:
    month = month or datetime.now().strftime("%Y-%m")
    with db.conn() as c:
        return int(c.execute("SELECT SUM(credits) FROM usage WHERE month=? AND model LIKE '1min:%'", (month,)).fetchone()[0] or 0)


def own_entry() -> dict:
    from . import aipolicy
    return {"v": APP_VERSION, "os": config.machine_label().split(" · ")[0], "at": _iso(_now()), "day": datetime.now().strftime("%Y-%m-%d"),
            "cr_day": aipolicy.day_credits(), "month": datetime.now().strftime("%Y-%m"), "cr_month": month_credits()}


def _num(raw: dict, k: str) -> int:
    try:
        return max(0, min(10**10, int(raw.get(k) or 0)))
    except (TypeError, ValueError):
        return 0


def _clean(raw: object) -> dict:
    """Une fiche lue dans la boîte : on ne garde que les champs attendus, bornés (la boîte peut avoir été modifiée)."""
    if not isinstance(raw, dict):
        return {}
    return {"v": str(raw.get("v", ""))[:20], "os": str(raw.get("os", ""))[:20], "at": str(raw.get("at", ""))[:24], "day": str(raw.get("day", ""))[:10],
            "cr_day": _num(raw, "cr_day"), "month": str(raw.get("month", ""))[:7], "cr_month": _num(raw, "cr_month")}


def decode(raw: str | None) -> dict[str, dict]:
    if not raw:
        return {}
    try:
        d = json.loads(raw)
        if not isinstance(d, dict) or d.get("v") != VERSION:
            return {}
        return {str(k)[:120]: _clean(x) for k, x in list((d.get("machines") or {}).items())[:50] if isinstance(x, dict)}
    except (ValueError, AttributeError):
        return {}


def encode(machines: dict[str, dict]) -> str:
    return json.dumps({"v": VERSION, "machines": machines}, ensure_ascii=False, separators=(",", ":"))


# ---------- échange avec la boîte ----------
def _cache() -> dict[str, dict]:
    return decode(db.kv_get("fleet:cache"))


def merge(machines: dict[str, dict], me: str, mine: dict, now: datetime, last_write: float, now_ts: float) -> tuple[dict, bool]:
    """Ajoute notre fiche aux fiches lues, oublie les très anciennes. Rend (fiches, faut-il écrire dans la boîte)."""
    old = machines.get(me)
    changed = (not old or any(old.get(k) != mine[k] for k in ("v", "day", "cr_day", "month", "cr_month")) or now_ts - last_write > REFRESH)
    out = {**machines, me: mine}
    cut = _iso(now - timedelta(days=FORGET_DAYS))
    out = {k: x for k, x in out.items() if k == me or (x.get("at") or "") >= cut}
    write = changed and (not old or now_ts - last_write > MIN_WRITE or old.get("v") != mine["v"])
    return out, write


def tick(force: bool = False) -> bool:
    """Relit les fiches, y met la nôtre à jour si besoin. Rend True si l'échange a eu lieu. Ne lève jamais d'erreur."""
    if not settingsync.enabled() or (not force and time.time() - _last["t"] < INTERVAL):
        return False
    _last["t"] = time.time()
    try:
        from . import sync
        prov = sync.provider(settingsync.account())
        if not hasattr(prov, "read_blob"):
            return False
        machines, write = merge(decode(prov.read_blob(BLOB)), config.machine_label(), own_entry(), _now(), _last["w"], time.time())
        if write:
            prov.write_blob(BLOB, encode(machines))
            _last["w"] = time.time()
        db.kv_set("fleet:cache", encode(machines))
        _note_top(machines)
        db.kv_set("fleet:error", "")
        return True
    except Exception as e:  # noqa: BLE001 — pas de réseau, pas de droit… : on réessaiera au prochain tour
        db.kv_set("fleet:error", type(e).__name__)
        log.info("échange des fiches impossible : %s", type(e).__name__)
        return False


def _note_top(machines: dict[str, dict]) -> None:
    """Retient la plus haute version vue sur vos ordinateurs et depuis quand : une alerte n'a de sens qu'après un délai."""
    top = max((x.get("v", "") for x in machines.values()), key=updater.parse, default="")
    try:
        cur = json.loads(db.kv_get("fleet:top") or "{}")
    except ValueError:
        cur = {}
    if top and (not cur.get("v") or updater.parse(top) > updater.parse(cur["v"])):
        db.kv_set("fleet:top", json.dumps({"v": top, "t": _iso(_now())}))


# ---------- ce qu'on en montre ----------
def snapshot() -> list[dict]:
    """[{name, me, version, os, seen, cr_day, cr_month}] : cet ordinateur en direct, les autres d'après la dernière lecture. Cet ordinateur d'abord, puis du plus gourmand au plus sobre."""
    me = config.machine_label()
    machines = _cache()
    machines[me] = own_entry()
    today, month = datetime.now().strftime("%Y-%m-%d"), datetime.now().strftime("%Y-%m")
    out = [{"name": k, "me": k == me, "version": x.get("v", ""), "os": x.get("os", ""), "seen": _parse_iso(x.get("at")),
            "cr_day": x["cr_day"] if x.get("day") == today else 0, "cr_month": x["cr_month"] if x.get("month") == month else 0} for k, x in machines.items()]
    return sorted(out, key=lambda m: (not m["me"], -m["cr_month"], m["name"]))


def totals() -> tuple[int, int]:
    """(crédits aujourd'hui, ce mois-ci) sur tous les ordinateurs."""
    s = snapshot()
    return sum(m["cr_day"] for m in s), sum(m["cr_month"] for m in s)


def top_version() -> str:
    try:
        return str(json.loads(db.kv_get("fleet:top") or "{}").get("v") or "")
    except ValueError:
        return ""


def outdated() -> list[dict]:
    """Les autres ordinateurs restés sur une version plus ancienne que la plus récente installée ailleurs depuis plus de STALE_HOURS (et vus ce dernier mois)."""
    try:
        top = json.loads(db.kv_get("fleet:top") or "{}")
    except ValueError:
        return []
    since = _parse_iso(top.get("t"))
    if not top.get("v") or not since or _now() - since < timedelta(hours=STALE_HOURS):
        return []
    return [m for m in snapshot() if not m["me"] and m["version"] and updater.parse(m["version"]) < updater.parse(top["v"])
            and m["seen"] and _now() - m["seen"] < timedelta(days=30)]


def ago(when: datetime | None) -> str:
    if not when:
        return "jamais"
    s = int((_now() - when).total_seconds())
    if s < 120:
        return "à l'instant"
    if s < 7200:
        return f"il y a {s // 60} min"
    if s < 172800:
        return f"il y a {s // 3600} h"
    return f"il y a {s // 86400} jours"


def compact(n: int) -> str:
    """12400 -> « 12 400 » ; 1250000 -> « 1,25 M »."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.2f}".rstrip("0").rstrip(".").replace(".", ",") + " M"
    return f"{n:,}".replace(",", " ")


def problems() -> list[str]:
    top = top_version()
    return [f"« {m['name']} » est resté en version {m['version']} (la {top} est installée ailleurs). Vu {ago(m['seen'])} : ouvrez Velora dessus pour qu'il se mette à jour."
            for m in outdated()]
