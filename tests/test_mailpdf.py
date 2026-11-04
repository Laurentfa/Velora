"""PDF d'un message (impression) : accents, caractères typographiques, plusieurs pages, avec ou sans police Unicode."""
from io import BytesIO

import pytest
from pypdf import PdfReader

from mailia import mailpdf

HEAD = {"subject": "RE: Anomalie – rapport d’observance", "from": "Clément <c@gestis.net>", "to": "a@x.fr, b@x.fr", "cc": "t@x.fr", "date": "vendredi 30 octobre 2026",
        "attachments": ["rapport.pdf", "DAP - ALLEAUME.pdf"]}
TEXT = "Bonjour,\n\nContrôle manuel… 12 € – « œuvre ».\n" + "\n".join(f"Ligne {i} : texte accentué éàù." for i in range(90)) + "\n\nCordialement"


def pages(data: bytes) -> list[str]:
    return [p.extract_text() for p in PdfReader(BytesIO(data)).pages]


@pytest.mark.parametrize("unicode_font", [True, False])
def test_the_pdf_holds_the_header_the_text_and_the_attachment_names(monkeypatch, unicode_font) -> None:
    files = mailpdf._font_files()
    if unicode_font and not files:
        pytest.skip("aucune police Unicode sur cette machine")
    monkeypatch.setattr(mailpdf, "_font_files", lambda: files if unicode_font else None)
    data = mailpdf.message_pdf(HEAD, TEXT)
    out = pages(data)
    assert data.startswith(b"%PDF") and len(out) >= 2  # le texte long passe sur plusieurs pages
    whole = "".join(out)
    assert "Contrôle manuel" in whole and "Clément" in whole and "a@x.fr" in whole and "Ligne 89" in whole
    assert "rapport.pdf" in out[-1] and "DAP - ALLEAUME.pdf" in out[-1]


def test_an_empty_message_and_odd_characters_never_break_the_pdf(monkeypatch) -> None:
    monkeypatch.setattr(mailpdf, "_font_files", lambda: None)  # police de base : ce qui n'existe pas en Latin-1 devient « ? »
    data = mailpdf.message_pdf({"subject": ""}, "")
    assert "(message vide)" in "".join(pages(data))
    data = mailpdf.message_pdf({"subject": "😀 日本語"}, "texte 😀 日本語\t\ttabulation\r\nligne")
    assert "tabulation" in "".join(pages(data))
