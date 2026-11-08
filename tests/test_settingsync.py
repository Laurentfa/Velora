"""Les consignes de l'IA et le nom suivent la personne d'un ordinateur à l'autre, par la boîte Exchange : fusion, conflits, deux ordinateurs qui convergent."""
import json

import pytest

from mailia import aishare, config, db, settingsync, sync

A_NOW = "2026-11-08T07:00:00Z"


@pytest.mark.parametrize("key,local,base,remote,lt,expect", [
    # premier contact (jamais synchronisé ici)
    ("prompt:analyze", "", None, None, None, ("keep",)),                                   # rien de part ni d'autre
    ("prompt:analyze", "Ma consigne", None, None, None, ("push", A_NOW)),                  # personnalisée ici, rien dans la boîte : on l'envoie
    ("prompt:analyze", "", None, {"v": "Autre", "t": "2026-11-01T00:00:00Z"}, None, ("pull",)),   # défaut ici : on reprend celle de la boîte
    ("prompt:analyze", "Ma consigne", None, {"v": "", "t": "x"}, None, ("push", A_NOW)),   # la boîte n'a que le défaut
    ("prompt:analyze", "Ma consigne", None, {"v": "Autre", "t": "x"}, None, ("conflict",)),  # deux consignes différentes : rien n'est écrasé
    ("user_name", "Laurent Fauvel", None, {"v": "Laurent  FAUVEL", "t": "x"}, None, ("keep",)),  # même personne : majuscules et espaces ignorés
    # déjà synchronisé
    ("prompt:analyze", "A", "A", {"v": "A", "t": "x"}, None, ("keep",)),
    ("prompt:analyze", "B", "A", {"v": "A", "t": "x"}, "2026-11-08T06:00:00Z", ("push", "2026-11-08T06:00:00Z")),   # modifiée ici
    ("prompt:analyze", "A", "A", {"v": "C", "t": "2026-11-08T06:00:00Z"}, None, ("pull",)),                         # modifiée ailleurs
    ("prompt:analyze", "B", "A", {"v": "C", "t": "2026-11-08T06:00:00Z"}, "2026-11-08T06:30:00Z", ("push", "2026-11-08T06:30:00Z")),  # des deux côtés : la plus récente (ici)
    ("prompt:analyze", "B", "A", {"v": "C", "t": "2026-11-08T06:30:00Z"}, "2026-11-08T06:00:00Z", ("pull",)),        # des deux côtés : la plus récente (ailleurs)
    ("prompt:analyze", "C", "A", {"v": "C", "t": "x"}, None, ("keep",)),                                            # modifiée pareil des deux côtés
])
def test_decide(key, local, base, remote, lt, expect) -> None:
    assert settingsync.decide(key, local, base, remote, lt, A_NOW) == expect


class Server:
    """La configuration cachée de la boîte, partagée par tous les ordinateurs."""
    raw: str | None = None
    fail = False

    def read_settings(self):
        if self.fail:
            raise OSError("réseau coupé")
        return self.raw

    def write_settings(self, raw):
        if self.fail:
            raise OSError("réseau coupé")
        self.raw = raw


@pytest.fixture
def world(clean_db, monkeypatch, tmp_path):
    """Une boîte Exchange avec IA, un serveur, et deux ordinateurs qu'on fait parler tour à tour (chacun a sa propre base de réglages)."""
    monkeypatch.setattr(config, "ACCOUNTS_PATH", tmp_path / "accounts.json")
    monkeypatch.setattr(config, "_acc_cache", (None, []))
    config.write_accounts([{"key": "boite", "label": "Test", "kind": "ews", "email": "moi@exemple.fr", "login": "a", "host": "x", "ai": True}])
    srv = Server()
    monkeypatch.setattr(sync, "provider", lambda key: srv)
    clock = {"n": 0}

    def fake_now():
        clock["n"] += 1
        return f"2026-11-08T07:{clock['n'] // 60:02d}:{clock['n'] % 60:02d}Z"
    monkeypatch.setattr(settingsync, "_now", fake_now)
    machines = {"A": {}, "B": {}}

    class W:
        server = srv

        @staticmethod
        def on(name, fn=None):
            """Exécute `fn` (ou une synchro) sur l'ordinateur `name` : sa base de réglages remplace temporairement celle de l'autre."""
            with db.conn() as c:
                saved = dict(c.execute("SELECT k, v FROM kv").fetchall())
                c.execute("DELETE FROM kv")
                for k, v in machines[name].items():
                    c.execute("INSERT INTO kv(k,v) VALUES(?,?)", (k, v))
            try:
                settingsync._last_tick["t"] = 0.0
                return fn() if fn else settingsync.tick()
            finally:
                with db.conn() as c:
                    machines[name] = dict(c.execute("SELECT k, v FROM kv").fetchall())
                    c.execute("DELETE FROM kv")
                    for k, v in saved.items():
                        c.execute("INSERT INTO kv(k,v) VALUES(?,?)", (k, v))

        @staticmethod
        def set(name, key, value):
            W.on(name, lambda: db.kv_set(key, value))

        @staticmethod
        def get(name, key):
            return W.on(name, lambda: db.kv_get(key))
    return W


def test_a_consigne_written_on_one_computer_reaches_the_other(world) -> None:
    world.set("A", "prompt:analyze", "Je veux tout en bref.")
    assert world.on("A")["pushed"] == ["prompt:analyze"]
    assert world.on("B")["pulled"] == ["prompt:analyze"]
    assert world.get("B", "prompt:analyze") == "Je veux tout en bref."
    assert world.on("A") == {"pulled": [], "pushed": [], "conflicts": []} and world.on("B") == {"pulled": [], "pushed": [], "conflicts": []}  # rien de plus à faire


def test_a_change_on_the_other_computer_comes_back(world) -> None:
    world.set("A", "prompt:rules", "Toujours répondre vite.")
    world.on("A"); world.on("B")
    world.set("B", "prompt:rules", "Toujours répondre vite et poliment.")
    assert world.on("B")["pushed"] == ["prompt:rules"]
    assert world.on("A")["pulled"] == ["prompt:rules"]
    assert world.get("A", "prompt:rules") == "Toujours répondre vite et poliment."


def test_simultaneous_edits_converge_without_ping_pong(world) -> None:
    world.set("A", "prompt:analyze", "Version 1")
    world.on("A"); world.on("B")
    world.set("A", "prompt:analyze", "Version de A")
    world.set("B", "prompt:analyze", "Version de B")  # les deux modifient avant de se parler
    for _ in range(4):
        world.on("A"); world.on("B")
    a, b = world.get("A", "prompt:analyze"), world.get("B", "prompt:analyze")
    assert a == b and a in ("Version de A", "Version de B")  # une seule gagne, les deux la partagent
    raw = world.server.raw
    for _ in range(3):  # et ça ne bouge plus
        world.on("A"); world.on("B")
    assert world.server.raw == raw


def test_two_different_personalised_consignes_are_never_overwritten(world) -> None:
    world.set("A", "prompt:analyze", "La consigne de A")
    world.on("A")  # A l'envoie
    world.set("B", "prompt:analyze", "La consigne de B")  # B avait déjà la sienne avant de lire la boîte
    res = world.on("B")
    assert res["conflicts"] == ["prompt:analyze"] and res["pulled"] == []
    assert world.get("B", "prompt:analyze") == "La consigne de B"  # intacte
    assert world.on("B", settingsync.conflicts)["prompt:analyze"]["v"] == "La consigne de A"
    world.on("B", lambda: settingsync.resolve("prompt:analyze", False))  # B garde la sienne : elle part vers la boîte
    world.on("B"); world.on("A")
    assert world.get("A", "prompt:analyze") == "La consigne de B"


def test_a_conflict_can_be_settled_by_taking_the_mailbox_version(world) -> None:
    world.set("A", "prompt:rules", "Règles de A")
    world.on("A")
    world.set("B", "prompt:rules", "Mes règles")
    assert world.on("B")["conflicts"] == ["prompt:rules"]
    world.on("B", lambda: settingsync.resolve("prompt:rules", True))
    assert world.get("B", "prompt:rules") == "Règles de A" and world.on("B", settingsync.conflicts) == {}
    assert world.on("B") == {"pulled": [], "pushed": [], "conflicts": []}  # réglé : plus rien à faire, rien n'est renvoyé


def test_the_name_is_shared_and_both_computers_then_have_the_same_fingerprint(world) -> None:
    world.set("A", "user_name", "Laurent Fauvel")
    world.set("A", "prompt:analyze", "Critères communs")
    world.on("A"); world.on("B")
    assert world.on("B", aishare.fingerprint) == world.on("A", aishare.fingerprint)  # c'est tout l'objet : les analyses peuvent maintenant être reprises


def test_nothing_is_written_when_switched_off_and_errors_never_escape(world) -> None:
    world.set("A", "settingsync_on", "0")
    world.set("A", "prompt:analyze", "Ne partira pas")
    assert world.on("A") is None and world.server.raw is None
    world.set("A", "settingsync_on", "1")
    world.server.fail = True
    assert world.on("A") is None and world.get("A", "ssync:error") == "OSError"  # réseau coupé : on réessaiera, sans exception
    world.server.fail = False
    assert world.on("A")["pushed"] == ["prompt:analyze"] and world.get("A", "ssync:error") == ""


def test_only_the_five_listed_settings_travel(world) -> None:
    world.set("A", "prompt:analyze", "x")
    world.set("A", "ONEMIN_API_KEY", "SECRET")
    world.set("A", "ai_budget_eur", "5")
    world.on("A")
    assert set(json.loads(world.server.raw)["keys"]) == {"prompt:analyze"} and "SECRET" not in world.server.raw
    assert set(settingsync.KEYS) == {"prompt:analyze", "prompt:brief", "prompt:draft", "prompt:rules", "user_name"}


def test_the_account_holding_the_settings_is_chosen_by_address(clean_db, monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(config, "ACCOUNTS_PATH", tmp_path / "accounts.json")
    monkeypatch.setattr(config, "_acc_cache", (None, []))
    config.write_accounts([{"key": "z", "kind": "ews", "email": "zoe@exemple.fr", "ai": True}, {"key": "a", "kind": "ews", "email": "alice@exemple.fr", "ai": True},
                           {"key": "m", "kind": "imap", "email": "0@exemple.fr", "ai": True}])
    assert settingsync.account() == "a"  # la même sur tous les ordinateurs, quel que soit le nom local de la boîte
    config.write_accounts([{"key": "m", "kind": "imap", "email": "0@exemple.fr", "ai": True}])
    assert settingsync.account() is None and settingsync.enabled() is False


def test_ews_stores_the_settings_in_a_hidden_user_configuration() -> None:
    from mailia.providers import ews
    assert ews.EwsProvider.SETTINGS_NAME == "Velora.Settings"
    assert callable(ews.EwsProvider.read_settings) and callable(ews.EwsProvider.write_settings)


async def test_the_settings_page_explains_the_feature_and_offers_a_choice_on_conflict(world, user) -> None:
    from nicegui import ui

    from mailia import ui_aishare
    world.set("A", "prompt:analyze", "La consigne de A")
    world.on("A")
    world.set("B", "prompt:analyze", "La consigne de B")
    world.on("B")  # conflit chez B

    @ui.page("/ssync")
    def page() -> None:
        world.on("B", ui_aishare.follow_settings)

    await user.open("/ssync")
    await user.should_see(marker="ssync-title")
    await user.should_see(marker="ssync-switch")
    await user.should_see(marker="ssync-conflict-prompt:analyze")
    await user.should_see("Activé par défaut")
