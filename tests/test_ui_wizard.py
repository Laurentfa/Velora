"""Assistant d'ajout d'une boîte (et de premier lancement) : parcours dans l'interface, sans réseau."""
from nicegui import ui
from nicegui.testing import User

from mailia import autoconfig, config, wizard
from mailia.ui_account import friendly_error


def wizard_page(first: bool = True):
    @ui.page("/wizard")
    async def page() -> None:
        await wizard.run_wizard(ui.element("div"), first, friendly_error)


async def test_first_launch_welcome_screen(user: User) -> None:
    wizard_page()
    await user.open("/wizard")
    await user.should_see("Bienvenue dans Velora")
    await user.should_see("Adresse e-mail")
    await user.should_see("Plus tard")


async def test_incomplete_address_is_refused(user: User) -> None:
    wizard_page()
    await user.open("/wizard")
    user.find("Adresse e-mail").type("pas-une-adresse")
    user.find("Continuer").click()
    await user.should_see("Saisissez une adresse e-mail complète.")


async def test_address_moves_on_to_connection_and_keeps_the_name(user: User, monkeypatch) -> None:
    monkeypatch.setattr(autoconfig, "detect", lambda addr: autoconfig._result(
        None, found=True, source="test", name="Serveur de test", host="imap.exemple.test", smtp_host="smtp.exemple.test"))
    wizard_page()
    await user.open("/wizard")
    user.find("Votre nom").type("Marie Dupont")
    user.find("Adresse e-mail").type("marie@exemple.test")
    user.find("Continuer").click()
    await user.should_see("Connexion à Serveur de test")
    assert config.user_name() == "Marie Dupont"


async def test_already_configured_address_is_refused(user: User) -> None:
    wizard_page(first=False)
    await user.open("/wizard")
    user.find("Adresse e-mail").type("moi@example.org")  # la boîte de test existe déjà
    user.find("Continuer").click()
    await user.should_see("Cette adresse est déjà configurée.")


def sandbox(monkeypatch):
    """Rien n'est écrit pour de bon : on relève ce que l'assistant enregistrerait."""
    from mailia import ai, sync
    saved = {"accounts": [], "env": []}
    monkeypatch.setattr(config, "save_account", lambda data, pw: saved["accounts"].append((data, pw)))
    monkeypatch.setattr(config, "write_env", lambda env: saved["env"].append(dict(env)))
    monkeypatch.setattr(config, "keychain_available", lambda: False)
    monkeypatch.setattr(sync, "reload", lambda: None)
    monkeypatch.setattr(ai, "reset_client", lambda: None)
    monkeypatch.setattr(autoconfig, "detect", lambda addr: autoconfig._result(
        None, found=True, source="test", name="Serveur de test", host="imap.exemple.test", smtp_host="smtp.exemple.test"))
    return saved


async def test_full_first_launch_flow_saves_account_and_ai_settings(user: User, monkeypatch) -> None:
    from mailia import sync
    saved = sandbox(monkeypatch)
    monkeypatch.setattr(sync, "test_account", lambda cfg: "Connexion réussie : 12 messages")
    wizard_page()
    await user.open("/wizard")
    user.find("Votre nom").type("Marie Dupont")
    user.find("Adresse e-mail").type("marie@exemple.test")
    user.find(marker="wiz-detect").click()
    await user.should_see("Connexion à Serveur de test")
    user.find(marker="wiz-test").click()  # sans mot de passe : refusé
    await user.should_see("Indiquez le mot de passe.")
    user.find("Mot de passe").type("secret-de-test")
    user.find(marker="wiz-test").click()
    await user.should_see("✓ Connexion réussie")
    user.find(marker="wiz-next").click()
    await user.should_see("Intelligence artificielle (facultative)")
    user.find("Clé API Claude").type("sk-ant-test")
    user.find(marker="wiz-ai-next").click()
    await user.should_see("Tout est prêt")
    user.find(marker="wiz-done").click()
    await user.should_see("Boîte ajoutée")
    (data, password), = saved["accounts"]
    assert data["email"] == "marie@exemple.test" and data["host"] == "imap.exemple.test" and data["kind"] == "imap"
    assert password == "secret-de-test"
    env = {k: v for e in saved["env"] for k, v in e.items()}
    assert env["AI_ENABLED"] == "1" and env["ANTHROPIC_API_KEY"] == "sk-ant-test"


async def test_failed_connection_test_blocks_the_next_step(user: User, monkeypatch) -> None:
    from mailia import sync

    def refuse(cfg):
        raise OSError("serveur injoignable")
    saved = sandbox(monkeypatch)
    monkeypatch.setattr(sync, "test_account", refuse)
    wizard_page()
    await user.open("/wizard")
    user.find("Adresse e-mail").type("marie@exemple.test")
    user.find(marker="wiz-detect").click()
    await user.should_see("Connexion à Serveur de test")
    user.find("Mot de passe").type("mauvais")
    user.find(marker="wiz-test").click()
    await user.should_see("✗")
    (nxt,) = user.find(marker="wiz-next").elements
    assert "disable" in nxt.props  # on ne peut pas continuer tant que la connexion n'a pas réussi
    assert not saved["accounts"]


def test_wizard_pure_helpers():
    st = {"email": " marie@exemple.test ", "label": "", "kind": "imap", "login": "marie@exemple.test", "host": " imap.x ", "port": 993, "tls": "ssl",
          "smtp_host": "smtp.x", "smtp_port": 587, "tls_insecure": False, "password": "p", "ai": True, "api_key": " sk-1 ", "onemin_key": "", "provider": "anthropic"}
    data = wizard.account_data(st)
    assert data["label"] == "marie@exemple.test"  # sans nom affiché, l'adresse (nettoyée) sert de nom
    assert data["login"] == "" and data["host"] == "imap.x" and data["smtp_host"] == "smtp.x"  # identifiant = adresse : rien de spécial à retenir
    assert wizard.ai_env(st) == {"AI_ENABLED": "1", "ANTHROPIC_API_KEY": "sk-1", "AI_PROVIDER": "anthropic"}
    assert wizard.ai_env({**st, "ai": False}) == {"AI_ENABLED": "0"}
    assert wizard.account_cfg(st).host == "imap.x"
    assert "masquées tant qu'aucune clé" in wizard.recap_text({**st, "api_key": ""}, True) and "paramètres" in wizard.recap_text(st, False)
