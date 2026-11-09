"""Conversation : répondre ou transférer n'importe quel message, pas seulement le dernier."""
import pytest
from conftest import add_message
from nicegui import ui

import main
from mailia import ai, config, queries, sync


@pytest.fixture
def box(monkeypatch):
    monkeypatch.setattr(sync, "run_cycle", lambda: None)
    monkeypatch.setattr(config, "ai_enabled", lambda: True)
    monkeypatch.setattr(ai, "summarize_thread", lambda ckey: "Alice, Bob et Carol règlent le dossier X.")
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


async def test_a_draft_is_not_part_of_the_conversation(user, box) -> None:
    from mailia import db, queries
    with db.conn() as c:
        c.execute("INSERT OR REPLACE INTO folders(account,key,name,path,role,sync) VALUES('testbox','Drafts','Brouillons','Brouillons','drafts',0)")
    add_message("testbox|INBOX|0", sender="alice@example.com", subject="Dossier X", ckey="testbox|conv", date="2026-10-01T10:00:00+00:00")
    add_message("testbox|Drafts|1", sender="", subject="Dossier X", ckey="testbox|conv", date="2026-10-02T10:00:00+00:00", folder="Drafts")
    assert [m["id"] for m in queries.thread("testbox|conv")] == ["testbox|INBOX|0"]
    assert queries.list_messages()[0]["conv_n"] == 1
    await user.open("/")
    await box[-1].open("testbox|INBOX|0")
    await user.should_not_see("Brouillon")
    assert queries.thread("testbox|nope") == []


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

    async def show_preview(self, name, data, key, download, excluded=False, send=None):
        shown.append((name, data, excluded))
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


async def test_elsewhere_a_pdf_of_the_message_text_is_previewed(user, box, monkeypatch) -> None:
    shown = await _print_first_message(user, box, monkeypatch, "linux", lambda html, pdf, timeout=40.0: False)
    name, data, excluded = shown[-1]
    text = _pdf_text(data)
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


async def test_on_windows_the_pdf_of_the_message_opens_in_the_default_pdf_application(user, box, monkeypatch) -> None:
    opened = []
    monkeypatch.setattr(main, "open_to_print", lambda name, data: opened.append((name, data)))
    # l'aperçu de Velora ne doit pas s'ouvrir : _print_first_message attend un aperçu, on lui en donne la trace sans le déclencher
    monkeypatch.setattr(main, "print_platform", lambda: "win32")
    monkeypatch.setattr(main, "SERVER_MODE", False)
    previews = []

    async def show_preview(self, *a, **k):
        previews.append(a)
    monkeypatch.setattr(main.Mailbox, "show_preview", show_preview)
    add_message("testbox|INBOX|0", sender="alice@example.com", subject="Dossier X", ckey="testbox|conv", date="2026-10-01T10:00:00+00:00", body="Premier message")
    add_message("testbox|INBOX|1", sender="bob@example.com", subject="Re: Dossier X", ckey="testbox|conv", date="2026-10-02T10:00:00+00:00", body="Deuxième message")
    await user.open("/")
    await box[-1].open("testbox|INBOX|1")
    await user.should_see("Dossier X")
    el = user.find(marker="tc-print-testbox|INBOX|0").elements.pop()
    with user.client:
        for lid in list(el._event_listeners):
            el._handle_event({"listener_id": lid, "args": {}})
    from tests.conftest import wait_until
    await wait_until(lambda: opened)
    name, data = opened[0]
    assert name == "Dossier X.pdf" and data.startswith(b"%PDF") and "Premier message" in _pdf_text(data) and "Deuxième message" not in _pdf_text(data)
    assert not previews  # pas d'aperçu dans Velora : le PDF s'ouvre dans l'application par défaut


async def test_a_conversation_is_summarised_on_opening_without_any_click(user, box) -> None:
    _thread()
    await user.open("/")
    await box[-1].open("testbox|INBOX|2")
    await user.should_see("Résumé de la conversation")
    await user.should_see("règlent le dossier X")


async def test_a_single_message_gets_no_conversation_summary(user, box) -> None:
    add_message("testbox|INBOX|0", sender="alice@example.com", subject="Seul", ckey="testbox|seul", date="2026-10-01T10:00:00+00:00")
    await user.open("/")
    await box[-1].open("testbox|INBOX|0")
    await user.should_see("Seul")
    await user.should_not_see("Résumé de la conversation")


async def test_a_two_message_conversation_is_not_summarised_automatically(user, box) -> None:
    for i, who in enumerate(["alice@example.com", "bob@example.com"]):
        add_message(f"testbox|INBOX|{i}", sender=who, subject="Court échange", ckey="testbox|court", date=f"2026-10-0{i + 1}T10:00:00+00:00")
    await user.open("/")
    await box[-1].open("testbox|INBOX|1")
    await user.should_see("Court échange")
    await user.should_not_see("Résumé de la conversation")


async def test_forward_opens_the_editor_at_once_and_attachments_arrive_one_by_one(user, box, monkeypatch) -> None:
    """Un mail à nombreuses pièces jointes : la fenêtre de rédaction s'ouvre sans attendre ; l'envoi attend la dernière pièce ; une pièce qui échoue n'arrête pas les autres."""
    import asyncio
    import json
    from tests.conftest import wait_until
    atts = [{"name": f"p{i}.pdf", "type": "application/pdf", "size": 1000} for i in range(4)] + [{"name": "logo.png", "type": "image/png", "size": 50, "inline": True}]
    add_message("testbox|INBOX|0", sender="alice@example.com", subject="Dossier X", att_json=json.dumps(atts), has_att=1)
    gate = asyncio.Event()
    fetched = []

    def fetch(m, a):
        fetched.append(a["name"])
        if a["name"] == "p2.pdf":
            raise OSError("serveur")
        return b"x" * 10, a["name"]
    monkeypatch.setattr(sync, "fetch_attachment", fetch)
    real_io = main.run.io_bound
    waited = []

    async def slow(fn, *a, **k):
        if getattr(fn, "__name__", "") == "fetch":
            waited.append(1)
            await gate.wait()  # chaque pièce attend le feu vert : la fenêtre doit déjà être ouverte
            gate.clear()
        return await real_io(fn, *a, **k)
    from mailia import ui_drafts
    monkeypatch.setattr(ui_drafts.run, "io_bound", slow)
    await user.open("/")
    b = box[-1]
    await b.open("testbox|INBOX|0")
    await b.forward()
    assert b.new and b.draft["files"] == [] and b.fwd_loading()  # ouverte avant la première pièce
    await user.should_see("Chargement des pièces jointes")
    sent = []
    monkeypatch.setattr(b, "undo_delay", lambda: 0)
    b.draft.update(to="bob@example.com")
    await b.send()  # l'envoi est refusé tant que tout n'est pas arrivé
    await user.should_see("encore en cours de chargement")
    for _ in range(4):
        await wait_until(lambda: waited)
        waited.clear()
        gate.set()
        await asyncio.sleep(0.2)
    await wait_until(lambda: not b.fwd_loading())
    assert [f[0] for f in b.draft["files"]] == ["p0.pdf", "p1.pdf", "p3.pdf"] and fetched == ["p0.pdf", "p1.pdf", "p2.pdf", "p3.pdf"]  # dans l'ordre ; la pièce en échec est signalée ; l'image du texte n'est pas rejointe
    assert not sent
    await user.should_see("non reprise")


async def test_scan_dialog_survives_a_scan_with_blank_pages(user, box, monkeypatch) -> None:
    """Après une numérisation, la vérification des pages vides plantait (« await » dans un générateur) : l'erreur s'affichait dans le journal à chaque scan."""
    import io
    from PIL import Image
    from mailia import scan
    white = io.BytesIO()
    Image.new("RGB", (300, 400), "white").save(white, "JPEG")
    monkeypatch.setattr(scan, "scan", lambda base, source, mode, dpi: [white.getvalue()])
    monkeypatch.setattr(scan, "discover", lambda t=3.0: [])
    monkeypatch.setattr(scan, "known", lambda: [{"name": "Faux", "base": "http://192.168.1.9/eSCL"}])
    add_message("testbox|INBOX|0", sender="alice@example.com", subject="Dossier X")
    await user.open("/")
    b = box[-1]
    await b.open("testbox|INBOX|0")
    await b.scan_dialog()
    await user.should_see("Scanner un document")
    user.find("Numériser").click()
    await user.should_see("semblent vides")  # l'avertissement apparaît, sans erreur dans le journal (le test échoue sur toute erreur)


async def test_attachment_buttons_never_take_the_keyboard(user, box) -> None:
    """Entrée dans l'éditeur ouvrait l'aperçu d'une pièce jointe après un ajout : ces boutons (aperçu, retrait) ne reçoivent plus le clavier."""
    add_message("testbox|INBOX|0", sender="alice@example.com", subject="Dossier X", ckey="testbox|conv")
    await user.open("/")
    b = box[-1]
    await b.open("testbox|INBOX|0")
    await b.forward()
    await user.should_see("Joindre un fichier")
    b.draft["files"].append(("a.pdf", b"%PDF-1.4", "application/pdf"))
    b.files_view.refresh()
    await user.should_see("a.pdf")
    btns = [e for e in b.host.client.elements.values() if isinstance(e, ui.button) and e._props.get("icon") in ("visibility", "close")
            and not e.is_deleted and "shrink-0" in e._classes]
    assert btns and all("tabindex" in e._props and str(e._props["tabindex"]) == "-1" for e in btns), [e._props for e in btns]
