"""Surveillance des avis de sécurité : liste des composants, requêtes (rien que des noms et des versions), résultat, cadence hebdomadaire, avis écartés. Aucun accès réseau."""
import json
import time

import pytest

from mailia import db, modules, vulnwatch


@pytest.fixture
def lock(tmp_path, monkeypatch):
    f = tmp_path / "requirements.lock"
    f.write_text("# commentaire\nPillow==13.0.0\npypdf==6.21.4  # audit\nsome_pkg.name==1.2.3 ; sys_platform == 'win32'\nnicegui>=3\n\n", encoding="utf-8")
    monkeypatch.setattr(vulnwatch, "lock_path", lambda: f)
    monkeypatch.setattr(modules, "DIR", tmp_path / "modules")
    return f


def test_components_come_from_the_lock_file_and_from_the_modules(lock, tmp_path) -> None:
    assert vulnwatch.python_components() == [{"ecosystem": "PyPI", "name": "pillow", "version": "13.0.0"}, {"ecosystem": "PyPI", "name": "pypdf", "version": "6.21.4"},
                                             {"ecosystem": "PyPI", "name": "some-pkg-name", "version": "1.2.3"}]  # noms normalisés, repères de plateforme et commentaires ignorés
    d = tmp_path / "modules" / "pdf"
    d.mkdir(parents=True)
    (d / "components.json").write_text(json.dumps({"components": [{"ecosystem": "npm", "name": "pdfjs-dist", "version": "4.10.38"}, {"ecosystem": "npm", "name": "x"},
                                                                  {"ecosystem": "inconnu", "name": "y", "version": "1"}, "n'importe quoi"]}), encoding="utf-8")
    assert {"ecosystem": "npm", "name": "pdfjs-dist", "version": "4.10.38", "from": "pdf"} in vulnwatch.components()
    assert len(vulnwatch.module_components()) == 1  # les entrées incomplètes ou d'un écosystème inconnu sont ignorées
    (d / "components.json").write_text("pas du json", encoding="utf-8")
    assert vulnwatch.module_components() == []


def test_without_a_lock_file_the_installed_versions_are_used(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(vulnwatch, "lock_path", lambda: tmp_path / "absent.lock")
    names = {c["name"] for c in vulnwatch.python_components()}
    assert "pytest" in names or "nicegui" in names


class FakeOSV:
    def __init__(self):
        self.calls = []

    def __call__(self, method, url, body=None, timeout=25):
        self.calls.append((method, url, body))
        if url.endswith("/querybatch"):
            return {"results": [{"vulns": [{"id": "GHSA-aaa"}, {"id": "PYSEC-bbb"}]} if (q["package"]["name"], q["version"]) == ("pillow", "12.3.0") else {} for q in body["queries"]]}
        if url.endswith("/vulns/GHSA-aaa"):
            return {"id": "GHSA-aaa", "aliases": ["CVE-2026-1"], "summary": "Dépassement de mémoire", "database_specific": {"severity": "CRITICAL"},
                    "affected": [{"package": {"name": "Pillow"}, "ranges": [{"events": [{"introduced": "0"}, {"fixed": "13.0.0"}, {"fixed": "12.9.1"}]}]}]}
        if url.endswith("/vulns/PYSEC-bbb"):
            return {"id": "PYSEC-bbb", "summary": "Autre avis", "affected": [{"package": {"name": "pillow"}, "ranges": [{"events": [{"fixed": "12.4.0"}]}]}]}
        raise OSError("inattendu")


def test_scan_sends_only_names_and_versions_and_ranks_by_severity(lock) -> None:
    osv = FakeOSV()
    issues = vulnwatch.scan([{"ecosystem": "PyPI", "name": "pillow", "version": "12.3.0"}, {"ecosystem": "PyPI", "name": "pypdf", "version": "6.21.4"}], osv)
    batch = [c for c in osv.calls if c[1].endswith("/querybatch")][0][2]
    assert batch == {"queries": [{"package": {"name": "pillow", "ecosystem": "PyPI"}, "version": "12.3.0"}, {"package": {"name": "pypdf", "ecosystem": "PyPI"}, "version": "6.21.4"}]}  # rien d'autre
    assert [i["id"] for i in issues] == ["GHSA-aaa", "PYSEC-bbb"]  # le plus grave d'abord
    assert issues[0]["severity"] == "CRITICAL" and issues[0]["fixed"] == "13.0.0" and issues[0]["aliases"] == ["CVE-2026-1"] and issues[1]["severity"] == "" and issues[1]["fixed"] == "12.4.0"


def test_check_stores_the_result_once_a_week_and_never_raises(lock, clean_db) -> None:
    osv = FakeOSV()
    res = vulnwatch.check(http=osv)
    assert res["issues"] == [] and res["n"] == 3 and res["error"] == ""  # aucun des composants du verrou n'a d'avis dans ce faux service
    assert vulnwatch.check(http=osv) is None  # trop tôt : une fois par semaine
    assert vulnwatch.check(force=True, http=osv) is not None
    db.kv_set(vulnwatch.KV_ON, "0")
    assert vulnwatch.check(http=osv) is None and vulnwatch.check(force=True, http=osv) is not None  # désactivé : seulement sur demande

    def down(*a, **k):
        raise OSError("pas de réseau")
    db.kv_set(vulnwatch.KV_ON, "1")
    r = vulnwatch.check(force=True, http=down)
    assert r["error"] == "OSError" and r["issues"] == [] and vulnwatch.last_result()["error"] == "OSError"
    assert vulnwatch.check(http=osv) is not None  # après un échec, on réessaie sans attendre une semaine


def test_dismissed_advisories_leave_the_open_list(clean_db) -> None:
    db.kv_set(vulnwatch.KV_LAST, json.dumps({"at": "2026-11-22T10:00:00+00:00", "at_ts": time.time(), "n": 1, "error": "", "issues": [{"id": "A", "severity": "HIGH"}, {"id": "B", "severity": "LOW"}]}))
    assert [i["id"] for i in vulnwatch.open_issues()] == ["A", "B"]
    vulnwatch.dismiss("A")
    assert [i["id"] for i in vulnwatch.open_issues()] == ["B"]
    vulnwatch.dismiss("A", False)
    assert len(vulnwatch.open_issues()) == 2
