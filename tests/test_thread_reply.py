"""Conversation : répondre ou transférer n'importe quel message, pas seulement le dernier."""
import pytest
from conftest import add_message
from nicegui import ui

import main
from mailia import config, queries, sync


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


def _thread() -> None:
    for i, who in enumerate(["alice@example.com", "bob@example.com", "carol@example.com"]):
        add_message(f"testbox|INBOX|{i}", sender=who, subject="Dossier X", ckey="testbox|conv", date=f"2026-10-0{i + 1}T10:00:00+00:00",
                    to_addrs="moi@example.org, dave@example.com")


async def test_each_message_has_reply_and_forward_and_replying_targets_that_message(user, box) -> None:
    _thread()
    await user.open("/")
    b = box[-1]
    await b.open("testbox|INBOX|2")
    await user.should_see("Dossier X")
    for i in range(3):
        assert user.find(marker=f"tc-reply-testbox|INBOX|{i}").elements
        assert user.find(marker=f"tc-forward-testbox|INBOX|{i}").elements
    el = user.find(marker="tc-reply-testbox|INBOX|0").elements.pop()
    with user.client:  # le simulateur ne déclenche que « click » : le bouton écoute « click.stop » (pour ne pas replier la carte), on envoie l'événement tel quel
        for lid in list(el._event_listeners):
            el._handle_event({"listener_id": lid, "args": {}})
    from tests.conftest import wait_until
    await wait_until(lambda: b.compose)
    assert b.cur["id"] == "testbox|INBOX|0" and b.draft["reply_to"] == "testbox|INBOX|0" and "alice@example.com" in b.draft["to"]


async def test_a_draft_in_the_thread_is_labelled_not_shown_as_an_unknown_sender(user, box) -> None:
    from mailia import db
    with db.conn() as c:
        c.execute("INSERT OR REPLACE INTO folders(account,key,name,path,role,sync) VALUES('testbox','Drafts','Brouillons','Brouillons','drafts',0)")
    add_message("testbox|INBOX|0", sender="alice@example.com", subject="Dossier X", ckey="testbox|conv", date="2026-10-01T10:00:00+00:00")
    add_message("testbox|Drafts|1", sender="", subject="Dossier X", ckey="testbox|conv", date="2026-10-02T10:00:00+00:00", folder="Drafts")
    await user.open("/")
    await box[-1].open("testbox|INBOX|0")
    await user.should_see("Brouillon")
    await user.should_see("Moi")


async def test_module_tools_stay_visible_when_forwarding(user, box, monkeypatch) -> None:
    from mailia import modules
    monkeypatch.setitem(modules.slots, "reader", [{"module": "essai", "order": 1, "build": lambda api, mb, msg: ui.label(f"CARTE {msg['id']}")}])
    add_message("testbox|INBOX|0", sender="alice@example.com", subject="Dossier X", ckey="testbox|conv")
    await user.open("/")
    b = box[-1]
    await b.open("testbox|INBOX|0")
    await user.should_see("CARTE testbox|INBOX|0")
    await b.forward()
    assert b.new
    await user.should_see("CARTE testbox|INBOX|0")


async def _print_first_message(user, box, monkeypatch, platform: str, native):
    """Clique sur l'imprimante du premier message d'une conversation de deux ; rend ce que l'aperçu a reçu : [(nom, octets, sans_resume_ia)]."""
    monkeypatch.setattr(main, "print_platform", lambda: platform)
    monkeypatch.setattr(main, "SERVER_MODE", False)
    monkeypatch.setattr(main, "native_html_to_pdf", native)
    shown = []

    async def show_preview(self, name, data, key, download, excluded=False, send=None, printable=True):
        shown.append((name, data, excluded, printable))
    monkeypatch.setattr(main.Mailbox, "show_preview", show_preview)
    add_message("testbox|INBOX|0", sender="alice@example.com", subject="Dossier X", ckey="testbox|conv", date="2026-10-01T10:00:00+00:00", body="Premier message d’Alice")
    add_message("testbox|INBOX|1", sender="bob@example.com", subject="Re: Dossier X", ckey="testbox|conv", date="2026-10-02T10:00:00+00:00", body="Deuxième message")
    await user.open("/")
    await box[-1].open("testbox|INBOX|1")
    await user.should_see("Dossier X")
    el = user.find(marker="tc-print-testbox|INBOX|0").elements.pop()
    with user.client:  # le bouton écoute « click.stop » : on envoie l'événement tel quel
        for lid in list(el._event_listeners):
            el._handle_event({"listener_id": lid, "args": {}})
    from tests.conftest import wait_until
    await wait_until(lambda: shown)
    return shown


def _pdf_text(data: bytes) -> str:
    from io import BytesIO

    from pypdf import PdfReader
    return "".join(p.extract_text() for p in PdfReader(BytesIO(data)).pages)


@pytest.mark.parametrize("platform", ["win32", "linux"])
async def test_on_windows_and_elsewhere_a_pdf_of_the_message_text_is_previewed(user, box, monkeypatch, platform) -> None:
    shown = await _print_first_message(user, box, monkeypatch, platform, lambda html, pdf, timeout=40.0: False)
    name, data, excluded, printable = shown[-1]
    text = _pdf_text(data)
    assert printable is (platform != "win32")  # Windows : le PDF est seulement affiché, sans bouton « Imprimer »
    assert name == "Dossier X.pdf" and data.startswith(b"%PDF") and excluded is True  # aperçu sans résumé IA
    assert "Premier message" in text and "Deuxième message" not in text and "alice@example.com" in text


async def test_on_mac_the_pdf_made_by_the_window_is_shown_as_it_is(user, box, monkeypatch) -> None:
    import shutil
    made = []

    def fake(html, pdf, timeout=40.0):
        made.append((html.read_text(encoding="utf-8"), pdf))
        pdf.write_bytes(b"%PDF-1.4 fait par WebKit")
        return True
    shown = await _print_first_message(user, box, monkeypatch, "darwin", fake)
    shutil.rmtree(made[0][1].parent, ignore_errors=True)
    assert "Premier message" in made[0][0] and "Deuxième message" not in made[0][0]
    assert shown[0][1].startswith(b"%PDF-1.4 fait par")


async def test_on_mac_python_takes_over_when_the_window_cannot_make_the_pdf(user, box, monkeypatch) -> None:
    shown = await _print_first_message(user, box, monkeypatch, "darwin", lambda html, pdf, timeout=40.0: False)
    assert "Premier message" in _pdf_text(shown[-1][1])
