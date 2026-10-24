"""Pièces jointes d'un message reçu : « Tout télécharger » en un seul zip."""
import json
import zipfile
from io import BytesIO

import pytest
from conftest import add_message
from nicegui import ui

import main
from mailia import config, queries, sync
from mailia.ui_files import FilesMixin


def test_zip_keeps_every_file_and_renames_duplicates_and_unsafe_names() -> None:
    blob = FilesMixin.zip_bundle([("a.pdf", b"1"), ("a.pdf", b"2"), ("../../etc/passwd", b"3"), ("", b"4")])
    names = zipfile.ZipFile(BytesIO(blob)).namelist()
    assert names == ["a.pdf", "a (1).pdf", "passwd", "piece-jointe"]
    assert zipfile.ZipFile(BytesIO(blob)).read("a (1).pdf") == b"2"


@pytest.fixture
def box(monkeypatch):
    monkeypatch.setattr(sync, "run_cycle", lambda: None)
    monkeypatch.setattr(config, "ai_enabled", lambda: True)
    monkeypatch.setattr(queries, "_refresh_headline", lambda sig, items: None)
    prov = type("P", (), {"calendar_events": lambda self, a, b: [], "tasks": lambda self: []})()
    real_provider = sync.provider
    monkeypatch.setattr(sync, "provider", lambda key: prov if key == "testbox" else real_provider(key))
    ui.page("/")(main.index_page)
    seen = []
    real = main.Mailbox.__init__
    monkeypatch.setattr(main.Mailbox, "__init__", lambda self, *a, **k: (real(self, *a, **k), seen.append(self))[0])
    return seen


async def test_the_zip_button_shows_for_several_attachments_and_saves_one_archive(user, box, monkeypatch) -> None:
    atts = [{"id": "1", "name": "devis.pdf", "size": 10}, {"id": "2", "name": "photo.jpg", "size": 20}]
    add_message("testbox|INBOX|1", subject="RE: Mon dossier", att_json=json.dumps(atts), has_att=1)
    add_message("testbox|INBOX|2", subject="Une seule", att_json=json.dumps(atts[:1]), has_att=1)
    monkeypatch.setattr(sync, "fetch_attachment", lambda m, a: (b"data-" + a["id"].encode(), a["name"]))
    saved = []
    monkeypatch.setattr(main.Mailbox, "_save_download", lambda self, name, data: saved.append((name, data)))
    await user.open("/")
    b = box[-1]
    await b.open("testbox|INBOX|2")
    await user.should_see("devis.pdf")
    await user.should_not_see("Tout télécharger")  # un seul fichier : le bouton existant suffit
    await b.open("testbox|INBOX|1")
    await user.should_see("Tout télécharger (2, zip)")
    user.find(marker="att-zip").click()
    from tests.conftest import wait_until
    await wait_until(lambda: saved)
    assert saved[0][0] == "Mon dossier.zip"
    assert zipfile.ZipFile(BytesIO(saved[0][1])).namelist() == ["devis.pdf", "photo.jpg"]
