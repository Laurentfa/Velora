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
    db.close_shared()  # comme si l'appli était fermée : la connexion gardée ouverte ne doit pas survivre au fichier détruit
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


def test_one_connection_is_kept_and_reopened_when_the_file_is_replaced(isolated_db):
    with db.conn() as a:
        pass
    with db.conn() as b:
        assert b is a  # pas de nouvelle connexion à chaque requête (c'était ~30 ms chacune sous Windows)
    db.close_shared()
    db.DB_PATH.replace(isolated_db / "ailleurs.db")  # déplaçable : plus aucun fichier tenu ouvert
    db.DB_PATH.write_bytes(b"")
    (isolated_db / "ailleurs.db").replace(db.DB_PATH)
    with db.conn() as c:
        assert c is not a


def test_failed_use_is_rolled_back_and_the_connection_survives(isolated_db):
    with pytest.raises(ZeroDivisionError):
        with db.conn() as c:
            c.execute("INSERT INTO kv(k,v) VALUES('x','1')")
            1 / 0
    assert db.kv_get("x") is None
    db.kv_set("y", "2")
    assert db.kv_get("y") == "2"


def test_threads_share_the_connection_safely(isolated_db):
    import threading
    errors = []

    def work(n):
        try:
            for i in range(50):
                db.kv_set(f"k{n}", str(i))
                db.kv_get(f"k{n}")
        except Exception as e:
            errors.append(e)
    ts = [threading.Thread(target=work, args=(n,)) for n in range(6)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not errors and db.kv_get("k3") == "49"


def test_settings_are_cached_but_never_stale_after_a_write(isolated_db):
    db.kv_set("a", "1")
    assert db.kv_get("a") == "1"
    with db.conn() as c:  # écriture directe, sans passer par kv_set
        c.execute("UPDATE kv SET v='2' WHERE k='a'")
    assert db.kv_get("a") == "2"
    with db.conn() as c:
        c.execute("DELETE FROM kv WHERE k='a'")
    assert db.kv_get("a") is None
    calls = []
    real = db.conn
    db.kv_get("b")
    db.conn = lambda: calls.append(1) or real()  # une 2e lecture identique ne touche pas la base
    try:
        db.kv_get("b")
    finally:
        db.conn = real
    assert not calls
