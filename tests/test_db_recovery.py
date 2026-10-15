"""Base abîmée : réparation automatique au démarrage (copie la plus récente valide, sinon base vide), sans jamais supprimer le fichier abîmé."""
from pathlib import Path
import sqlite3

import pytest

from mailia import config, db


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "mail.db")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    db.init()
    return tmp_path


def corrupt(path):
    for suffix in ("-wal", "-shm"):
        path.with_name(path.name + suffix).unlink(missing_ok=True)
    path.write_bytes(b"SQLite format 3\x00" + b"\xff" * 8000)  # l'en-tête est intact (ce n'est pas une base chiffrée), les pages sont détruites


def test_corrupted_file_is_detected_as_corruption(isolated_db):
    corrupt(db.DB_PATH)
    with pytest.raises(Exception) as err:
        db.init()
    assert db.is_corruption(err.value)
    assert not db.is_corruption(RuntimeError("clé introuvable"))  # une clé absente n'est pas une base abîmée


def test_recover_restores_latest_valid_backup_and_keeps_the_damaged_file(isolated_db):
    db.kv_set("user_name", "Marie Dupont")
    old = db.backup_now()
    db.kv_set("user_name", "Après la sauvegarde")
    (isolated_db / "backups" / "mail-29990101-000000.db").write_bytes(b"copie elle-meme abimee")  # la plus récente est inutilisable
    corrupt(db.DB_PATH)
    outcome = db.recover()
    assert outcome == f"sauvegarde {Path(old).name}"  # on saute la copie abîmée et on prend la précédente
    db.init()
    assert db.kv_get("user_name") == "Marie Dupont"
    assert list(isolated_db.glob("mail.db.abimee-*"))  # le fichier abîmé est conservé


def test_recover_without_any_backup_starts_empty(isolated_db):
    db.kv_set("user_name", "Perdu")
    corrupt(db.DB_PATH)
    assert db.recover() == "vide"
    db.init()
    assert db.kv_get("user_name") in (None, "")
    sqlite3.connect(db.DB_PATH).execute("select count(*) from messages").fetchone()  # la nouvelle base est utilisable
