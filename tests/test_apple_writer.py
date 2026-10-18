"""Apple Intelligence dans la rédaction (Mac) : le pont avec le programme natif (sur un faux programme), les boutons, l'aperçu avant remplacement, l'indépendance vis-à-vis des réglages IA."""
import asyncio
import json
import stat
import sys
import textwrap
from datetime import datetime

import pytest
from nicegui import events, ui

import main
from mailia import apple_writer, config, db, sync


@pytest.fixture
def fake_helper(tmp_path, monkeypatch):
    """Un faux « velora-write » : met le texte en majuscules, ou répond une erreur selon le mode."""
    script = tmp_path / "velora-write"
    script.write_text(textwrap.dedent(f"""\
        #!{sys.executable}
        import json, sys
        if "--check" in sys.argv:
            print(json.dumps({{"available": True}})); sys.exit(0)
        d = json.load(sys.stdin)
        if d["mode"] == "shorter" and "refus" in d["text"]:
            print(json.dumps({{"error": "refused", "msg": "x"}})); sys.exit(1)
        print(json.dumps({{"text": d["text"].upper()}}))
    """))
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setattr(apple_writer, "helper_path", lambda: script)
    return script


@pytest.mark.skipif(sys.platform == "win32", reason="le faux programme est un script à shebang")
def test_rewrite_goes_through_the_helper(fake_helper):
    assert apple_writer.rewrite("correct", "bonjour") == "BONJOUR"
    assert apple_writer.rewrite("correct", "   ") == ""


@pytest.mark.skipif(sys.platform == "win32", reason="le faux programme est un script à shebang")
def test_a_refusal_or_a_bad_mode_becomes_a_clear_error(fake_helper):
    with pytest.raises(apple_writer.WriterError, match="refusé"):
        apple_writer.rewrite("shorter", "texte refusé")
    with pytest.raises(apple_writer.WriterError):
        apple_writer.rewrite("inconnu", "x")


@pytest.mark.skipif(sys.platform == "win32", reason="le faux programme est un script à shebang")
def test_a_long_text_is_cut_into_pieces_and_put_back_together(fake_helper):
    long = "\n".join(f"Paragraphe numéro {i}. Il parle du devis et de la livraison prévue pour vendredi prochain." for i in range(80))
    pieces = apple_writer.chunks(long)
    assert len(pieces) >= 3 and all(len(p) <= apple_writer.MAX_CHUNK for p in pieces)
    assert apple_writer.rewrite("correct", long) == "\n".join(p.upper() for p in pieces)
    assert "Paragraphe numéro 79" in "\n".join(pieces)  # rien n'est perdu


def test_chunks_cut_a_huge_paragraph_at_sentences():
    para = " ".join(f"Phrase {i} assez longue pour remplir de la place." for i in range(200))
    pieces = apple_writer.chunks(para)
    assert len(pieces) > 2 and all(len(p) <= apple_writer.MAX_CHUNK + 60 for p in pieces)


def test_support_requires_a_mac_with_macos_26_and_the_helper(monkeypatch, tmp_path):
    helper = tmp_path / "velora-write"
    helper.write_text("")
    monkeypatch.setattr(apple_writer, "helper_path", lambda: helper)
    monkeypatch.setattr(apple_writer.sys, "platform", "darwin")
    monkeypatch.setattr(apple_writer.platform, "mac_ver", lambda: ("26.2", ("", "", ""), ""))
    assert apple_writer.supported()
    monkeypatch.setattr(apple_writer.platform, "mac_ver", lambda: ("15.5", ("", "", ""), ""))
    assert not apple_writer.supported()
    monkeypatch.setattr(apple_writer.sys, "platform", "win32")
    assert not apple_writer.supported()


def test_the_setting_turns_the_buttons_off():
    assert apple_writer.enabled()
    db.kv_set("apple_writer", "0")
    assert not apple_writer.enabled()


class Done:
    def __init__(self, v):
        self.v = v

    def __await__(self):
        if False:
            yield
        return self.v


@pytest.fixture
def box(monkeypatch):
    monkeypatch.setattr(sync, "run_cycle", lambda: None)
    ui.page("/")(main.index_page)
    db.kv_set("brief_day", datetime.now().strftime("%Y-%m-%d"))
    seen = []
    real = main.Mailbox.__init__
    monkeypatch.setattr(main.Mailbox, "__init__", lambda self, *a, **k: (real(self, *a, **k), seen.append(self))[0])
    return seen


def click(user, marker):
    (el,) = user.find(marker=marker).elements
    with user.client:
        for listener in el._event_listeners.values():
            if listener.type == "click":
                events.handle_event(listener.handler, events.ClickEventArguments(sender=el, client=user.client))


async def open_composer(user, box):
    await user.open("/")
    b = box[-1]
    b.new_message()
    await asyncio.sleep(0.5)
    return b


async def test_the_buttons_are_there_even_when_the_ai_functions_are_off(user, box, monkeypatch) -> None:
    monkeypatch.setattr(config, "ai_enabled", lambda: False)  # IA désactivée ou sans clé : Apple Intelligence reste proposée
    monkeypatch.setattr(apple_writer, "supported", lambda: True)
    monkeypatch.setattr(apple_writer, "available", lambda max_age=60.0: True)
    await open_composer(user, box)
    assert user.find(marker="apple-tool").elements and user.find(marker="apple-correct").elements


async def test_the_buttons_are_hidden_when_apple_intelligence_is_unavailable_or_turned_off(user, box, monkeypatch) -> None:
    monkeypatch.setattr(apple_writer, "available", lambda max_age=60.0: False)
    await open_composer(user, box)
    await user.should_not_see(marker="apple-tool")


async def test_turned_off_in_the_settings_the_buttons_disappear(user, box, monkeypatch) -> None:
    monkeypatch.setattr(apple_writer, "available", lambda max_age=60.0: True)
    db.kv_set("apple_writer", "0")
    await open_composer(user, box)
    await user.should_not_see(marker="apple-tool")


async def test_correct_shows_before_and_after_then_applies(user, box, monkeypatch) -> None:
    sent = []
    monkeypatch.setattr(apple_writer, "available", lambda max_age=60.0: True)
    monkeypatch.setattr(apple_writer, "rewrite", lambda mode, text: text.replace("ecrit", "écris"))

    def fake_js(code, **k):
        sent.append(code)
        if "mailiaTarget" in code:
            return Done({"text": "je vous ecrit au sujet du devis", "kind": "message"})
        if "mailiaApply" in code:
            return Done(True)
        return Done(None)
    monkeypatch.setattr(ui, "run_javascript", fake_js)
    await open_composer(user, box)
    click(user, "apple-correct")
    await user.should_see("proposition d'Apple Intelligence")
    await user.should_see("je vous ecrit au sujet du devis")
    await user.should_see("je vous écris au sujet du devis")
    assert not [c for c in sent if "mailiaApply" in c]  # rien n'est remplacé avant votre accord
    click(user, "apple-apply")
    await asyncio.sleep(0.5)
    applied = [c for c in sent if "mailiaApply" in c]
    assert applied and json.loads(applied[0].split(", ", 1)[1].rstrip(")")) == "je vous écris au sujet du devis"


async def test_cancel_leaves_the_text_alone(user, box, monkeypatch) -> None:
    sent = []
    monkeypatch.setattr(apple_writer, "available", lambda max_age=60.0: True)
    monkeypatch.setattr(apple_writer, "rewrite", lambda mode, text: "autre texte")

    def fake_js(code, **k):
        sent.append(code)
        return Done({"text": "texte", "kind": "selection"} if "mailiaTarget" in code else None)
    monkeypatch.setattr(ui, "run_javascript", fake_js)
    await open_composer(user, box)
    click(user, "apple-rephrase")
    await user.should_see("proposition d'Apple Intelligence")
    click(user, "apple-cancel")
    await asyncio.sleep(0.4)
    assert not [c for c in sent if "mailiaApply" in c]


async def test_an_error_is_shown_not_swallowed(user, box, monkeypatch) -> None:
    monkeypatch.setattr(apple_writer, "available", lambda max_age=60.0: True)

    def boom(mode, text):
        raise apple_writer.WriterError(apple_writer.ERRORS["too_long"])
    monkeypatch.setattr(apple_writer, "rewrite", boom)
    monkeypatch.setattr(ui, "run_javascript", lambda code, **k: Done({"text": "x", "kind": "message"} if "mailiaTarget" in code else None))
    await open_composer(user, box)
    click(user, "apple-correct")
    await user.should_see("trop long")


async def test_empty_text_says_so(user, box, monkeypatch) -> None:
    monkeypatch.setattr(apple_writer, "available", lambda max_age=60.0: True)
    monkeypatch.setattr(ui, "run_javascript", lambda code, **k: Done({"text": "  ", "kind": "message"} if "mailiaTarget" in code else None))
    await open_composer(user, box)
    click(user, "apple-correct")
    await user.should_see("Rien à traiter")


def test_the_page_scripts_are_valid_javascript(tmp_path):
    """Un « \\n » mal échappé dans le code de la page a déjà cassé tout le script : on le fait relire par Node quand il est là."""
    import re
    import shutil
    import subprocess
    node = shutil.which("node")
    if not node:
        pytest.skip("Node n'est pas installé")
    from mailia import ui_assets
    code = "\n".join(re.findall(r"<script>(.*?)</script>", ui_assets.SWIPE_JS, re.S))
    f = tmp_path / "page.js"
    f.write_text(code, encoding="utf-8")
    r = subprocess.run([node, "--check", str(f)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[:400]


def test_the_builds_ship_the_translation_files():
    """Sans mailia/locales dans l'appli empaquetée, le choix « English » ne traduisait rien (le dictionnaire restait introuvable)."""
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    assert "mailia/locales" in (root / "build_mac.sh").read_text(encoding="utf-8")
    assert "mailia\\locales" in (root / "build_windows.bat").read_text(encoding="utf-8")
    assert (root / "mailia" / "locales" / "en.json").is_file()
