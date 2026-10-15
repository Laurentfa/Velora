"""Fenêtre « Contacts et groupes » : liste, recherche, ajout, favori, retrait, création de groupe (filet avant tout remaniement du code)."""
import asyncio

from nicegui import ui
from nicegui.testing import User

from mailia import db
from mailia.contacts_ui import contacts_dialog


def contacts_page(written: list | None = None):
    @ui.page("/contacts")
    async def page() -> None:
        await contacts_dialog(ui.element("div"), lambda addresses: (written if written is not None else []).extend(addresses))


def seed():
    db.contact_save("alice@exemple.fr", "Alice Martin", "manual")
    db.contact_save("bob@exemple.fr", "Bob Durand", "manual")


async def test_dialog_lists_contacts_and_filters_them(user: User) -> None:
    seed()
    contacts_page()
    await user.open("/contacts")
    await user.should_see("Contacts et groupes")
    await user.should_see("Alice Martin")
    await user.should_see("Bob Durand")
    user.find("Rechercher un nom ou une adresse…").type("bob")
    await user.should_see("Bob Durand")
    await user.should_not_see("Alice Martin")


async def test_new_contact_is_saved(user: User) -> None:
    contacts_page()
    await user.open("/contacts")
    user.find(marker="ct-new").click()
    await user.should_see("Nouveau contact")
    user.find("Nom").type("Claire Petit")
    user.find("Adresse e-mail").type("claire@exemple.fr")
    user.find(marker="ct-save").click()
    await asyncio.sleep(0.3)  # la fenêtre se referme puis le carnet est mis à jour
    assert [c["email"] for c in db.contacts_list("claire")] == ["claire@exemple.fr"]


async def test_invalid_address_is_refused(user: User) -> None:
    contacts_page()
    await user.open("/contacts")
    user.find(marker="ct-new").click()
    await user.should_see("Nouveau contact")
    user.find("Adresse e-mail").type("pas-une-adresse")
    user.find(marker="ct-save").click()
    await user.should_see("Adresse e-mail invalide.")
    assert not db.contacts_list("pas-une-adresse")


async def test_favorite_star_and_removal(user: User) -> None:
    seed()
    contacts_page()
    await user.open("/contacts")
    await user.should_see("Alice Martin")
    user.find(marker="ct-fav").click()  # le premier de la liste
    first = db.contacts_list()[0]
    assert first["favorite"]
    user.find(marker="ct-drop").click()
    await user.should_see("contact(s)")
    assert len(db.contacts_list()) == 1


async def test_group_creation_and_members(user: User) -> None:
    seed()
    contacts_page()
    await user.open("/contacts")
    await user.should_see("Alice Martin")
    # l'onglet Groupes : son contenu existe dès l'ouverture
    user.find(marker="grp-new").click()
    await user.should_see("Nom du groupe")
    user.find("Nom du groupe").type("Équipe technique")
    user.find(marker="grp-create").click()
    await user.should_see("👥 Équipe technique")
    (g,) = db.groups_list()
    assert g["name"] == "Équipe technique" and g["members"] == []
    db.group_set_member(g["id"], "alice@exemple.fr", True)
    assert db.groups_list()[0]["members"] == ["alice@exemple.fr"]
