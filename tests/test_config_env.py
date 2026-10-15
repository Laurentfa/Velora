import pytest
from dotenv import dotenv_values

from mailia import config

SECRETS = ['simple123', 'ab #cd', 'a"b\'c', 'p\\w$x', 'pre ${HOME} post', 'trail ', '  lead', 'é€ü', "multi\nline", 'x=y', "'quoted'",
           'hash#nospace', "it's", '\\', 'back\\slash\\', '$HOME', 'a b  c', '"dq"', 'tab\there', '=début', '#début']


@pytest.mark.parametrize("pw", SECRETS)
def test_secret_round_trip(pw):
    config.write_env({"PW_ROUNDTRIP": pw, "AUTRE": "valeur 2 #"})
    got = config._read_env_file()
    assert got["PW_ROUNDTRIP"] == pw
    assert got["AUTRE"] == "valeur 2 #"
    assert dotenv_values(config.ENV_PATH, interpolate=False)["PW_ROUNDTRIP"] == pw


def test_unset_and_permissions():
    config.write_env({"PW_TMP": "x y"})
    config.unset_env(["PW_TMP"])
    assert "PW_TMP" not in config._read_env_file()
    import sys
    if not sys.platform.startswith("win"):  # sous Windows, les droits sont gérés par le profil
        assert oct(config.ENV_PATH.stat().st_mode & 0o777) == "0o600"


def test_atomic_write_leaves_no_temp_file(tmp_path):
    target = tmp_path / "f.json"
    config.atomic_write(target, "{}", private=False)
    assert target.read_text() == "{}"
    assert not list(tmp_path.glob("*.tmp"))


def test_accounts_load():
    accts = config.load_accounts()
    assert accts["testbox"].password == "motdepasse-de-test"
    assert accts["noai"].ai_enabled is False
    assert accts["testbox"].tls_insecure is False


def test_user_name_feeds_the_ai_prompts_and_the_default_signature(monkeypatch):
    from mailia import ai, compose, config, db
    monkeypatch.delenv("SIGNATURE", raising=False)
    assert config.user_name() == "" and config.persona("Tu aides ‹‹NOM›› (‹‹PRENOM››).") == "Tu aides l'utilisateur (l'utilisateur)."
    db.kv_set("user_name", "  Marie Dupont ")
    assert config.user_name() == "Marie Dupont" and config.user_first_name() == "Marie"
    assert config.persona("Tu aides ‹‹NOM›› : action de ‹‹PRENOM››.") == "Tu aides Marie Dupont : action de Marie."
    sys_prompt = config.persona(ai.analyze_system())
    assert "Marie Dupont" in sys_prompt and "‹‹" not in sys_prompt and "Laurent" not in sys_prompt
    assert "Marie" in ai.default_text("analyze") and "‹‹" not in ai.default_text("analyze")
    assert "Marie Dupont" in compose.signature_html()  # signature par défaut : le nom de l'utilisateur
    db.kv_set("user_name", "")
    assert compose.signature_html() == "<p></p>"  # pas de nom : rien d'inventé


def test_no_personal_name_left_in_ai_prompts():
    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent / "mailia"
    for f in ("ai.py", "ask.py", "rules.py", "aipolicy.py", "config.py", "api_ui.py"):
        text = (root / f).read_text(encoding="utf-8")
        assert "Laurent" not in text and "fauvel" not in text.lower(), f
