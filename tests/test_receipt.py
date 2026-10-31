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


def test_a_subject_cannot_inject_headers_into_the_receipt() -> None:
    msg = imap.build_receipt("moi@pro.fr", "laurent@perso.fr", "Devis\r\nBcc: espion@mal.fr", "<a@b>\r\nX-Evil: 1")
    assert msg["Bcc"] is None and msg["X-Evil"] is None and "\n" not in msg["Subject"]


def test_only_recent_mail_is_reread_for_a_receipt_request() -> None:
    from datetime import datetime, timedelta, timezone
    base = {"att_json": "[]", "body_html": "", "unsub": "", "auth": "{}", "bcc_addrs": "", "folder": "INBOX", "body": "x", "id": "a", "receipt_to": None}
    new = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    old = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
    assert sync.needs_details({**base, "date": new}) and not sync.needs_details({**base, "date": old})


def test_imap_sync_reads_the_request_without_a_second_trip_to_the_server() -> None:
    assert imap.receipt_address({"disposition-notification-to": ("a@b.fr",)}) == "a@b.fr"


def test_ews_receipt_request_gives_the_sender_address() -> None:
    from types import SimpleNamespace

    from mailia.providers import ews
    it = SimpleNamespace(is_read_receipt_requested=True, sender=SimpleNamespace(email_address="x@y.fr"))
    assert ews._receipt_to(it) == "x@y.fr"
    assert ews._receipt_to(SimpleNamespace(is_read_receipt_requested=False, sender=None)) == ""


async def test_the_banner_warns_when_the_address_is_not_the_senders(user, box) -> None:
    add_message("testbox|INBOX|4", subject="Offre", sender="promo@exemple.com", receipt_to="autre@tiers.net")
    add_message("testbox|INBOX|5", subject="Devis", sender="laurent@perso.fr", receipt_to="laurent@perso.fr")
    await user.open("/")
    await box[-1].open("testbox|INBOX|4")
    await user.should_see("autre@tiers.net demande")
    assert len(list(user.find(marker="receipt-warn").elements)) == 1
    await box[-1].open("testbox|INBOX|5")
    await user.should_see("laurent@perso.fr demande")
    await user.should_not_see(marker="receipt-warn")
