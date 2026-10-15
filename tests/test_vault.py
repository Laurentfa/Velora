import pytest
from mailia import config, db, vault


class FakeKeyring:
    def __init__(self): self.store = {}
    def get_password(self, s, k): return self.store.get((s, k))
    def set_password(self, s, k, v): self.store[(s, k)] = v
    def delete_password(self, s, k): self.store.pop((s, k), None)


@pytest.fixture
def fake(monkeypatch):
    kr = FakeKeyring()
    monkeypatch.setattr(vault, "_keyring", lambda: kr)
    monkeypatch.setattr(vault, "_key", None)
    yield kr
    if vault.is_encrypted():
        db.exclusive(vault.disable_encryption)
    monkeypatch.setattr(vault, "_key", None)


def test_encrypt_then_decrypt_keeps_data(fake):
    if not vault.available():
        pytest.skip("SQLCipher indisponible")
    db.kv_set("secret", "valeur confidentielle")
    key = db.exclusive(vault.enable_encryption)
    assert vault.is_encrypted() and len(key) == 64
    assert b"valeur confidentielle" not in config.DB_PATH.read_bytes()
    assert db.kv_get("secret") == "valeur confidentielle"
    db.kv_set("apres", "ok")
    db.exclusive(vault.disable_encryption)
    assert not vault.is_encrypted() and db.kv_get("apres") == "ok"
    assert fake.store == {}


def test_missing_key_is_reported_not_silent(fake):
    if not vault.available():
        pytest.skip("SQLCipher indisponible")
    key = db.exclusive(vault.enable_encryption)
    fake.store.clear(); vault._key = None
    with pytest.raises(RuntimeError, match="clé"):
        db.kv_get("x")
    assert vault.restore_key("00" * 32) is False
    assert vault.restore_key(key) is True and db.kv_get("secret") is None


def test_lock_code_and_rate_limit():
    vault._fails.clear()
    vault.set_code(db, "4321")
    assert vault.lock_enabled(db)
    assert vault.check_code(db, "4321")[0] is True
    for _ in range(5):
        assert vault.check_code(db, "0000")[0] is False
    ok, wait = vault.check_code(db, "4321")   # bon code, mais trop d'essais récents : attente imposée
    assert ok is False and wait > 0
    vault._fails.clear(); vault.clear_code(db)
    assert not vault.lock_enabled(db)
