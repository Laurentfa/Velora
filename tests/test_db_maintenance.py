import sqlite3
import time

from mailia import db


def test_wal_indexes_and_version():
    with db.conn() as c:
        assert c.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert c.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
        names = {r[1] for r in c.execute("SELECT * FROM sqlite_master WHERE type='index'")}
    assert {"idx_msg_folder_read", "idx_msg_folder_date", "idx_msg_category", "idx_msg_sender"} <= names
    db.init()  # rejouable sans erreur


def test_backup_rotation_and_consistency():
    db.kv_set("temoin", "valeur")
    paths = []
    for _ in range(4):
        paths.append(db.backup_now(keep=2)); time.sleep(1.1)
    files = sorted(db.backups_dir().glob("mail-*.db"))
    assert len(files) == 2 and files[-1].name == paths[-1].split("/")[-1]
    c = sqlite3.connect(files[-1]); assert c.execute("select v from kv where k='temoin'").fetchone()[0] == "valeur"
    assert not db.backup_due()
    db.kv_set("db_backup", "0"); db.kv_set("db_backup_at", ""); assert not db.backup_due()
