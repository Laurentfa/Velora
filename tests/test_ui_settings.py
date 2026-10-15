"""Fenêtre des paramètres : chaque onglet s'affiche, un réglage modifié est bien enregistré (filet avant tout découpage du code)."""
from nicegui import ui
from nicegui.testing import User

from mailia import config, db
from mailia.ui_settings import open_settings

SECTIONS = ("Boîtes mail", "Affichage et signature", "Dossiers", "Intelligence artificielle", "Consignes de l'IA", "Règles de tri automatiques",
            "Réponse automatique d'absence", "Agenda", "Automatismes", "Confidentialité et sécurité", "API et assistants", "Utilisation et données")


def settings_page():
    @ui.page("/settings")
    def page() -> None:
        open_settings(ui.element("div"), lambda acct: None)


async def test_every_section_is_rendered(user: User) -> None:
    settings_page()
    await user.open("/settings")
    await user.should_see("Paramètres")
    for title in SECTIONS:
        await user.should_see(title)  # l'onglet existe
    for heading in ("Boîtes mail", "Affichage et signature", "Règles de tri automatiques", "Réponse automatique d'absence", "Agenda"):
        await user.should_see(heading)
    for body in ("Ajouter une boîte mail", "Langue de l'interface", "Fournisseur d'IA", "Nouvelle règle", "Mots de passe des boîtes",
                 "Exporter mes données", "Choisissez les dossiers affichés"):
        await user.should_see(body)  # le contenu de chaque onglet est bien construit, pas seulement son titre


async def test_user_name_is_saved_as_you_type(user: User) -> None:
    settings_page()
    await user.open("/settings")
    user.find("Votre nom").type("Marie Dupont")
    assert config.user_name() == "Marie Dupont"


async def test_save_writes_the_ai_switch(user: User) -> None:
    settings_page()
    await user.open("/settings")
    assert config.ai_enabled()
    user.find("Activer les fonctions IA (résumés, tri, brouillons, brief)").click()
    user.find(marker="settings-save").click()
    await user.should_see("Paramètres enregistrés")
    assert not config.ai_enabled()
    assert db.kv_get("signature_on") in ("0", "1")
