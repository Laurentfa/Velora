"""Accusé de réception demandé par l'expéditeur : repéré à la réception, proposé, jamais envoyé automatiquement."""
import pytest
from conftest import add_message
from nicegui import ui

import main
from mailia import config, db, queries, sync
from mailia.providers import imap


def test_the_requesting_address_is_read_from_the_header() -> None:
    assert imap.receipt_address({"disposition-notification-to": ("Laurent <laurent@perso.fr>",)}) == "laurent@perso.fr"
    assert imap.receipt_address({}) == ""
    assert imap.receipt_address({"disposition-notification-to": ("n'importe quoi",)}) == ""


def test_the_receipt_is_a_standard_disposition_notification() -> None:
    msg = imap.build_receipt("moi@pro.fr", "laurent@perso.fr", "Devis", "<abc@perso.fr>")
    assert msg.get_content_type() == "multipart/report" and msg.get_param("report-type") == "disposition-notification"
    parts = msg.get_payload()
    assert parts[1].get_content_type() == "message/disposition-notification"
    text = str(parts[1].get_payload())
    assert "Original-Message-ID: <abc@perso.fr>" in text and "MDN-sent-manually; displayed" in text and "Final-Recipient: rfc822; moi@pro.fr" in text


def test_sending_a_receipt_needs_a_request() -> None:
    with pytest.raises(ValueError):
        sync.send_receipt({"account": "testbox", "receipt_to": ""})


@pytest.fixture
def box(monkeypatch):
    monkeypatch.setattr(sync, "run_cycle", lambda: None)
    monkeypatch.setattr(config, "ai_enabled", lambda: True)
    monkeypatch.setattr(queries, "_refresh_headline", lambda sig, items: None)
    ui.page("/")(main.index_page)
    seen = []
    real = main.Mailbox.__init__
    monkeypatch.setattr(main.Mailbox, "__init__", lambda self, *a, **k: (real(self, *a, **k), seen.append(self))[0])
    return seen


async def test_the_banner_is_offered_and_nothing_is_sent_by_itself(user, box, monkeypatch) -> None:
    sent = []
    monkeypatch.setattr(sync, "send_receipt", lambda m: sent.append(m["receipt_to"]))
    add_message("testbox|INBOX|1", subject="Devis", sender="laurent@perso.fr", receipt_to="laurent@perso.fr")
    await user.open("/")
    await box[-1].open("testbox|INBOX|1")
    await user.should_see("laurent@perso.fr demande un accusé de réception.")
    assert sent == []
    await box[-1].answer_receipt("testbox|INBOX|1", True)
    assert sent == ["laurent@perso.fr"] and db.kv_get("receipt:testbox|INBOX|1") == "1"
    await user.should_not_see("demande un accusé de réception")


async def test_declining_hides_the_banner_without_sending(user, box, monkeypatch) -> None:
    sent = []
    monkeypatch.setattr(sync, "send_receipt", lambda m: sent.append(m))
    add_message("testbox|INBOX|2", subject="Devis", sender="laurent@perso.fr", receipt_to="laurent@perso.fr")
    await user.open("/")
    await box[-1].open("testbox|INBOX|2")
    await box[-1].answer_receipt("testbox|INBOX|2", False)
    assert sent == [] and db.kv_get("receipt:testbox|INBOX|2") == "0"
    await user.should_not_see("demande un accusé de réception")


async def test_no_banner_when_nothing_was_requested(user, box) -> None:
    add_message("testbox|INBOX|3", subject="Salut", sender="alice@example.com")
    await user.open("/")
    await box[-1].open("testbox|INBOX|3")
    await user.should_not_see("demande un accusé de réception")
