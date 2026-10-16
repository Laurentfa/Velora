"""Désabonnement : le bandeau s'affiche dans tous les dossiers (pas seulement la boîte de réception), avec des précautions pour les indésirables."""
import asyncio
import json
from datetime import datetime

import pytest
from nicegui import ui
from nicegui.testing import User

import main
from mailia import db, sync
from tests.conftest import add_message

UNSUB = json.dumps({"https": "https://exemple.fr/desabo", "mailto": "", "one_click": False, "link": ""})
TEXT = "Message envoyé à une liste de diffusion."


@pytest.fixture
def box(monkeypatch):
    monkeypatch.setattr(sync, "run_cycle", lambda: None)
    ui.page("/")(main.index_page)
    db.kv_set("brief_day", datetime.now().strftime("%Y-%m-%d"))
    with db.conn() as c:
        for k, n, role in (("INBOX", "Boîte de réception", "inbox"), ("Junk", "Courrier indésirable", "junk"), ("com", "Autres Commerciaux", ""), ("Sent", "Envoyés", "sent")):
            c.execute("INSERT OR REPLACE INTO folders(account,key,name,path,role,sync) VALUES('testbox',?,?,?,?,1)", (k, n, n, role))
    seen = []
    real = main.Mailbox.__init__
    monkeypatch.setattr(main.Mailbox, "__init__", lambda self, *a, **k: (real(self, *a, **k), seen.append(self))[0])
    return seen


def mail(folder, uid="1", sender="promo@sfr.fr", auth=None, unsub=UNSUB, subject="Offre du mois"):
    mid = f"testbox|{folder}|{uid}"
    add_message(mid=mid, folder=folder, subject=subject, sender=sender, unsub=unsub, auth=json.dumps(auth or {}))
    return mid


async def opened(user, box, mid):
    await user.open("/")
    await box[-1].open(mid)
    await asyncio.sleep(0.3)


async def test_banner_in_the_inbox_as_before(user: User, box) -> None:
    await opened(user, box, mail("INBOX"))
    await user.should_see(TEXT)


async def test_banner_in_a_regular_subfolder_and_the_archive(user: User, box) -> None:
    await opened(user, box, mail("com"))  # « Autres Commerciaux » : les newsletters rangées par l'utilisateur
    await user.should_see(TEXT)
    await user.should_see("Se désabonner")


async def test_junk_shows_it_when_the_server_verified_the_sender(user: User, box) -> None:
    await opened(user, box, mail("Junk", auth={"dmarc": "pass", "spf": "pass", "dkim": "pass"}))
    await user.should_see(TEXT)
    await user.should_see("classé en indésirable")  # le bandeau le dit


async def test_junk_hides_it_for_an_unverified_stranger(user: User, box) -> None:
    await opened(user, box, mail("Junk", sender="inconnu@spam-exemple.biz", auth={"spf": "fail"}))
    await user.should_not_see(TEXT)  # un vrai spam : se désabonner confirmerait l'adresse


async def test_junk_shows_it_for_a_sender_we_already_know_even_without_auth_headers(user: User, box) -> None:
    for i in range(6):  # assez de mails reçus de cet expéditeur pour le connaître
        add_message(mid=f"testbox|INBOX|k{i}", sender="promo@sfr.fr", subject=f"Avant {i}")
    await opened(user, box, mail("Junk"))
    await user.should_see(TEXT)


async def test_never_in_sent_mail_nor_for_a_suspicious_message(user: User, box) -> None:
    await opened(user, box, mail("Sent", sender="moi@example.org"))
    await user.should_not_see(TEXT)


async def test_no_banner_without_an_unsubscribe_link(user: User, box) -> None:
    await opened(user, box, mail("com", unsub=""))
    await user.should_not_see(TEXT)
