import os

import pytest

from mailia import config


class FakeKeyring:
    def __init__(self): self.store = {}
    def get_password(self, s, k): return self.store.get((s, k))
    def set_password(self, s, k, v): self.store[(s, k)] = v
    def delete_password(self, s, k): self.store.pop((s, k), None)


@pytest.fixture
def kr(monkeypatch):
    fake = FakeKeyring()
    monkeypatch.setattr(config, "_keyring", lambda: fake)
    for k in ("USE_KEYCHAIN", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    config.unset_env(["USE_KEYCHAIN", "ANTHROPIC_API_KEY"])
    yield fake
    config.unset_env(["USE_KEYCHAIN", "ANTHROPIC_API_KEY"])


def test_keychain_available_probe(kr):
    assert config.keychain_available() is True
    assert not kr.store  # l'élément d'essai est effacé


def test_api_key_goes_to_keychain_when_enabled(kr):
    config.write_env({"USE_KEYCHAIN": "1"})
    config.write_env({"ANTHROPIC_API_KEY": "sk-ant-secret #1"})
    assert os.environ["ANTHROPIC_API_KEY"] == "sk-ant-secret #1"
    assert "ANTHROPIC_API_KEY" not in config._read_env_file()          # plus dans le fichier
    assert kr.store[(config.KEYCHAIN_SERVICE, "env:ANTHROPIC_API_KEY")] == "sk-ant-secret #1"
    del os.environ["ANTHROPIC_API_KEY"]
    config.hydrate_secrets()
    assert os.environ["ANTHROPIC_API_KEY"] == "sk-ant-secret #1"        # relue au démarrage


def test_migration_both_directions(kr):
    config.write_env({"ANTHROPIC_API_KEY": "sk-ant-abc", "PW_TESTBOX": "motdepasse-de-test"})
    moved, errors = config.use_keychain(True)
    assert not errors and moved >= 2
    assert "ANTHROPIC_API_KEY" not in config._read_env_file() and "PW_TESTBOX" not in config._read_env_file()
    assert config.get_password("testbox") == "motdepasse-de-test"
    moved, errors = config.use_keychain(False)
    assert not errors
    assert config._read_env_file()["ANTHROPIC_API_KEY"] == "sk-ant-abc"
    assert config._read_env_file()["PW_TESTBOX"] == "motdepasse-de-test"
    assert not kr.store
    config.write_env({"PW_TESTBOX": "motdepasse-de-test"})


def test_keychain_failure_keeps_secret_in_file(kr, monkeypatch):
    config.write_env({"USE_KEYCHAIN": "1"})
    def boom(*a, **k): raise RuntimeError("trousseau verrouillé")
    monkeypatch.setattr(kr, "set_password", boom)
    config.write_env({"ANTHROPIC_API_KEY": "sk-ant-keep"})
    assert config._read_env_file()["ANTHROPIC_API_KEY"] == "sk-ant-keep"   # jamais perdu
