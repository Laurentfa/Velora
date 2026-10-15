"""Garde-fou : aucune erreur de nom, aucun import ou variable inutile dans le code (pyflakes)."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_pyflakes_clean():
    out = subprocess.run([sys.executable, "-m", "pyflakes", "main.py", "mailia", "tools", "tests"], cwd=ROOT, capture_output=True, text=True)
    assert out.returncode == 0 and not out.stdout.strip(), out.stdout


def test_logo_is_an_inline_png_of_the_app_icon():
    import base64
    import io
    from PIL import Image
    from mailia import brand
    html = brand.logo_img(28)
    assert html.startswith('<img src="data:image/png;base64,') and "width:28px" in html
    im = Image.open(io.BytesIO(base64.b64decode(brand.LOGO_PNG)))
    assert im.size == (96, 96) and im.getpixel((48, 8))[:3] == (255, 255, 255) or im.getpixel((48, 8))[0] > 235  # carré clair, avec le V bleu au centre
    assert im.getpixel((60, 60))[2] > 150 and im.getpixel((60, 60))[0] < 120  # le V bleu au centre


def test_preview_has_zoom_controls():
    src = open("mailia/ui_files.py", encoding="utf-8").read()
    assert "zoom_in" in src and "zoom_out" in src and "set_zoom" in src


def test_signature_block_embeds_remote_images(monkeypatch):
    from mailia import compose
    monkeypatch.setattr(compose, "signature_on", lambda: True)
    monkeypatch.setattr(compose, "signature_html", lambda a="": '<p>x<img src="http://h.example/a.png"></p>')
    monkeypatch.setattr(compose, "_fetch_signature_image", lambda u: (b"\x89PNG", "image/png"))
    assert "data:image/png;base64," in compose.sig_block("a") and "http://h.example" not in compose.sig_block("a")


def test_csv_preview_is_a_table():
    from mailia import preview
    html, total, shown = preview.csv_table('Nom;Ville\n="01";<b>Rouen</b>\nA;B\n'.encode())
    assert total == 2 and shown == 2 and "<th>Nom</th>" in html and "<td>01</td>" in html and "&lt;b&gt;" in html
    assert preview.csv_table(b"juste un texte") is None


def test_lock_screen_unlocks_session_on_good_code():
    src = open("mailia/ui_lock.py", encoding="utf-8").read()
    i = src.index("async def submit()")
    assert "session_unlock()" in src[i:i + 400]


def test_network_failures_log_one_line(caplog):
    import logging
    from mailia import sync
    with caplog.at_level(logging.WARNING, logger="sync"):
        sync.log_failure("liste des dossiers x", ConnectionRefusedError("refusé"))
        sync.log_failure("liste des dossiers y", ValueError("bug"))
    net, bug = caplog.records
    assert net.levelno == logging.WARNING and not net.exc_info
    assert bug.levelno == logging.ERROR and bug.exc_info


def test_summaries_written_without_the_body_are_requeued():
    from mailia import db, queries
    from tests.conftest import add_message
    long_body = "Bonjour, voici le dossier complet du patient avec toutes les pièces demandées pour la prise en charge. " * 2
    add_message(mid="testbox|INBOX|b1", subject="PPC", body=long_body, summary="Message sans contenu visible.", ai_at="2026-10-01T00:00:00")
    add_message(mid="testbox|INBOX|b2", subject="OK", body=long_body, summary="Demande de devis pour une chaudière.", ai_at="2026-10-01T00:00:00")
    add_message(mid="testbox|INBOX|b3", subject="Vide", body="", summary="Message vide.", ai_at="2026-10-01T00:00:00")
    assert queries.requeue_bodyless_summaries() == 1  # b1 seulement : b2 est un bon résumé, b3 n'a toujours pas de texte
    with db.conn() as c:
        pending = {r["id"] for r in c.execute("SELECT id FROM messages WHERE ai_at IS NULL")}
    assert pending == {"testbox|INBOX|b1"}
