"""Surlignage jaune des mots cherchés : termes, positions (casse, accents, début de mot), échappement, extraits, affichage dans la liste."""
import asyncio
from datetime import datetime

import pytest
from nicegui import ui
from nicegui.testing import User

import main
from mailia import queries, search, sync
from tests.conftest import add_message


def test_terms_from_free_text_and_subject_sender_filters():
    assert search.highlight_terms("Lemasson dominique") == ["lemasson", "dominique"]
    assert search.highlight_terms('"patient VNI"') == ["patient", "vni"]
    assert search.highlight_terms("de:martin objet:devis pj:oui chaudière") == ["chaudiere", "martin", "devis"]
    assert search.highlight_terms("-de:spam a") == []  # filtre exclu et mot d'une seule lettre : rien à surligner
    assert search.highlight_terms("") == []


def test_highlight_ignores_case_and_accents_and_matches_word_starts():
    t = search.highlight_terms("lemasson elodie")
    assert search.highlight_html("RE: MPRTLS MR LEMASSON DOMINIQUE", t) == "RE: MPRTLS MR <mark>LEMASSON</mark> DOMINIQUE"
    assert search.highlight_html("Rendez-vous Élodie", t) == "Rendez-vous <mark>Élodie</mark>"  # É trouvé par « elodie »
    assert search.highlight_html("Lemassonne et ses lemas", search.highlight_terms("lemas")) == "<mark>Lemas</mark>sonne et ses <mark>lemas</mark>"  # début de mot
    assert search.highlight_html("catalemasson", t) == "catalemasson"  # au milieu d'un mot : comme la recherche, pas surligné


def test_highlight_never_lets_mail_html_through():
    out = search.highlight_html('<img src=x onerror=alert(1)> Lemasson & <b>', search.highlight_terms("lemasson"))
    assert "<img" not in out and "&lt;img" in out and "<mark>Lemasson</mark>" in out and "&amp;" in out and "<b>" not in out


def test_snippet_gives_context_around_the_first_match():
    body = "Bonjour, " + "blabla " * 30 + "le patient Lemasson Dominique est sous VNI depuis un mois. " + "fin " * 60
    sn = search.snippet(body, search.highlight_terms("lemasson"))
    assert sn.startswith("…") and sn.endswith("…") and "Lemasson" in sn and len(sn) < 200
    assert not sn.startswith("… ") and sn.split()[0] != "…lematch"
    assert search.snippet("rien ici", search.highlight_terms("lemasson")) == ""
    assert search.snippet("Lemasson tout au début", search.highlight_terms("lemasson")).startswith("Lemasson")  # pas de « … » au début


def test_add_snippets_only_when_the_word_is_not_already_in_the_subject_or_sender():
    add_message(mid="testbox|INBOX|1", subject="Rapport", body="Le patient Lemasson est stable.")
    add_message(mid="testbox|INBOX|2", subject="Dossier Lemasson", body="Le patient Lemasson est stable.")
    rows = [dict(r) for r in queries.list_messages("", "lemasson", group=False)]
    queries.add_snippets(rows, search.highlight_terms("lemasson"))
    by = {r["id"]: r["snip"] for r in rows}
    assert "Lemasson" in by["testbox|INBOX|1"] and by["testbox|INBOX|2"] == ""  # déjà visible (surligné) dans l'objet


@pytest.fixture
def mailbox(monkeypatch):
    monkeypatch.setattr(sync, "run_cycle", lambda: None)
    monkeypatch.setattr(sync, "search_remote", lambda *a: set())
    ui.page("/")(main.index_page)
    from mailia import db
    db.kv_set("brief_day", datetime.now().strftime("%Y-%m-%d"))
    add_message(mid="testbox|INBOX|1", subject="Devis chaudière", body="Voici le devis pour M. Lemasson, patient sous VNI.")
    add_message(mid="testbox|INBOX|2", subject="Réunion jeudi", body="On se voit jeudi.", date="2026-10-02T11:00:00+00:00")
    seen = []
    real = main.Mailbox.__init__
    monkeypatch.setattr(main.Mailbox, "__init__", lambda self, *a, **k: (real(self, *a, **k), seen.append(self))[0])
    return seen


async def test_search_highlights_the_subject_and_shows_a_snippet(user: User, mailbox) -> None:
    await user.open("/")
    await user.should_see("Devis chaudière")
    box = mailbox[-1]
    await box.set_query("lemasson")
    await asyncio.sleep(0.4)
    contents = [e.content for e in user.find(kind=ui.html).elements if "mark" in str(getattr(e, "content", ""))]
    assert any("<mark>Lemasson</mark>" in c and "patient sous VNI" in c for c in contents)  # extrait du texte, mot surligné
    await box.set_query("devis")
    await asyncio.sleep(0.4)
    contents = [e.content for e in user.find(kind=ui.html).elements if "mark" in str(getattr(e, "content", ""))]
    assert any("<mark>Devis</mark> chaudière" in c for c in contents)  # mot de l'objet surligné
    await box.set_query("")
    await asyncio.sleep(0.4)
    assert not [e for e in user.find(kind=ui.html).elements if "<mark>" in str(getattr(e, "content", ""))]  # recherche effacée : plus de surlignage


def test_mail_frame_gets_the_highlight_script_only_during_a_search():
    from mailia import htmlmail
    plain = htmlmail.build_doc("<p>Bonjour Lemasson</p>")
    assert "createElement('mark')" not in plain and plain.count("<script nonce=") == 1
    doc = htmlmail.build_doc("<p>Bonjour Lemasson</p>", terms=["lemasson", "elodie"])
    assert "createElement('mark')" in doc and '["lemasson", "elodie"]' in doc
    assert doc.count("<script nonce=") == 2  # le script de surlignage est autorisé comme celui de l'appli (même jeton) ; sans recherche il n'y en a qu'un
    bad = htmlmail.build_doc("<p>x</p>", terms=["</script><img src=x onerror=alert(1)>"])
    assert "</script><img" not in bad and "\\u003c/script\\u003e" in bad  # un mot de recherche ne peut pas sortir du script


async def test_plain_text_body_is_highlighted_when_a_search_is_active(user: User, mailbox) -> None:
    await user.open("/")
    box = mailbox[-1]
    await box.set_query("lemasson")
    await asyncio.sleep(0.4)
    await box.open("testbox|INBOX|1")
    box.plain.add("testbox|INBOX|1")  # texte brut
    box.reader.refresh()
    await asyncio.sleep(0.4)
    marked = [str(e.content) for e in user.find(kind=ui.html).elements if "mailbody" in " ".join(e.classes)]
    assert any("<mark>Lemasson</mark>" in c for c in marked)
