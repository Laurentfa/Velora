"""Dictée vocale : le pont avec le programme natif (sur un faux programme), le bouton micro et l'écriture du texte dicté (sur un faux moteur)."""
import asyncio
import queue
import sys
import textwrap
from datetime import datetime

import pytest
from nicegui import events, ui
from nicegui.testing import User

import main
from mailia import db, dictation, sync


class FakeSession:
    def __init__(self):
        self.events = queue.Queue()
        self.stopped = False
        self.killed = False
        self.finished = False

    def start(self):
        self.events.put({"t": "ready"})

    def stop(self):
        self.stopped = True
        self.events.put({"t": "done"})

    def kill(self):
        self.killed = True


@pytest.fixture
def box(monkeypatch):
    monkeypatch.setattr(sync, "run_cycle", lambda: None)
    ui.page("/")(main.index_page)
    db.kv_set("brief_day", datetime.now().strftime("%Y-%m-%d"))
    seen = []
    real = main.Mailbox.__init__
    monkeypatch.setattr(main.Mailbox, "__init__", lambda self, *a, **k: (real(self, *a, **k), seen.append(self))[0])
    return seen


@pytest.fixture
def fake(monkeypatch):
    s = FakeSession()
    monkeypatch.setattr(dictation, "usable", lambda: True)
    monkeypatch.setattr(dictation, "check", lambda locale="fr-FR": {"available": True, "installed": True})
    monkeypatch.setattr(dictation, "new_session", lambda: s)
    return s


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
    await asyncio.sleep(0.4)
    return b


async def test_no_microphone_button_when_dictation_is_not_possible(user: User, box, monkeypatch) -> None:
    monkeypatch.setattr(dictation, "usable", lambda: False)
    await open_composer(user, box)
    await user.should_not_see(marker="dictate-body")
    await user.should_not_see(marker="dictate-ins")


async def test_dictating_the_instruction_shows_the_live_text_then_keeps_each_sentence(user: User, box, fake) -> None:
    b = await open_composer(user, box)
    assert user.find(marker="dictate-ins").elements and user.find(marker="dictate-body").elements
    click(user, "dictate-ins")
    await asyncio.sleep(0.5)
    assert b.dict_state and b.dict_state["target"] == "ins"
    fake.events.put({"t": "partial", "text": "relancer Mme"})
    await asyncio.sleep(0.5)
    assert b.draft["ins"] == "relancer Mme"  # provisoire : visible tout de suite
    fake.events.put({"t": "partial", "text": "Relancer Mme Martin"})
    fake.events.put({"t": "final", "text": "Relancer Mme Martin pour le devis."})
    fake.events.put({"t": "partial", "text": "Ton"})
    await asyncio.sleep(0.6)
    assert b.draft["ins"] == "Relancer Mme Martin pour le devis. Ton"  # la phrase finie est gardée, la suivante est provisoire
    fake.events.put({"t": "final", "text": "Ton aimable."})
    await asyncio.sleep(0.6)
    assert b.draft["ins"] == "Relancer Mme Martin pour le devis. Ton aimable."


async def test_dictating_the_body_updates_the_live_text_in_the_editor(user: User, box, fake, monkeypatch) -> None:
    from mailia import ui_dictation
    calls = []
    real = ui_dictation.ui.run_javascript
    monkeypatch.setattr(ui_dictation.ui, "run_javascript", lambda js, **k: (calls.append(js), real(js, **k))[1])
    b = await open_composer(user, box)
    click(user, "dictate-body")
    await asyncio.sleep(0.5)
    fake.events.put({"t": "partial", "text": "bon"})
    fake.events.put({"t": "partial", "text": "bonjour"})
    fake.events.put({"t": "final", "text": "Bonjour."})
    await asyncio.sleep(0.6)
    live = [c for c in calls if "mailiaLive" in c]
    assert live[0].endswith('"", "start")')  # le repère est posé dès le début
    assert live[1].endswith('"bon", "partial")') and live[2].endswith('"bonjour", "partial")') and live[3].endswith('"Bonjour.", "final")')
    click(user, "dictate-body")
    await asyncio.sleep(0.6)
    assert b.dict_state is None
    assert [c for c in calls if "mailiaLive" in c][-1].endswith('"", "end")')  # à la fin, le repère est retiré


async def test_a_second_click_stops_and_ends_cleanly(user: User, box, fake) -> None:
    b = await open_composer(user, box)
    click(user, "dictate-body")
    await asyncio.sleep(0.5)
    click(user, "dictate-body")
    await asyncio.sleep(0.6)
    assert fake.stopped and b.dict_state is None


async def test_an_error_ends_the_dictation_and_tells_why(user: User, box, fake) -> None:
    b = await open_composer(user, box)
    click(user, "dictate-body")
    await asyncio.sleep(0.5)
    fake.events.put({"t": "error", "code": "mic_denied", "msg": ""})
    await asyncio.sleep(0.6)
    assert b.dict_state is None
    await user.should_see("L'accès au micro est refusé")


async def test_leaving_the_composer_stops_the_microphone(user: User, box, fake) -> None:
    b = await open_composer(user, box)
    click(user, "dictate-ins")
    await asyncio.sleep(0.5)
    b.new = False
    b.compose = False
    await asyncio.sleep(0.6)
    assert fake.killed and b.dict_state is None


def test_session_reads_events_from_the_helper_and_stops_it(tmp_path):
    script = tmp_path / "fake_helper.py"
    script.write_text(textwrap.dedent("""
        import json, sys
        print(json.dumps({"t": "ready"}), flush=True)
        print(json.dumps({"t": "partial", "text": "bon"}), flush=True)
        for line in sys.stdin:
            if line.strip() == "stop":
                break
        print(json.dumps({"t": "final", "text": "Bonjour."}), flush=True)
        print(json.dumps({"t": "done"}), flush=True)
    """))

    class S(dictation.Session):
        def command(self):
            return [sys.executable, str(script)]

    s = S()
    s.start()
    got = [s.events.get(timeout=10), s.events.get(timeout=10)]
    assert [e["t"] for e in got] == ["ready", "partial"]
    s.stop()
    rest = [s.events.get(timeout=10), s.events.get(timeout=10)]
    assert rest[0] == {"t": "final", "text": "Bonjour."} and rest[1]["t"] == "done"


def test_session_reports_a_helper_that_dies(tmp_path):
    script = tmp_path / "dies.py"
    script.write_text("import sys; sys.exit(3)")

    class S(dictation.Session):
        def command(self):
            return [sys.executable, str(script)]

    s = S()
    s.start()
    ev = s.events.get(timeout=10)
    assert ev["t"] == "error" and ev["code"] == "crash"


def test_session_without_helper_reports_unavailable(monkeypatch):
    monkeypatch.setattr(dictation, "helper_path", lambda: None)
    s = dictation.Session()
    s.start()
    assert s.events.get_nowait()["code"] == "unavailable"
    monkeypatch.setattr(dictation, "supported", lambda: False)
    monkeypatch.setattr(dictation.whisper_engine if hasattr(dictation, "whisper_engine") else __import__("mailia.whisper_engine", fromlist=["x"]), "libs_ok", lambda: False)
    assert dictation.check() == {"available": False, "installed": False}


def test_supported_requires_a_mac_with_macos_26(monkeypatch, tmp_path):
    helper = tmp_path / "velora-dictate"
    helper.write_text("")
    monkeypatch.setattr(dictation, "helper_path", lambda: helper)
    monkeypatch.setattr(dictation.sys, "platform", "darwin")
    monkeypatch.setattr(dictation.platform, "mac_ver", lambda: ("26.1", ("", "", ""), ""))
    assert dictation.supported()
    monkeypatch.setattr(dictation.platform, "mac_ver", lambda: ("15.5", ("", "", ""), ""))
    assert not dictation.supported()
    monkeypatch.setattr(dictation.sys, "platform", "win32")
    assert not dictation.supported()
