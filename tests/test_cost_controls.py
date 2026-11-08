"""Plafond quotidien de crédits, brief gardé d'un lancement à l'autre, export/import de la configuration."""
import json
import time

import pytest

from mailia import aipolicy, config, db, health, modules, queries


def spend(credits, feature="analyze", model="1min:x"):
    db.record_usage(model, 100, 10, feature, credits)


def test_daily_cap_suspends_the_ai_and_shows_in_the_health_pill(clean_db):
    spend(5000)
    aipolicy.check_credits()  # pas de plafond : rien ne bloque
    db.kv_set("onemin_daily_cap", "10000")
    aipolicy.check_credits()  # 50 % : encore bon
    assert aipolicy.daily_cap_reached() is None and not any("quotidien" in p for p in health.problems())
    spend(5200)
    with pytest.raises(aipolicy.AIError) as e:
        aipolicy.check_credits()
    assert e.value.detail == "cap" and not e.value.fallback  # jamais de repli payant à l'insu de l'utilisateur
    assert any("quotidien" in p for p in health.problems())
    db.kv_set("onemin_daily_cap", "50000")  # plafond relevé : l'IA reprend
    aipolicy.check_credits()
    assert not any("quotidien" in p for p in health.problems())


def test_credit_breakdown_of_today_and_of_the_last_days(clean_db):
    spend(300, "analyze"); spend(700, "brief_structured"); spend(100, "analyze", model="claude-x")  # ce dernier n'est pas 1min.AI
    got = aipolicy.day_credits_by_feature()
    assert [c for _, _, c in got] == [700, 300] and aipolicy.day_credits() == 1000
    days = aipolicy.credits_by_day(14)
    assert len(days) == 14 and days[-1][1] == 1000 and sum(c for _, c in days[:-1]) == 0
    assert "·" in config.machine_label()


def test_brief_is_not_rewritten_within_the_hour_but_is_after(clean_db):
    db.kv_set("brief:headline", json.dumps({"intro": "x", "urgent": [], "waiting": [], "info": ""}))
    db.kv_set("brief:at", str(time.time() - 600))
    assert not queries._brief_due()  # 10 min : trop tôt, même si de nouveaux mails sont arrivés
    db.kv_set("brief:at", str(time.time() - 2 * 3600))
    assert queries._brief_due()
    db.kv_set("brief:at", str(time.time() - 600)); db.kv_set("brief:headline", "")
    assert queries._brief_due()  # rien à afficher : on la fait
    db.kv_set("brief:headline", "x")
    db.kv_set("brief:at", str(time.time() - 25 * 3600))
    assert queries._brief_due()  # un autre jour


def make_module(base, mid, version):
    d = base / mid
    d.mkdir(parents=True)
    (d / "module.json").write_text(json.dumps({"name": mid, "version": version, "api": 1}))
    (d / "module.py").write_text("def register(api):\n    pass\n")
    return d


def test_backup_carries_accounts_settings_and_modules_but_no_secret(clean_db, tmp_path, monkeypatch):
    from mailia import backup
    monkeypatch.setattr(modules, "DIR", tmp_path / "mods")
    monkeypatch.setattr(config, "ACCOUNTS_PATH", tmp_path / "accounts.json")  # sinon les boîtes du test resteraient pour les tests suivants
    monkeypatch.setattr(config, "_acc_cache", (None, []))
    make_module(modules.DIR, "mon_module", "1.0")
    (modules.DIR / "mon_module" / "__pycache__").mkdir()
    (modules.DIR / "mon_module" / "__pycache__" / "a.pyc").write_bytes(b"x")
    db.kv_set("onemin_daily_cap", "1234"); db.kv_set("vip_senders", '["a@b.fr"]'); db.kv_set("module:mon_module:cfg", '{"url": "https://x"}')
    db.kv_set("weather_place", '{"name": "Rouen"}'); db.kv_set("weather_cache", "cache"); db.kv_set("feed_url", "https://exemple.fr/rss");
    db.kv_set("module:mon_module:src", "/chemin/de/l/autre/machine"); db.kv_set("thread_sum:abc", "cache"); db.kv_set("module:odoo:password", "SECRET")
    config.write_accounts([{"key": "perso", "label": "Perso", "email": "a@b.fr", "password": "mdp", "kind": "imap"}])
    data = backup.export_data()
    blob = backup.dumps(data).decode()
    assert [m["id"] for m in data["modules"]] == ["mon_module"] and set(data["modules"][0]["files"]) == {"module.json", "module.py"}
    assert "SECRET" not in blob and "mdp" not in blob and "thread_sum" not in blob and "autre/machine" not in blob
    assert {"onemin_daily_cap", "vip_senders", "module:mon_module:cfg", "weather_place", "feed_url"} <= set(data["kv"]) and "weather_cache" not in data["kv"]  # la ville suit, pas le cache and "1 module(s)" in backup.summary(data)

    # autre machine : rien d'installé ; une boîte déjà présente est conservée
    with db.conn() as c:
        c.execute("DELETE FROM kv")
    config.write_accounts([{"key": "perso", "label": "Déjà là", "kind": "imap"}])
    monkeypatch.setattr(modules, "DIR", tmp_path / "mods2")
    r = backup.import_data(json.loads(blob))
    assert r["modules"] == 1 and r["modules mis à jour"] == 0 and r["boîtes"] == 0
    assert db.kv_get("onemin_daily_cap") == "1234" and config.read_accounts()[0]["label"] == "Déjà là"
    assert (modules.DIR / "mon_module" / "module.py").is_file()
    # un module n'est remplacé que par une version plus récente
    assert backup.import_data(json.loads(blob))["modules mis à jour"] == 0
    (modules.DIR / "mon_module" / "module.json").write_text(json.dumps({"name": "m", "version": "0.5", "api": 1}))
    assert backup.import_data(json.loads(blob))["modules mis à jour"] == 1


def test_an_old_backup_without_modules_still_restores_and_a_hostile_module_is_refused(clean_db, tmp_path, monkeypatch):
    import base64

    from mailia import backup
    monkeypatch.setattr(modules, "DIR", tmp_path / "mods")
    assert backup.import_data({"app": "Velora", "kv": {"theme": "dark"}})["réglages"] == 1  # sauvegarde d'avant les modules
    ok = base64.b64encode(b"{}").decode()
    evil = {"app": "Velora", "kv": {}, "modules": [{"id": "pirate", "version": "1.0", "files": {"module.json": ok, "../../evil.py": ok}}]}
    with pytest.raises(ValueError):
        backup.import_data(evil)
    assert not (tmp_path / "evil.py").exists() and not (modules.DIR / "pirate").exists()
    with pytest.raises(ValueError):
        backup.import_data({"app": "autre", "kv": {}})


def test_every_https_download_uses_the_bundled_certificates(clean_db, monkeypatch):
    """Régression : météo, flux RSS et mise à jour utilisaient le magasin du système, vide dans l'appli Mac (« unable to get local issuer certificate »)."""
    import io
    import ssl
    import urllib.request

    from mailia import feed, updater, weather
    seen = []

    class R(io.BytesIO):
        status = 200
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def fake(req, timeout=None, context=None):
        seen.append(context)
        return R(b'{"x": 1}')
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    weather._get("https://example.org/w", {})
    updater._get("https://example.org/u")
    assert len(seen) == 2 and all(isinstance(c, ssl.SSLContext) for c in seen)
    # le flux passe par l'ouvreur de Velora (certificats embarqués, redirections limitées à https)
    opened = []
    monkeypatch.setattr(urllib.request.OpenerDirector, "open", lambda self, req, data=None, timeout=None: (opened.append(self), R(b"<rss/>"))[1])
    db.kv_set("feed_url", "https://example.org/rss")
    try:
        feed.fetch(force=True)
    except Exception:  # noqa: BLE001 — le contenu n'est pas un flux : seul le chemin réseau nous intéresse
        pass
    assert len(opened) == 1 and any(isinstance(h, urllib.request.HTTPSHandler) for h in opened[0].handlers)


def test_a_lost_connection_is_dropped_so_the_next_cycle_reconnects(clean_db, monkeypatch) -> None:
    """Régression : après la veille du Mac, la connexion Exchange morte faisait échouer tous les cycles jusqu'au redémarrage de Velora."""
    import requests
    from exchangelib.protocol import CachingProtocol

    from mailia import sync
    cleared = []
    monkeypatch.setattr(CachingProtocol, "clear_cache", classmethod(lambda cls: cleared.append(1)))
    monkeypatch.setattr(sync, "_contacts_upkeep", lambda changed: None)
    monkeypatch.setattr(sync, "analyze_pending", lambda: 0)
    monkeypatch.setitem(sync.ACCOUNTS, "boite", type("C", (), {"kind": "ews"})())
    monkeypatch.setitem(sync._providers, "boite", object())
    assert sync.connection_lost(requests.exceptions.ReadTimeout("Read timed out")) and sync.connection_lost(RuntimeError("Reraised from ReadTimeout(…)"))
    assert not sync.connection_lost(RuntimeError("401 Unauthorized"))
    monkeypatch.setattr(sync, "sync_account", lambda key: (_ for _ in ()).throw(requests.exceptions.ReadTimeout("Read timed out")))
    sync._last_run.clear()
    sync.run_cycle(force=True)
    assert "boite" not in sync._providers and cleared  # connexions oubliées
    monkeypatch.setitem(sync._providers, "boite", object())
    monkeypatch.setattr(sync, "sync_account", lambda key: (_ for _ in ()).throw(RuntimeError("401 Unauthorized")))
    sync.run_cycle(force=True)
    assert "boite" in sync._providers  # un refus du serveur n'efface rien
    sync.last_error.clear()
