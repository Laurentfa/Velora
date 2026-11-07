"""Plafond quotidien de crédits, brief gardé d'un lancement à l'autre, export/import de la configuration."""
import json
import time
import zipfile

import pytest

from mailia import aipolicy, config, configbundle, db, health, modules, queries


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


def test_export_import_round_trip_keeps_secrets_out(clean_db, tmp_path, monkeypatch):
    monkeypatch.setattr(modules, "DIR", tmp_path / "mods")
    monkeypatch.setattr(config, "ACCOUNTS_PATH", tmp_path / "accounts.json")  # sinon les boîtes du test resteraient pour les tests suivants
    monkeypatch.setattr(config, "_acc_cache", (None, []))
    make_module(modules.DIR, "mon_module", "1.0")
    (modules.DIR / "mon_module" / "__pycache__").mkdir()
    (modules.DIR / "mon_module" / "__pycache__" / "a.pyc").write_bytes(b"x")
    db.kv_set("onemin_daily_cap", "1234"); db.kv_set("vip_senders", '["a@b.fr"]'); db.kv_set("module:mon_module:cfg", '{"url": "https://x"}')
    db.kv_set("module:mon_module:src", "/chemin/de/l/autre/machine"); db.kv_set("thread_sum:abc", "cache"); db.kv_set("openai_api_key", "SECRET")
    config.write_accounts([{"key": "perso", "label": "Perso", "email": "a@b.fr", "password": "mdp", "kind": "imap"}])
    out = tmp_path / "conf.zip"
    assert configbundle.export_bundle(out) == {"settings": 3, "accounts": 1, "modules": 1}
    with zipfile.ZipFile(out) as z:
        names = z.namelist()
        blob = z.read("velora-config.json").decode()
    assert "modules/mon_module/module.py" in names and not any("pycache" in n for n in names)
    assert "SECRET" not in blob and "mdp" not in blob and "thread_sum" not in blob and "autre/machine" not in blob

    # autre machine : tout est vide, sauf une boîte et un réglage déjà posés (conservés)
    with db.conn() as c:
        c.execute("DELETE FROM kv")
    config.write_accounts([{"key": "perso", "label": "Déjà là", "kind": "imap"}])
    db.kv_set("vip_senders", '["local@x.fr"]')
    monkeypatch.setattr(modules, "DIR", tmp_path / "mods2")
    r = configbundle.import_bundle(out)
    assert r == {"settings": 2, "accounts": 0, "modules": 1, "updated": 0}
    assert db.kv_get("onemin_daily_cap") == "1234" and db.kv_get("vip_senders") == '["local@x.fr"]'
    assert config.read_accounts()[0]["label"] == "Déjà là" and (modules.DIR / "mon_module" / "module.py").is_file()
    # un module n'est remplacé que par une version plus récente
    assert configbundle.import_bundle(out)["updated"] == 0
    (modules.DIR / "mon_module" / "module.json").write_text(json.dumps({"name": "m", "version": "0.5", "api": 1}))
    assert configbundle.import_bundle(out)["updated"] == 1


def test_import_refuses_anything_that_is_not_a_configuration(clean_db, tmp_path):
    bad = tmp_path / "x.zip"
    bad.write_bytes(b"pas un zip")
    with pytest.raises(ValueError):
        configbundle.import_bundle(bad)
    other = tmp_path / "y.zip"
    with zipfile.ZipFile(other, "w") as z:
        z.writestr("readme.txt", "x")
    with pytest.raises(ValueError):
        configbundle.import_bundle(other)
