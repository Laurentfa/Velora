"""Dictée avec Whisper : téléchargement du modèle (vérifié, repris, annulable), découpage de la parole aux pauses, interface (accord, progression, réglages)."""
import asyncio
import hashlib
import http.server
import queue
import threading
from dataclasses import replace

import numpy as np
import pytest
from nicegui import events, ui
from nicegui.testing import User

import main
from mailia import dictation, sync, whisper_engine

PAYLOAD = bytes(range(256)) * 4000  # ~1 Mo


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        data = PAYLOAD
        rng = self.headers.get("Range")
        if rng and rng.startswith("bytes=") and self.server.allow_range:
            start = int(rng[6:].split("-")[0])
            self.send_response(206)
            body = data[start:]
        else:
            self.send_response(200)
            body = data
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def server(monkeypatch):
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    srv.allow_range = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    tiny = whisper_engine.Model("tiny-test", "ggml-test.bin", len(PAYLOAD), hashlib.sha256(PAYLOAD).hexdigest(), "Test", "")
    monkeypatch.setitem(whisper_engine.MODELS, "tiny-test", tiny)
    monkeypatch.setattr(whisper_engine, "BASE_URL", f"http://127.0.0.1:{srv.server_address[1]}/")
    whisper_engine.remove("tiny-test")
    yield srv
    srv.shutdown()
    whisper_engine.remove("tiny-test")


def test_download_verifies_and_installs(server):
    seen = []
    whisper_engine.download("tiny-test", progress=lambda d, t: seen.append(d))
    assert whisper_engine.is_installed("tiny-test") and seen[-1] == len(PAYLOAD)
    assert not whisper_engine.model_path("tiny-test").with_name("ggml-test.bin.part").exists()


def test_download_resumes_a_partial_file(server):
    part = whisper_engine.model_path("tiny-test").with_name("ggml-test.bin.part")
    part.write_bytes(PAYLOAD[:300_000])
    first = []
    whisper_engine.download("tiny-test", progress=lambda d, t: first.append(d))
    assert first[0] > 300_000 and whisper_engine.is_installed("tiny-test")  # on est reparti d'où on s'était arrêté


def test_download_restarts_when_the_server_cannot_resume(server):
    server.allow_range = False
    part = whisper_engine.model_path("tiny-test").with_name("ggml-test.bin.part")
    part.write_bytes(PAYLOAD[:300_000])
    whisper_engine.download("tiny-test")
    assert whisper_engine.is_installed("tiny-test")


def test_a_corrupted_download_is_rejected_and_removed(server, monkeypatch):
    bad = replace(whisper_engine.MODELS["tiny-test"], sha256="0" * 64)
    monkeypatch.setitem(whisper_engine.MODELS, "tiny-test", bad)
    with pytest.raises(whisper_engine.DownloadError, match="altéré"):
        whisper_engine.download("tiny-test")
    assert not whisper_engine.is_installed("tiny-test") and not whisper_engine.model_path("tiny-test").with_name("ggml-test.bin.part").exists()


def test_cancel_keeps_the_partial_file_for_later(server):
    cancel = threading.Event()

    def progress(done, total):
        cancel.set()
    with pytest.raises(whisper_engine.DownloadError, match="interrompu"):
        whisper_engine.download("tiny-test", progress=progress, cancel=cancel)
    assert not whisper_engine.is_installed("tiny-test")


def test_not_enough_disk_space_is_explained(server, monkeypatch):
    monkeypatch.setattr(whisper_engine, "free_space", lambda: 10)
    with pytest.raises(whisper_engine.DownloadError, match="espace libre"):
        whisper_engine.download("tiny-test")


def test_unreachable_server_is_explained(monkeypatch):
    monkeypatch.setattr(whisper_engine, "BASE_URL", "http://127.0.0.1:1/")
    with pytest.raises(whisper_engine.DownloadError, match="impossible"):
        whisper_engine.download("rapide")  # n'écrit rien : la connexion échoue avant


def test_pinned_models_are_sane():
    for m in whisper_engine.MODELS.values():
        assert len(m.sha256) == 64 and m.size > 10_000_000 and m.file.endswith(".bin")
    assert whisper_engine.DEFAULT_MODEL in whisper_engine.MODELS


def test_phantom_text_on_silence_is_dropped():
    c = whisper_engine.clean_text
    assert c("Bonjour tout le monde.") == "Bonjour tout le monde."
    assert c("Sous-titrage ST' 501") == "" and c("Merci d'avoir regardé cette vidéo !") == "" and c(" ... ") == "" and c("") == ""


# ---------------------------------------------------------------- découpage de la parole
class FakeMic:
    """Un micro qui joue un scénario : liste de (durée en s, « parole » ou « silence »), puis du silence tant qu'on n'arrête pas."""

    def __init__(self, script):
        self.blocks = []
        for dur, kind in script:
            amp = 0.2 if kind == "speech" else 0.0005
            self.blocks += [(np.random.default_rng(1).standard_normal(1600) * amp).astype("float32") for _ in range(int(dur * 10))]
        self.opened = self.closed = False

    def open(self):
        self.opened = True

    def read(self, timeout=0.3):
        if self.blocks:
            return self.blocks.pop(0)
        threading.Event().wait(0.01)
        return np.zeros(1600, dtype="float32") + 0.0005 if timeout > 0.1 else None

    def close(self):
        self.closed = True


def drain(s, until, timeout=10):
    out = []
    while True:
        ev = s.events.get(timeout=timeout)
        out.append(ev)
        if ev["t"] in until:
            return out


def session(script, texts):
    calls = []

    def runner_factory(path):
        def run(audio):
            calls.append(len(audio) / 16000)
            return texts[min(len(calls), len(texts)) - 1]
        return run
    s = whisper_engine.WhisperSession("rapide", mic=FakeMic(script), runner_factory=runner_factory)
    s.PARTIAL_EVERY = 0.5
    return s, calls


def test_each_pause_ends_a_sentence_and_partials_come_while_speaking(monkeypatch):
    monkeypatch.setattr(whisper_engine, "keep_runner", lambda *a, **k: None)
    s, calls = session([(0.5, "silence"), (2.0, "speech"), (1.2, "silence"), (1.0, "speech"), (1.2, "silence")], ["bon", "bonjour", "Bonjour à tous.", "Merci", "Merci.", "Merci.", "Merci."])
    s.start()
    evs = drain(s, {"final"}, 20)
    evs += drain(s, {"final"}, 20)
    s.stop()
    evs += drain(s, {"done", "error"}, 20)
    kinds = [e["t"] for e in evs]
    assert kinds[:2] == ["loading", "ready"] and "partial" in kinds and kinds.count("final") == 2 and kinds[-1] == "done"
    assert s.mic.opened and s.mic.closed


def test_silence_alone_produces_nothing_and_stopping_ends_cleanly(monkeypatch):
    monkeypatch.setattr(whisper_engine, "keep_runner", lambda *a, **k: None)
    s, calls = session([(2.0, "silence")], ["ne devrait jamais sortir"])
    s.start()
    assert drain(s, {"ready"})[-1]["t"] == "ready"
    threading.Event().wait(0.5)
    s.stop()
    rest = drain(s, {"done", "error"})
    assert rest[-1]["t"] == "done" and not [e for e in rest if e["t"] in ("partial", "final")] and calls == []


def test_stopping_during_speech_still_delivers_the_last_words(monkeypatch):
    monkeypatch.setattr(whisper_engine, "keep_runner", lambda *a, **k: None)
    s, calls = session([(0.3, "silence"), (1.5, "speech")], ["Dernier passage."])
    s.start()
    drain(s, {"partial"}, 20)
    s.stop()
    rest = drain(s, {"done", "error"}, 20)
    assert [e["text"] for e in rest if e["t"] == "final"] == ["Dernier passage."] and rest[-1]["t"] == "done"


def test_a_failing_microphone_is_reported(monkeypatch):
    class Broken(FakeMic):
        def open(self):
            raise OSError("pas de micro")
    s = whisper_engine.WhisperSession("rapide", mic=Broken([]), runner_factory=lambda p: (lambda a: ""))
    s.start()
    ev = drain(s, {"error"})[-1]
    assert ev["t"] == "error" and "pas de micro" in ev["msg"]


def test_engine_choice(monkeypatch):
    monkeypatch.setattr(dictation, "supported", lambda: True)
    assert dictation.engine() == "apple"
    monkeypatch.setattr(dictation, "supported", lambda: False)
    monkeypatch.setattr(whisper_engine, "libs_ok", lambda: True)
    assert dictation.engine() == "whisper" and dictation.usable()
    monkeypatch.setattr(whisper_engine, "libs_ok", lambda: False)
    assert dictation.engine() == "" and not dictation.usable()


# ---------------------------------------------------------------- interface
@pytest.fixture
def box(monkeypatch):
    from datetime import datetime

    from mailia import db
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


async def test_first_use_asks_before_downloading_then_dictates(user: User, box, monkeypatch) -> None:
    class Fake:
        def __init__(self):
            self.events = queue.Queue()
            self.finished = False

        def start(self):
            self.events.put({"t": "ready"})

        def stop(self):
            self.events.put({"t": "done"})

        def kill(self):
            pass
    sess = Fake()
    state = {"installed": False, "downloaded": 0}
    monkeypatch.setattr(dictation, "usable", lambda: True)
    monkeypatch.setattr(dictation, "engine", lambda: "whisper")
    monkeypatch.setattr(dictation, "check", lambda locale="fr-FR": {"available": True, "installed": state["installed"]})
    monkeypatch.setattr(dictation, "new_session", lambda: sess)

    def fake_download(key, progress=None, cancel=None):
        progress(1000, 2000)
        state["installed"] = True
        state["downloaded"] += 1
    monkeypatch.setattr(whisper_engine, "download", fake_download)
    await user.open("/")
    b = box[-1]
    b.new_message()
    await asyncio.sleep(0.4)
    click(user, "dictate-body")
    await user.should_see("Télécharger le modèle de dictée")
    await user.should_see("huggingface.co")
    assert state["downloaded"] == 0  # rien ne se télécharge sans le clic
    click(user, "dictate-download")
    await asyncio.sleep(1.0)
    assert state["downloaded"] == 1 and b.dict_state and b.dict_state["session"] is sess


async def test_declining_the_download_starts_nothing(user: User, box, monkeypatch) -> None:
    monkeypatch.setattr(dictation, "usable", lambda: True)
    monkeypatch.setattr(dictation, "engine", lambda: "whisper")
    monkeypatch.setattr(dictation, "check", lambda locale="fr-FR": {"available": True, "installed": False})
    called = []
    monkeypatch.setattr(whisper_engine, "download", lambda *a, **k: called.append(1))
    await user.open("/")
    b = box[-1]
    b.new_message()
    await asyncio.sleep(0.4)
    click(user, "dictate-body")
    await user.should_see("Télécharger le modèle de dictée")
    click(user, "dictate-cancel")
    await asyncio.sleep(0.5)
    assert not called and b.dict_state is None


async def test_a_failed_download_can_be_retried(user: User, box, monkeypatch) -> None:
    monkeypatch.setattr(dictation, "usable", lambda: True)
    monkeypatch.setattr(dictation, "engine", lambda: "whisper")
    monkeypatch.setattr(dictation, "check", lambda locale="fr-FR": {"available": True, "installed": False})

    def failing(key, progress=None, cancel=None):
        raise whisper_engine.DownloadError("Téléchargement impossible : hors connexion")
    monkeypatch.setattr(whisper_engine, "download", failing)
    await user.open("/")
    b = box[-1]
    b.new_message()
    await asyncio.sleep(0.4)
    click(user, "dictate-body")
    await user.should_see("Télécharger le modèle de dictée")
    click(user, "dictate-download")
    await user.should_see("hors connexion")
    await user.should_see("Réessayer")
    assert b.dict_state is None


def test_a_slow_computer_stops_the_provisional_text_but_still_transcribes_at_pauses(monkeypatch):
    monkeypatch.setattr(whisper_engine, "keep_runner", lambda *a, **k: None)
    clock = {"t": 0.0}
    monkeypatch.setattr(whisper_engine.time, "monotonic", lambda: clock["t"])

    def factory(path):
        def run(audio):
            clock["t"] += len(audio) / 16000 * 1.5  # 1,5 fois plus lent que la parole
            return "texte"
        return run
    s = whisper_engine.WhisperSession("rapide", mic=FakeMic([(0.3, "silence"), (4.0, "speech"), (1.2, "silence")]), runner_factory=factory)
    s.PARTIAL_EVERY = 0.5
    s.start()
    evs = drain(s, {"final"}, 20)
    s.stop()
    evs += drain(s, {"done", "error"}, 20)
    kinds = [e["t"] for e in evs]
    assert "slow" in kinds and kinds.count("partial") <= 2 and "final" in kinds


def test_provisional_text_only_reads_the_last_ten_seconds(monkeypatch):
    monkeypatch.setattr(whisper_engine, "keep_runner", lambda *a, **k: None)
    lengths = []

    def factory(path):
        def run(audio):
            lengths.append(len(audio) / 16000)
            return "mot"
        return run
    s = whisper_engine.WhisperSession("rapide", mic=FakeMic([(0.3, "silence"), (14.0, "speech")]), runner_factory=factory)
    s.PARTIAL_EVERY = 0.5
    s.start()
    drain(s, {"ready"})
    for _ in range(40):
        threading.Event().wait(0.1)
        if len(lengths) >= 20:
            break
    s.stop()
    drain(s, {"done", "error"}, 30)
    assert lengths and max(lengths[:-1] or [0]) <= 10.05  # sauf le passage définitif de fin


def test_microphone_test_reports_level_text_and_speed(monkeypatch):
    class Mic(FakeMic):
        device = None
    mic = Mic([(1.0, "speech")])
    levels = []
    r = whisper_engine.test_microphone(0.5, mic=mic, runner_factory=lambda p: (lambda a: "Bonjour test."), on_level=lambda peak, frac: levels.append(peak))
    assert r["text"] == "Bonjour test." and r["peak"] > 0.1 and r["audio_s"] > 0.3 and r["took"] >= 0 and levels


def test_microphone_test_reports_a_broken_microphone():
    class Broken(FakeMic):
        def open(self):
            raise OSError("micro absent")
    r = whisper_engine.test_microphone(0.3, mic=Broken([]), runner_factory=lambda p: (lambda a: ""))
    assert "micro absent" in r["error"]


def test_chosen_device_is_read_from_settings():
    from mailia import db
    db.kv_set("dictation_device", "3")
    assert whisper_engine.chosen_device() == 3
    db.kv_set("dictation_device", "")
    assert whisper_engine.chosen_device() is None


async def test_settings_tab_offers_the_microphone_choice_and_test(user: User, monkeypatch) -> None:
    from mailia import db
    from mailia.ui_settings import open_settings
    monkeypatch.setattr(dictation, "engine", lambda: "whisper")
    monkeypatch.setattr(dictation, "usable", lambda: True)
    monkeypatch.setattr(whisper_engine, "input_devices", lambda: [(2, "Micro du casque"), (5, "Micro intégré")])
    monkeypatch.setattr(whisper_engine, "device_name", lambda device=None: "Micro intégré")

    @ui.page("/settings")
    def page() -> None:
        open_settings(ui.element("div"), lambda acct: None)
    await user.open("/settings")
    await user.should_see("Dictée vocale")
    user.find("Dictée vocale").click()
    await user.should_see("Micro et diagnostic")
    await user.should_see("Tester le micro (5 s)")
    await user.should_see("Micro utilisé pour la dictée")
    assert db.kv_get("dictation_device") in (None, "")


def test_autotune_keeps_the_fastest_thread_count(monkeypatch):
    from mailia import db
    monkeypatch.setattr(whisper_engine.os, "cpu_count", lambda: 12)
    speed = {2: 5.0, 4: 3.0, 6: 2.0, 8: 2.6, 11: 4.0}
    clock = {"t": 0.0}

    def run(audio):
        clock["t"] += speed.get(run.state["n"], 9.0)
        return "x"
    run.state = {"n": 11}
    best, times = whisper_engine.autotune(run, np.zeros(16000, dtype="float32"), timer=lambda: clock["t"])
    assert best == 6 and run.state["n"] == 6 and times[6] == 2.0
    assert db.kv_get("dictation_threads") == "6" and whisper_engine.chosen_threads() == 6


def test_microphone_test_reports_engine_and_threads_and_skips_tuning_when_fast():
    class Mic(FakeMic):
        device = None

    def factory(path):
        def run(audio):
            return "Bonjour."
        run.state = {"n": 7}
        run.system_info = lambda: "AVX2 = 1"
        return run
    r = whisper_engine.test_microphone(0.3, mic=Mic([(1.0, "speech")]), runner_factory=factory)
    assert r["engine"] == "AVX2 = 1" and r["threads"] == 7 and not r.get("tuned")
