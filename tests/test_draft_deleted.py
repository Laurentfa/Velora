"""Un brouillon supprimé depuis la liste du dossier Brouillons ne doit pas rester ouvert dans l'éditeur, ni réapparaître."""
import pytest
from conftest import add_message
from nicegui import ui

import main
from mailia import config, db, queries, sync


@pytest.fixture
def box(monkeypatch):
    monkeypatch.setattr(sync, "run_cycle", lambda: None)
    monkeypatch.setattr(config, "ai_enabled", lambda: True)
    monkeypatch.setattr(queries, "_refresh_headline", lambda sig, items: None)
    calls = []
    prov = type("P", (), {"calendar_events": lambda self, a, b: [], "tasks": lambda self: [], "delete": lambda self, folder, uid: calls.append((folder, uid))})()
    real_provider = sync.provider
    monkeypatch.setattr(sync, "provider", lambda key: prov if key == "testbox" else real_provider(key))
    ui.page("/")(main.index_page)
    seen = []
    real = main.Mailbox.__init__
    monkeypatch.setattr(main.Mailbox, "__init__", lambda self, *a, **k: (real(self, *a, **k), seen.append(self))[0])
    return seen, calls


async def test_deleting_the_open_draft_from_the_list_closes_the_editor_and_forgets_the_draft(user, box) -> None:
    seen, calls = box
    add_message("testbox|INBOX|1", subject="Bienvenue", sender="no-reply@exemple.com")
    add_message("testbox|Drafts|D1", subject="RE: Bienvenue", sender="moi@example.org", folder="Drafts")
    db.draft_save({"id": "dr1", "account": "testbox", "is_new": False, "reply_to": "testbox|INBOX|1", "to": "no-reply@exemple.com", "cc": "", "bcc": "",
                   "subject": "RE: Bienvenue", "html": "<p>Merci</p>", "importance": 1}, None)
    db.draft_set_server("dr1", "D1", db.draft_load("dr1")[0]["updated"], "Drafts")
    await user.open("/")
    b = seen[-1]
    await b.resume_draft("dr1")
    assert b.compose and b.draft["id"] == "dr1" and b.cur and b.cur["id"] == "testbox|INBOX|1"
    await b.swipe_act(queries.get_message("testbox|Drafts|D1"), "delete")
    assert calls == [("Drafts", "D1")]  # supprimé sur le serveur
    assert not b.compose and b.cur is None  # l'éditeur et le message affiché se sont fermés
    assert db.draft_load("dr1")[0] is None  # et le brouillon local n'existe plus : rien ne le recréera


async def test_deleting_another_message_leaves_the_open_editor_alone(user, box) -> None:
    seen, calls = box
    add_message("testbox|INBOX|1", subject="Bienvenue", sender="no-reply@exemple.com")
    add_message("testbox|INBOX|2", subject="Autre", sender="x@y.fr")
    db.draft_save({"id": "dr2", "account": "testbox", "is_new": False, "reply_to": "testbox|INBOX|1", "to": "no-reply@exemple.com", "cc": "", "bcc": "",
                   "subject": "RE: Bienvenue", "html": "<p>Merci</p>", "importance": 1}, None)
    await user.open("/")
    b = seen[-1]
    await b.resume_draft("dr2")
    await b.swipe_act(queries.get_message("testbox|INBOX|2"), "delete")
    assert b.compose and db.draft_load("dr2")[0] is not None
