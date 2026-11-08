"""Fiches des ordinateurs : crédits de tous les ordinateurs, alerte d'une version restée en arrière."""
import json
from datetime import datetime, timedelta, timezone

from mailia import config, db, fleet, health, settingsync, sync


def _iso(d):
    return d.strftime("%Y-%m-%dT%H:%M:%SZ")


def _fiche(v="1.0.69", cr_day=0, cr_month=0, at=None, day=None, month=None):
    return {"v": v, "os": "PC Windows", "at": _iso(at or datetime.now(timezone.utc)), "day": day or datetime.now().strftime("%Y-%m-%d"),
            "cr_day": cr_day, "month": month or datetime.now().strftime("%Y-%m"), "cr_month": cr_month}


def test_decode_ignores_garbage_and_bounds_values():
    assert fleet.decode(None) == {} and fleet.decode("pas du json") == {} and fleet.decode(json.dumps({"v": 99, "machines": {}})) == {}
    raw = json.dumps({"v": 1, "machines": {"PC": {"v": "1.0.1" * 20, "cr_day": "abc", "cr_month": 10**15, "x": "ignoré"}, "mauvais": 3}})
    out = fleet.decode(raw)
    assert list(out) == ["PC"] and out["PC"]["cr_day"] == 0 and out["PC"]["cr_month"] == 10**10 and len(out["PC"]["v"]) == 20 and "x" not in out["PC"]
    assert fleet.decode(fleet.encode(out)) == out


def test_merge_writes_only_when_useful_and_forgets_old_machines():
    now = datetime.now(timezone.utc)
    mine = _fiche(cr_day=100)
    got, write = fleet.merge({}, "Mac", mine, now, 0.0, 1000.0)
    assert write and got["Mac"] == mine
    same = dict(mine)
    _, write = fleet.merge({"Mac": same}, "Mac", mine, now, 900.0, 1000.0)
    assert not write  # rien n'a changé et la dernière écriture est récente
    _, write = fleet.merge({"Mac": same}, "Mac", mine, now, 0.0, 5000.0)
    assert write  # fiche inchangée mais ancienne : réécrite, c'est ce qui dit « vu récemment »
    changed = _fiche(cr_day=200)
    _, write = fleet.merge({"Mac": same}, "Mac", changed, now, 900.0, 1000.0)
    assert not write  # changé mais écrit il y a moins de dix minutes
    _, write = fleet.merge({"Mac": _fiche(v="1.0.68")}, "Mac", mine, now, 900.0, 1000.0)
    assert write  # nouvelle version : tout de suite
    old = _fiche(at=now - timedelta(days=90))
    got, _ = fleet.merge({"Vieux": old, "PC": _fiche()}, "Mac", mine, now, 0.0, 1000.0)
    assert set(got) == {"Mac", "PC"}


def test_snapshot_sums_machines_and_ignores_yesterday(monkeypatch, clean_db):
    monkeypatch.setattr(config, "machine_label", lambda: "Mac · bureau")
    monkeypatch.setattr(fleet, "own_entry", lambda: _fiche(cr_day=1000, cr_month=5000))
    db.kv_set("fleet:cache", fleet.encode({"PC Windows · pc": _fiche(cr_day=300, cr_month=2000),
                                           "Mac · portable": _fiche(cr_day=999, cr_month=777, day="2020-01-01", month="2020-01")}))
    snap = fleet.snapshot()
    assert snap[0]["me"] and [m["name"] for m in snap] == ["Mac · bureau", "PC Windows · pc", "Mac · portable"]
    assert fleet.totals() == (1300, 7000)  # la fiche d'un autre jour ou d'un autre mois ne compte pas
    from mailia import ui_fleet
    assert "1 300 crédits aujourd'hui" in ui_fleet.summary_text(snap) and "7 000 ce mois-ci" in ui_fleet.summary_text(snap)
    assert ui_fleet.summary_text([dict(m, cr_day=0, cr_month=0) for m in snap]) == ""


def test_outdated_alert_waits_a_day_and_names_the_machine(monkeypatch, clean_db):
    monkeypatch.setattr(config, "machine_label", lambda: "Mac · bureau")
    monkeypatch.setattr(fleet, "own_entry", lambda: _fiche(v="1.0.69"))
    db.kv_set("fleet:cache", fleet.encode({"PC Windows · pc": _fiche(v="1.0.60")}))
    fleet._note_top(fleet._cache() | {"Mac · bureau": _fiche(v="1.0.69")})
    assert fleet.outdated() == []  # la 1.0.69 vient d'apparaître : le PC a le temps de se mettre à jour
    db.kv_set("fleet:top", json.dumps({"v": "1.0.69", "t": _iso(datetime.now(timezone.utc) - timedelta(hours=30))}))
    late = fleet.outdated()
    assert [m["name"] for m in late] == ["PC Windows · pc"]
    msg = fleet.problems()[0]
    assert "PC Windows · pc" in msg and "1.0.60" in msg and "1.0.69" in msg
    db.kv_set("fleet:cache", fleet.encode({"PC Windows · pc": _fiche(v="1.0.60", at=datetime.now(timezone.utc) - timedelta(days=45))}))
    assert fleet.outdated() == []  # un ordinateur qu'on ne voit plus depuis plus d'un mois n'est plus signalé
    db.kv_set("fleet:cache", fleet.encode({"PC Windows · pc": _fiche(v="1.0.69")}))
    assert fleet.outdated() == []


def test_health_lists_late_machines(monkeypatch):
    monkeypatch.setattr(fleet, "problems", lambda: ["« PC » est resté en version 1.0.60"])
    monkeypatch.setattr(sync, "_ai_pause", {"until": 0.0, "fp": "", "why": ""})
    assert any("1.0.60" in p for p in health.problems())


def test_tick_exchanges_cards_with_the_mailbox(monkeypatch, clean_db):
    class Prov:
        def __init__(self): self.blob, self.writes = fleet.encode({"PC Windows · pc": _fiche(v="1.0.69", cr_day=50)}), 0
        def read_blob(self, name): return self.blob
        def write_blob(self, name, raw): self.blob, self.writes = raw, self.writes + 1
    prov = Prov()
    monkeypatch.setattr(settingsync, "enabled", lambda: True)
    monkeypatch.setattr(settingsync, "account", lambda: "exchange")
    monkeypatch.setattr(sync, "provider", lambda k: prov)
    monkeypatch.setattr(config, "machine_label", lambda: "Mac · bureau")
    monkeypatch.setattr(fleet, "own_entry", lambda: _fiche(cr_day=10))
    monkeypatch.setitem(fleet._last, "w", 0.0)
    assert fleet.tick(force=True) and prov.writes == 1
    assert set(fleet.decode(prov.blob)) == {"Mac · bureau", "PC Windows · pc"}
    assert fleet.totals()[0] == 60
    assert not fleet.tick()  # trop tôt : pas de nouvel échange
    monkeypatch.setattr(settingsync, "enabled", lambda: False)
    assert not fleet.tick(force=True)  # synchronisation éteinte : rien n'est lu ni écrit
    monkeypatch.setattr(settingsync, "enabled", lambda: True)
    monkeypatch.setattr(sync, "provider", lambda k: (_ for _ in ()).throw(OSError("hors ligne")))
    assert fleet.tick(force=True) is False and db.kv_get("fleet:error") == "OSError"  # boîte injoignable : pas d'exception


# ---------- fenêtre principale ----------
import asyncio
from nicegui.testing import User
import main as app_main


async def test_credits_counter_shows_every_computer_and_opens_the_detail(user: User, monkeypatch, clean_db) -> None:
    from nicegui import ui
    monkeypatch.setattr(sync, "run_cycle", lambda: None)
    ui.page("/")(app_main.index_page)
    monkeypatch.setattr(config, "machine_label", lambda: "Mac · bureau")
    db.record_usage("1min:claude", 100, 50, "analyze", credits=1234)
    db.kv_set("fleet:cache", fleet.encode({"PC Windows · pc": _fiche(v="1.0.60", cr_day=300, cr_month=2000)}))
    db.kv_set("fleet:top", json.dumps({"v": "1.0.69", "t": _iso(datetime.now(timezone.utc) - timedelta(days=2))}))
    await user.open("/")
    await asyncio.sleep(5.5)  # la première lecture de l'état de santé a lieu à 4 s
    await user.should_see("crédits aujourd'hui")
    user.find(marker="credits-counter").click()
    await user.should_see("Crédits 1min.AI par ordinateur")
    await user.should_see("PC Windows · pc")
    await user.should_see("en retard")


async def test_module_pages_have_their_own_section_only_when_there_are_modules(user: User, monkeypatch, clean_db) -> None:
    from nicegui import ui
    monkeypatch.setattr(sync, "run_cycle", lambda: None)
    monkeypatch.setattr(app_main.modules, "views", {})
    ui.page("/")(app_main.index_page)
    await user.open("/")
    await user.should_not_see("Modules")  # aucun module : pas de section
    monkeypatch.setattr(app_main.modules, "views", {"mod_x_v": {"title": "Ma page de module", "icon": "⚕"}})
    await user.open("/")
    await user.should_see("Ma page de module")
    assert user.find(marker="modules-section").elements
