"""Corrections issues de l'audit de sécurité du 8 novembre 2026."""
import base64
import json

import pytest

from mailia import autoconfig, backup, config, db, modules, safexml, scan, settingsync


def hostile_backup(tmp_path, monkeypatch):
    monkeypatch.setattr(modules, "DIR", tmp_path / "mods")
    monkeypatch.setattr(config, "ACCOUNTS_PATH", tmp_path / "accounts.json")
    monkeypatch.setattr(config, "_acc_cache", (None, []))
    code = base64.b64encode(b"def register(api):\n    open('/tmp/pwned','w').write('x')\n").decode()
    return {"app": "Velora", "kv": {"modules_on": "1", "modules_enabled": '["pirate"]', "theme": "dark"},
            "accounts": [{"key": "evil", "label": "Piège", "kind": "imap", "email": "a@b.fr", "host": "evil.example", "tls_insecure": True, "password": "x", "oauth_token": "y"}],
            "modules": [{"id": "pirate", "version": "1.0", "files": {"module.json": base64.b64encode(b'{"name":"p","version":"1.0","api":1}').decode(), "module.py": code}}]}


def test_a_restored_backup_never_enables_a_module_nor_turns_off_certificate_checks(clean_db, tmp_path, monkeypatch) -> None:
    """Un fichier de sauvegarde piégé ne doit pas pouvoir faire exécuter du code (modules) ni désactiver la vérification des certificats d'une boîte."""
    data = hostile_backup(tmp_path, monkeypatch)
    res = backup.import_data(data)
    assert res["modules"] == 1 and (modules.DIR / "pirate" / "module.py").is_file()  # le module est copié…
    assert db.kv_get("modules_on") is None and db.kv_get("modules_enabled") is None  # …mais jamais activé : c'est à l'utilisateur de le faire
    assert modules.enabled_ids() == set() and not modules.master_on()
    acc = next(a for a in config.read_accounts() if a["key"] == "evil")
    assert "tls_insecure" not in acc and "password" not in acc and "oauth_token" not in acc  # liste fermée de réglages de boîte
    assert db.kv_get("theme") == "dark"  # les réglages ordinaires passent


def test_an_export_never_carries_the_module_switches(clean_db, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(modules, "DIR", tmp_path / "mods")
    db.kv_set("modules_on", "1")
    db.kv_set("modules_enabled", '["x"]')
    db.kv_set("modules_repo", "moi/depot")
    kv = backup.export_data()["kv"]
    assert "modules_on" not in kv and "modules_enabled" not in kv and kv["modules_repo"] == "moi/depot"


def test_xml_with_entity_declarations_is_refused_everywhere() -> None:
    bomb = b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;">]><x>&b;</x>'
    with pytest.raises(safexml.ParseError):
        safexml.fromstring(bomb)
    assert autoconfig.parse_autoconfig(bomb) is None
    assert safexml.fromstring(b"<a><b>ok</b></a>").find("b").text == "ok"
    assert scan.safexml is safexml  # le scanner passe par le même lecteur


def test_values_read_from_the_mailbox_are_capped(clean_db) -> None:
    raw = json.dumps({"v": 1, "keys": {"prompt:analyze": {"v": "x" * 500_000, "t": "t" * 500}, "user_name": {"v": "N" * 5000, "t": "t"}, "autre": {"v": "x", "t": "t"}}})
    got = settingsync.decode(raw)
    assert len(got["prompt:analyze"]["v"]) == 20000 and len(got["prompt:analyze"]["t"]) == 32 and len(got["user_name"]["v"]) == 200
    assert "autre" not in got  # seuls les cinq réglages prévus


def test_saving_an_attachment_reports_a_refused_write_instead_of_failing_silently(clean_db, monkeypatch, tmp_path) -> None:
    """Régression vue sur un Mac : « Operation not permitted » dans Téléchargements finissait dans le journal, sans un mot à l'écran."""
    from pathlib import Path

    from mailia import ui_files
    notes = []
    monkeypatch.setattr(ui_files.ui, "notify", lambda *a, **k: notes.append((a, k)))
    monkeypatch.setattr(ui_files, "SERVER_MODE", False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    real_write = Path.write_bytes

    def refuse(self, data):
        if self.name == "contrat.pdf":
            raise PermissionError(1, "Operation not permitted")
        return real_write(self, data)
    monkeypatch.setattr(Path, "write_bytes", refuse)
    ui_files.FilesMixin._save_download(object(), "contrat.pdf", b"%PDF")
    assert len(notes) == 1 and notes[0][1]["type"] == "negative" and "Téléchargements" in notes[0][0][0] and "Confidentialité" in notes[0][0][0]
    assert not (tmp_path / "Downloads" / "contrat.pdf").exists()
    notes.clear()
    ui_files.FilesMixin._save_download(object(), "ok.pdf", b"%PDF")  # un enregistrement normal marche toujours
    assert (tmp_path / "Downloads" / "ok.pdf").read_bytes() == b"%PDF" and notes[0][1]["type"] == "positive"


def test_saved_attachments_are_marked_as_downloaded(monkeypatch, tmp_path) -> None:
    """Comme un navigateur : le système avertit avant d'ouvrir un programme ou un document risque venu d'un mail."""
    import subprocess
    import sys

    from mailia import ui_common
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **k: calls.append(cmd))
    monkeypatch.setattr(sys, "platform", "darwin")
    f = tmp_path / "piece.pdf"
    f.write_bytes(b"x")
    ui_common.mark_downloaded(f)
    assert calls and calls[0][:3] == ["xattr", "-w", "com.apple.quarantine"] and calls[0][-1] == str(f)


@pytest.mark.skipif(__import__("sys").platform == "win32", reason="droits POSIX")
def test_data_files_are_private_to_the_user(clean_db, tmp_path, monkeypatch) -> None:
    """La base, les boîtes et leurs fichiers temporaires ne sont lisibles que par vous (avant : lisibles par tous, protégés seulement par le dossier)."""
    import os
    # un dossier de données à part : on ne touche jamais aux fichiers du test en cours
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "mail.db")
    monkeypatch.setattr(config, "ENV_PATH", tmp_path / ".env")
    names = ("mail.db", "mail.db-wal", "mail.db-shm", "accounts.json", ".env")
    for n in names:  # des fichiers déjà là, lisibles par tous (installation d'une version précédente)
        (tmp_path / n).write_text("x")
        (tmp_path / n).chmod(0o644)
    config.harden_permissions()
    for n in names:
        assert (os.stat(tmp_path / n).st_mode & 0o777) == 0o600, n
    # un fichier de boîtes réécrit par Velora est créé privé
    monkeypatch.setattr(config, "ACCOUNTS_PATH", tmp_path / "boites.json")
    monkeypatch.setattr(config, "_acc_cache", (None, []))
    config.write_accounts([{"key": "x", "label": "X", "kind": "imap", "email": "a@b.fr"}])
    assert (os.stat(tmp_path / "boites.json").st_mode & 0o777) == 0o600
