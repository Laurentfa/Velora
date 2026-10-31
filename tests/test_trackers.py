"""Pixels espions : détection et retrait dans les mails HTML reçus."""
import pytest
from conftest import add_message
from nicegui import ui

import main
from mailia import config, htmlmail, queries, sync


def strip(html: str):
    return htmlmail.strip_trackers(htmlmail.sanitize(html))


def test_one_pixel_images_are_found_and_removed() -> None:
    out, found = strip('<p>Bonjour</p><img src="https://track.exemple.com/p/abc123" width="1" height="1">')
    assert "track.exemple.com" not in out and "<p>Bonjour</p>" in out
    assert found == [{"host": "track.exemple.com", "kind": "pixel"}]


def test_known_open_tracking_urls_are_found_even_when_not_tiny() -> None:
    out, found = strip('<img src="https://abc.list-manage.com/track/open.php?u=1&id=2" width="120"><img src="https://u123.ct.sendgrid.net/wf/open?upn=xyz">')
    assert "list-manage" not in out and "sendgrid" not in out
    assert [f["kind"] for f in found] == ["adresse de suivi", "adresse de suivi"]


def test_style_based_tiny_images_are_found() -> None:
    _, found = strip('<img src="https://t.exemple.fr/i.gif" style="width:1px;height:1px">')
    assert found and found[0]["kind"] == "pixel"


def test_normal_images_logos_and_embedded_pictures_are_kept() -> None:
    html = '<img src="https://exemple.fr/logo.png" width="200" height="60"><img src="cid:photo1"><img src="https://exemple.fr/banniere.jpg">'
    out, found = strip(html)
    assert found == [] and out.count("<img") == 3


def test_a_mail_whose_only_remote_image_is_a_tracker_no_longer_asks_to_show_images() -> None:
    out, _ = strip('<p>Salut</p><img src="https://t.exemple.com/o.gif" width="1" height="1">')
    assert not htmlmail.has_remote_images(out)


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


async def test_the_reader_tells_which_trackers_were_hidden(user, box) -> None:
    add_message("testbox|INBOX|1", subject="Offre", sender="promo@exemple.com", body="texte",
                body_html='<p>Bonjour</p><img src="https://suivi.exemple.com/o/xyz" width="1" height="1">', att_json="[]", unsub="", auth="{}", bcc_addrs="")
    await user.open("/")
    await box[-1].open("testbox|INBOX|1")
    await user.should_see("Pisteur d'ouverture masqué : suivi.exemple.com")
    await user.should_not_see("Images distantes bloquées")


def test_thin_remote_lines_are_not_taken_for_trackers() -> None:
    out, found = strip('<img src="https://exemple.fr/filet.png" width="600" height="1"><img src="https://exemple.fr/barre.png" width="2" height="400">')
    assert found == [] and out.count("<img") == 2
