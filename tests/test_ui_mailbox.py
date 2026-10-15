"""Fenêtre principale, parcours réels dans l'interface : liste, ouverture d'un mail, archivage, repli des dossiers d'une boîte."""
import json
from datetime import datetime

import pytest
from nicegui import events, ui
from nicegui.testing import User

import main
from mailia import db, sync
from tests.conftest import add_message


@pytest.fixture
def mailbox(monkeypatch):
    monkeypatch.setattr(sync, "run_cycle", lambda: None)
    ui.page("/")(main.index_page)  # NiceGUI repart de zéro à chaque test : on réenregistre la page principale
    db.kv_set("brief_day", datetime.now().strftime("%Y-%m-%d"))  # le brief du jour est déjà passé : on arrive sur la boîte de réception
    add_message(mid="testbox|INBOX|1", subject="Devis chaudière", body="Voici le devis demandé.", ckey="testbox|c1")
    add_message(mid="testbox|INBOX|2", subject="Réunion jeudi", body="On se voit jeudi.", ckey="testbox|c2", date="2026-10-01T11:00:00+00:00")


def fire(user: User, element, event: str = "click.stop") -> None:
    """Déclenche le gestionnaire d'un clic « .stop » (le simulateur de NiceGUI ne le fait pas tout seul)."""
    with user.client:
        for listener in element._event_listeners.values():
            if listener.type == event:
                events.handle_event(listener.handler, events.GenericEventArguments(sender=element, client=user.client, args=None))


async def test_list_shows_messages_and_click_opens_one(user: User, mailbox) -> None:
    await user.open("/")
    await user.should_see("Devis chaudière")
    await user.should_see("Réunion jeudi")
    user.find(marker="row-testbox|INBOX|1").click()
    await user.should_not_see("Sélectionnez un message")  # le volet de lecture s'est ouvert


async def test_archive_icon_archives_the_conversation(user: User, mailbox, monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(sync, "perform_many", lambda msgs, kind, dest=None: calls.append((kind, [m["id"] for m in msgs])))
    await user.open("/")
    await user.should_see("Devis chaudière")
    archive = next(b for b in user.find(ui.button).elements if b.props.get("icon") == "archive"
                   and any(l.type == "click.stop" for l in b._event_listeners.values()))  # l'icône de la ligne (pas le geste de balayage)
    fire(user, archive)
    await user.should_see("archivé")
    assert calls and calls[0][0] == "archive"


async def test_account_arrow_folds_its_folders(user: User, mailbox) -> None:
    with db.conn() as c:
        c.execute("INSERT OR REPLACE INTO folders(account,key,name,path,role,sync) VALUES('testbox','Clients','Clients','Clients','',1)")
    await user.open("/")
    await user.should_see("Clients")
    (arrow,) = user.find(marker="fold-testbox").elements
    fire(user, arrow)
    await user.should_not_see("Clients")
    assert json.loads(db.kv_get("side_fold")) == ["testbox"]
    (arrow,) = user.find(marker="fold-testbox").elements
    fire(user, arrow)
    await user.should_see("Clients")


async def test_ask_source_click_opens_the_mail(user: User, mailbox, monkeypatch) -> None:
    """Régression : cliquer sur une source de « Demander à ma boîte » fermait la fenêtre sans ouvrir le mail."""
    from mailia import ask, config
    monkeypatch.setattr(config, "ai_enabled", lambda: True)
    monkeypatch.setattr(ask, "answer", lambda q: {"text": "C'est le devis [1].", "sources": [
        {"n": 1, "id": "testbox|INBOX|1", "subject": "Devis chaudière", "sender": "Alice", "date": "2026-10-01T10:00:00+00:00", "cited": True}]})
    await user.open("/")
    await user.should_see("Demander à ma boîte")
    user.find(marker="ask").click()
    await user.should_see("Posez une question")
    user.find(ui.input).type("le devis").trigger("keydown.enter")
    await user.should_see("Sources citées")
    user.find(marker="source-1").click()
    await user.should_not_see("Sélectionnez un message")  # le mail s'est ouvert dans le volet de lecture
