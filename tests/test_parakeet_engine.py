"""Dictée avec Parakeet : téléchargement de plusieurs fichiers (vérifiés, repris), normalisation du volume, session de dictée, choix du moteur, comparaison."""
import asyncio
import hashlib
import http.server
import queue
import threading
from datetime import datetime

import numpy as np
import pytest
from nicegui import events, ui
from nicegui.testing import User

import main
from mailia import db, dictation, parakeet_engine, sync, whisper_engine

FILES = {"a.bin": bytes(range(256)) * 3000, "b.bin": b"petit fichier", "c.txt": b"x" * 5000}


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        name = self.path.rsplit("/", 1)[-1]
        data = FILES.get(name)
        if data is None:
            self.send_response(404)
            self.end_headers()
            return
        rng = self.headers.get("Range")
        if rng and rng.startswith("bytes="):
            self.send_response(206)
            data = data[int(rng[6:].split("-")[0]):]
        else:
            self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture
def server(monkeypatch):
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    files = tuple((n, len(d), hashlib.sha256(d).hexdigest()) for n, d in FILES.items())
    monkeypatch.setattr(parakeet_engine, "FILES", files)
    monkeypatch.setattr(parakeet_engine, "TOTAL", sum(f[1] for f in files))
    monkeypatch.setattr(parakeet_engine, "BASE_URL", f"http://127.0.0.1:{srv.server_address[1]}/")
    parakeet_engine.remove()
    yield srv
    srv.shutdown()
    parakeet_engine.remove()


def test_download_fetches_every_file_and_reports_overall_progress(server):
    seen = []
    parakeet_engine.download(progress=lambda d, t: seen.append((d, t)))
    assert parakeet_engine.is_installed() and seen[-1] == (parakeet_engine.TOTAL, parakeet_engine.TOTAL)
    assert all(a[0] <= b[0] for a, b in zip(seen, seen[1:]))  # la progression ne recule jamais


def test_download_resumes_and_skips_files_already_in_place(server):
    d = parakeet_engine.model_dir()
    d.mkdir(parents=True, exist_ok=True)
    (d / "b.bin").write_bytes(FILES["b.bin"])  # déjà là
    (d / "a.bin.part").write_bytes(FILES["a.bin"][:100_000])  # à moitié reçu
    parakeet_engine.download()
    assert parakeet_engine.is_installed()


def test_an_altered_file_is_rejected_and_the_model_stays_uninstalled(server, monkeypatch):
    bad = tuple((n, s, "0" * 64 if n == "b.bin" else h) for n, s, h in parakeet_engine.FILES)
    monkeypatch.setattr(parakeet_engine, "FILES", bad)
    with pytest.raises(whisper_engine.DownloadError, match="altéré"):
        parakeet_engine.download()
    assert not parakeet_engine.is_installed()


def test_remove_deletes_the_model(server):
    parakeet_engine.download()
    parakeet_engine.remove()
    assert not parakeet_engine.is_installed()


def test_pinned_files_are_sane():
    assert len(parakeet_engine.REVISION) == 40 and parakeet_engine.REVISION in parakeet_engine.BASE_URL
    for n, size, sha in parakeet_engine.FILES:
        assert size > 0 and len(sha) == 64
    assert parakeet_engine.TOTAL > 600_000_000


def test_volume_is_normalised_but_silence_stays_silent():
    n = parakeet_engine.normalize
    quiet = (np.sin(np.arange(16000) / 7) * 0.002).astype("float32")
    assert quiet.max() < 0.003 and np.array_equal(n(quiet), quiet)  # silence numérique : intact
    weak = (np.sin(np.arange(16000) / 7) * 0.01).astype("float32")
    out = n(weak)
    assert 0.05 < float(np.sqrt((out ** 2).mean())) < 0.07 and float(np.abs(out).max()) <= 1.0
    loud = np.clip(np.sin(np.arange(16000) / 7) * 3, -1, 1).astype("float32")
    assert float(np.abs(n(loud)).max()) <= 1.0 and len(n(np.zeros(0))) == 0


class FakeMic:
    def __init__(self, script):
        self.blocks = []
        for dur, kind in script:
            amp = 0.2 if kind == "speech" else 0.0005
            self.blocks += [(np.random.default_rng(1).standard_normal(1600) * amp).astype("float32") for _ in range(int(dur * 10))]

    def open(self):
        pass

    def read(self, timeout=0.3):
        if self.blocks:
            return self.blocks.pop(0)
        threading.Event().wait(0.01)
        return np.zeros(1600, dtype="float32") + 0.0005 if timeout > 0.1 else None

    def close(self):
        pass


def drain(s, until, timeout=20):
    out = []
    while True:
        ev = s.events.get(timeout=timeout)
        out.append(ev)
        if ev["t"] in until:
            return out


def test_the_session_dictates_with_parakeet_and_never_asks_for_a_prompt(monkeypatch):
    kept = []
    monkeypatch.setattr(parakeet_engine, "keep_runner", lambda *a, **k: kept.append(a))

    def factory(path):
        assert path == str(parakeet_engine.model_dir())
        def run(audio, fast=False):
            return "Bonjour tout le monde."
        run.needs_warmup = True
        return run
    s = parakeet_engine.ParakeetSession(mic=FakeMic([(0.3, "silence"), (2.0, "speech"), (1.2, "silence")]), runner_factory=factory)
    s.PARTIAL_EVERY = 0.5
    s.start()
    evs = drain(s, {"final"})
    s.stop()
    evs += drain(s, {"done", "error"})
    kinds = [e["t"] for e in evs]
    assert kinds[:2] == ["loading", "ready"] and "final" in kinds and kinds[-1] == "done" and s.prompt is None and kept


def test_engine_choice_between_whisper_and_parakeet(monkeypatch):
    monkeypatch.setattr(dictation, "supported", lambda: False)
    monkeypatch.setattr(whisper_engine, "libs_ok", lambda: True)
    monkeypatch.setattr(parakeet_engine, "libs_ok", lambda: True)
    assert dictation.engine() == "whisper"  # Whisper reste le choix par défaut tant que vous n'en décidez pas autrement
    db.kv_set("dictation_engine", "parakeet")
    assert dictation.engine() == "parakeet" and dictation.engine_module() is parakeet_engine
    monkeypatch.setattr(parakeet_engine, "libs_ok", lambda: False)
    assert dictation.engine() == "whisper"  # moteur choisi absent de cette installation : on se replie
    monkeypatch.setattr(whisper_engine, "libs_ok", lambda: False)
    assert dictation.engine() == ""
    monkeypatch.setattr(dictation, "supported", lambda: True)
    db.kv_set("dictation_engine", "parakeet")
    assert dictation.engine() == "apple"  # sur Mac (macOS 26), on garde Apple quoi qu'il arrive


def test_new_session_follows_the_engine(monkeypatch):
    monkeypatch.setattr(dictation, "engine", lambda: "parakeet")
    assert isinstance(dictation.new_session(), parakeet_engine.ParakeetSession)
    monkeypatch.setattr(dictation, "engine", lambda: "whisper")
    assert isinstance(dictation.new_session(), whisper_engine.WhisperSession) and not isinstance(dictation.new_session(), parakeet_engine.ParakeetSession)


def test_compare_runs_the_same_recording_through_both_engines():
    def maker(text, delay):
        def make():
            def run(audio, fast=False):
                return text
            run.needs_warmup = True
            return run
        return make
    r = whisper_engine.compare_microphone(0.5, mic=FakeMic([(1.0, "speech")]), factories={"whisper": maker("texte whisper", 0), "parakeet": maker("texte parakeet", 0)})
    assert [x["engine"] for x in r["results"]] == ["whisper", "parakeet"] and r["results"][1]["text"] == "texte parakeet" and r["audio_s"] > 0.3


def test_compare_needs_both_engines(monkeypatch):
    monkeypatch.setattr(whisper_engine, "is_installed", lambda key=None: False)
    monkeypatch.setattr(parakeet_engine, "is_installed", lambda key=None: False)
    assert "Installez les deux" in whisper_engine.compare_microphone(0.2, mic=FakeMic([]))["error"]


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


async def test_first_use_with_parakeet_asks_before_downloading_it(user: User, box, monkeypatch) -> None:
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
    state = {"installed": False, "downloads": 0}
    monkeypatch.setattr(dictation, "usable", lambda: True)
    monkeypatch.setattr(dictation, "engine", lambda: "parakeet")
    monkeypatch.setattr(dictation, "check", lambda locale="fr-FR": {"available": True, "installed": state["installed"]})
    monkeypatch.setattr(dictation, "new_session", lambda: sess)

    def fake_download(key=None, progress=None, cancel=None):
        progress(1000, 2000)
        state["installed"] = True
        state["downloads"] += 1
    monkeypatch.setattr(parakeet_engine, "download", fake_download)
    await user.open("/")
    b = box[-1]
    b.new_message()
    await asyncio.sleep(0.4)
    click(user, "dictate-body")
    await user.should_see("Télécharger le modèle de dictée")
    await user.should_see("Parakeet")
    assert state["downloads"] == 0
    click(user, "dictate-download")
    await asyncio.sleep(1.0)
    assert state["downloads"] == 1 and b.dict_state and b.dict_state["session"] is sess


async def test_names_are_corrected_after_the_fact_with_parakeet(user: User, box, monkeypatch) -> None:
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
    db.contact_save("a@x.fr", "Aurélie Kerjean", "manual")
    monkeypatch.setattr(dictation, "usable", lambda: True)
    monkeypatch.setattr(dictation, "engine", lambda: "parakeet")
    monkeypatch.setattr(dictation, "check", lambda locale="fr-FR": {"available": True, "installed": True})
    monkeypatch.setattr(dictation, "new_session", lambda: sess)
    await user.open("/")
    b = box[-1]
    b.new_message()
    await asyncio.sleep(0.4)
    click(user, "dictate-ins")
    await asyncio.sleep(0.5)
    sess.events.put({"t": "final", "text": "Relancer Madame Kerjan pour le devis."})
    await asyncio.sleep(0.6)
    assert b.draft["ins"] == "Relancer Madame Kerjean pour le devis."


async def test_settings_tab_lets_you_choose_between_the_two_engines(user: User, monkeypatch) -> None:
    from mailia.ui_settings import open_settings
    monkeypatch.setattr(dictation, "supported", lambda: False)
    monkeypatch.setattr(whisper_engine, "libs_ok", lambda: True)
    monkeypatch.setattr(parakeet_engine, "libs_ok", lambda: True)
    monkeypatch.setattr(whisper_engine, "input_devices", lambda: [(2, "Micro")])
    monkeypatch.setattr(whisper_engine, "device_name", lambda device=None: "Micro")

    @ui.page("/settings")
    def page() -> None:
        open_settings(ui.element("div"), lambda acct: None)
    await user.open("/settings")
    user.find("Dictée vocale").click()
    await user.should_see("Parakeet (NVIDIA)")
    await user.should_see("Whisper (OpenAI)")
    await user.should_see("Comparer Whisper et Parakeet (5 s)")
    assert db.kv_get("dictation_engine") in (None, "")


def test_errors_tell_what_failed_and_where(monkeypatch):
    class Broken:
        def open(self):
            pass

        def read(self, timeout=0.3):
            return None

        def close(self):
            pass

    def factory(path=None):
        import json
        json.loads("")  # le genre d'erreur obscure vue sur un vrai poste
    r = whisper_engine.test_microphone(0.2, mic=Broken(), runner_factory=factory)
    assert "Expecting value" in r["error"] and "JSONDecodeError" in r["error"] and ".py:" in r["error"]
    r = whisper_engine.compare_microphone(0.2, mic=Broken(), factories={"whisper": factory, "parakeet": factory})
    assert "JSONDecodeError" in r["error"]


def test_one_engine_failing_does_not_hide_the_other():
    def ok():
        def run(audio, fast=False):
            return "texte"
        run.needs_warmup = True
        return run

    def bad():
        def run(audio, fast=False):
            raise ValueError("moteur cassé")
        return run
    r = whisper_engine.compare_microphone(0.3, mic=FakeMic([(1.0, "speech")]), factories={"whisper": ok, "parakeet": bad})
    by = {x["engine"]: x["text"] for x in r["results"]}
    assert by["whisper"] == "texte" and "moteur cassé" in by["parakeet"] and "ValueError" in by["parakeet"]


def test_dictation_messages_keep_the_detail():
    assert "JSONDecodeError" in dictation.message("failed", "x [JSONDecodeError · a.py:1 (f)]")
    assert dictation.message("mic_denied") == dictation.ERRORS["mic_denied"]


def test_an_empty_or_broken_config_is_repaired_before_loading(tmp_path):
    import json
    (tmp_path / "config.json").write_text("", encoding="utf-8")
    parakeet_engine.ensure_config(str(tmp_path))
    assert json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))["model_type"] == "nemo-conformer-tdt"
    (tmp_path / "config.json").write_text('{"model_type": "autre", "x": 1}', encoding="utf-8")
    parakeet_engine.ensure_config(str(tmp_path))  # un fichier valide n'est pas touché
    assert json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))["model_type"] == "autre"
    (tmp_path / "config.json").unlink()
    parakeet_engine.ensure_config(str(tmp_path))  # absent : recréé
    assert (tmp_path / "config.json").exists()


async def test_when_parakeet_fails_to_start_velora_goes_back_to_whisper(user: User, box, monkeypatch) -> None:
    class Fake:
        def __init__(self):
            self.events = queue.Queue()
            self.finished = False

        def start(self):
            self.events.put({"t": "error", "code": "failed", "msg": "boom [JSONDecodeError · a.py:1 (f)]"})

        def stop(self):
            pass

        def kill(self):
            pass
    monkeypatch.setattr(dictation, "usable", lambda: True)
    monkeypatch.setattr(dictation, "engine", lambda: "parakeet")
    monkeypatch.setattr(dictation, "check", lambda locale="fr-FR": {"available": True, "installed": True})
    monkeypatch.setattr(dictation, "new_session", lambda: Fake())
    monkeypatch.setattr(whisper_engine, "libs_ok", lambda: True)
    monkeypatch.setattr(whisper_engine, "is_installed", lambda key=None: True)
    db.kv_set("dictation_engine", "parakeet")
    await user.open("/")
    b = box[-1]
    b.new_message()
    await asyncio.sleep(0.4)
    click(user, "dictate-body")
    await asyncio.sleep(0.8)
    await user.should_see("JSONDecodeError")
    await user.should_see("retour à Whisper")
    assert db.kv_get("dictation_engine") == "whisper" and b.dict_state is None


def test_a_corrupted_model_is_detected_deleted_and_reported(server, monkeypatch):
    import onnx_asr
    parakeet_engine.download()
    # un fichier de la bonne taille mais rempli de zéros (ce qu'une coupure peut laisser sous Windows)
    bad = parakeet_engine.model_dir() / "a.bin"
    bad.write_bytes(b"\0" * len(FILES["a.bin"]))
    assert parakeet_engine.is_installed()  # la taille seule ne le voit pas
    assert parakeet_engine.verify_files() == ["a.bin"]

    def boom(*a, **k):
        raise RuntimeError("INVALID_PROTOBUF")
    monkeypatch.setattr(onnx_asr, "load_model", boom)
    with pytest.raises(RuntimeError, match="abîmés \\(a.bin\\)"):
        parakeet_engine.load_runner(str(parakeet_engine.model_dir()))
    assert not bad.exists() and not parakeet_engine.is_installed()  # prêt à être retéléchargé


def test_an_engine_failure_with_intact_files_says_so(server, monkeypatch):
    import onnx_asr
    parakeet_engine.download()

    def boom(*a, **k):
        raise RuntimeError("autre problème")
    monkeypatch.setattr(onnx_asr, "load_model", boom)
    with pytest.raises(RuntimeError, match="fichiers sont intacts"):
        parakeet_engine.load_runner(str(parakeet_engine.model_dir()))
    assert parakeet_engine.is_installed()


def test_downloaded_files_are_flushed_to_disk(server, monkeypatch):
    calls = []
    real = whisper_engine.os.fsync
    monkeypatch.setattr(whisper_engine.os, "fsync", lambda fd: (calls.append(fd), real(fd))[1])
    parakeet_engine.download()
    assert len(calls) == len(parakeet_engine.FILES)


def test_reactivity_presets_drive_the_live_text(monkeypatch):
    assert parakeet_engine.reactivity() == parakeet_engine.DEFAULT_REACTIVITY == 3
    s = parakeet_engine.ParakeetSession(mic=FakeMic([]), runner_factory=lambda p: (lambda a: ""))
    assert (s.PARTIAL_EVERY, s.PARTIAL_WINDOW) == (0.3, 6.0)
    db.kv_set("dictation_reactivity", "4")
    s = parakeet_engine.ParakeetSession(mic=FakeMic([]), runner_factory=lambda p: (lambda a: ""))
    assert (s.PARTIAL_EVERY, s.PARTIAL_WINDOW) == (0.15, 5.0)
    db.kv_set("dictation_reactivity", "1")
    s = parakeet_engine.ParakeetSession(mic=FakeMic([]), runner_factory=lambda p: (lambda a: ""))
    assert (s.PARTIAL_EVERY, s.PARTIAL_WINDOW) == (1.2, 10.0)
    db.kv_set("dictation_reactivity", "9")  # valeur absurde : réglage par défaut
    assert parakeet_engine.reactivity() == 3


def test_threads_follow_the_reactivity_but_always_leave_a_core_free(monkeypatch):
    monkeypatch.setattr(parakeet_engine.os, "cpu_count", lambda: 16)
    db.kv_set("dictation_reactivity", "1")
    assert parakeet_engine.threads() == 2
    db.kv_set("dictation_reactivity", "4")
    assert parakeet_engine.threads() == 4
    monkeypatch.setattr(parakeet_engine.os, "cpu_count", lambda: 2)
    assert parakeet_engine.threads() == 1


def test_the_live_text_only_reads_the_last_seconds_of_the_window(monkeypatch):
    monkeypatch.setattr(parakeet_engine, "keep_runner", lambda *a, **k: None)
    lengths = []

    def factory(path):
        def run(audio, fast=False):
            lengths.append(len(audio) / 16000)
            return "mot"
        run.needs_warmup = True
        return run
    s = parakeet_engine.ParakeetSession(mic=FakeMic([(0.3, "silence"), (12.0, "speech")]), runner_factory=factory)
    s.start()
    drain(s, {"ready"})
    for _ in range(80):
        threading.Event().wait(0.1)
        if len(lengths) >= 25:
            break
    s.stop()
    drain(s, {"done", "error"}, 30)
    assert max(lengths[2:-1]) <= 6.05  # fenêtre de la réactivité 3, hors préchauffage et passage définitif


def test_a_long_phrase_freezes_at_a_short_pause_but_a_short_one_waits(monkeypatch):
    monkeypatch.setattr(parakeet_engine, "keep_runner", lambda *a, **k: None)

    def run_script(script):
        n = {"i": 0}

        def factory(path):
            def run(audio, fast=False):
                n["i"] += 1
                return f"texte{n['i']}"
            run.needs_warmup = True
            return run
        s = parakeet_engine.ParakeetSession(mic=FakeMic(script), runner_factory=factory)
        s.start()
        evs = []
        drain(s, {"ready"})
        threading.Event().wait(3.0 + sum(d for d, _ in script) * 0.4)
        s.stop()
        evs = drain(s, {"done", "error"}, 30)
        return [e for e in evs if e["t"] == "final"]
    # 5 s de parole puis 0,4 s de silence puis 2 s de parole : la pause courte coupe, car la phrase dépasse 70 % de la fenêtre (4,2 s)
    long_phrase = run_script([(0.3, "silence"), (5.0, "speech"), (0.4, "silence"), (2.0, "speech"), (1.5, "silence")])
    short_phrase = run_script([(0.3, "silence"), (2.0, "speech"), (0.4, "silence"), (2.0, "speech"), (1.5, "silence")])
    assert len(long_phrase) >= 2 and len(short_phrase) == 1


async def test_settings_show_the_reactivity_slider_only_with_parakeet(user: User, monkeypatch) -> None:
    from mailia.ui_settings import open_settings
    monkeypatch.setattr(dictation, "supported", lambda: False)
    monkeypatch.setattr(whisper_engine, "libs_ok", lambda: True)
    monkeypatch.setattr(parakeet_engine, "libs_ok", lambda: True)
    monkeypatch.setattr(whisper_engine, "input_devices", lambda: [])
    monkeypatch.setattr(whisper_engine, "device_name", lambda device=None: "Micro")
    db.kv_set("dictation_engine", "parakeet")

    @ui.page("/settings")
    def page() -> None:
        open_settings(ui.element("div"), lambda acct: None)
    await user.open("/settings")
    user.find("Dictée vocale").click()
    await user.should_see("Réactivité : Réactif")
    (sl,) = user.find(marker="dictation-reactivity").elements
    with user.client:
        sl.set_value(4)
    await asyncio.sleep(0.3)
    assert db.kv_get("dictation_reactivity") == "4"


def test_two_simultaneous_loads_build_the_model_only_once(monkeypatch):
    built = []

    def slow_build(path):
        built.append(path)
        threading.Event().wait(0.3)
        def run(audio, fast=False):
            return ""
        return run
    monkeypatch.setattr(parakeet_engine, "_build_runner", slow_build)
    monkeypatch.setattr(parakeet_engine, "_runner_cache", {})
    got = []
    ts = [threading.Thread(target=lambda: got.append(parakeet_engine.load_runner("/x/model"))) for _ in range(3)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(built) == 1 and len({id(r) for r in got}) == 1  # préchargement et clic sur le micro ne chargent pas le modèle deux fois


def test_a_loaded_model_stays_in_memory_for_ten_minutes(monkeypatch):
    assert whisper_engine.KEEP_MINUTES == 10
    import inspect
    assert inspect.signature(parakeet_engine.keep_runner).parameters["minutes"].default == 10
    assert inspect.signature(whisper_engine.keep_runner).parameters["minutes"].default == 10


def test_preload_loads_and_warms_once_and_does_nothing_without_a_model(server, monkeypatch):
    monkeypatch.setattr(parakeet_engine, "libs_ok", lambda: True)
    assert parakeet_engine.preload() is False  # modèle pas installé : rien à charger
    parakeet_engine.download()
    loads, warms = [], []

    def fake_build(path):
        loads.append(path)
        def run(audio, fast=False):
            return ""
        run.needs_warmup = True
        return run
    monkeypatch.setattr(parakeet_engine, "_build_runner", fake_build)
    monkeypatch.setattr(parakeet_engine, "_runner_cache", {})
    monkeypatch.setattr(whisper_engine, "warm_up", lambda run: warms.append(1) or 0.0)
    assert parakeet_engine.preload() is True and parakeet_engine.preload() is True
    assert len(loads) == 1 and len(warms) == 1  # la deuxième fois, le modèle est déjà là
    run = parakeet_engine.load_runner(str(parakeet_engine.model_dir()))
    assert len(loads) == 1 and run  # et la dictée qui suit le réutilise


def test_the_parakeet_warm_up_is_not_digital_silence():
    import numpy as np
    src = open(parakeet_engine.__file__, encoding="utf-8").read()
    assert "warmup_audio" in src
    a = (np.random.default_rng(0).standard_normal(16000) * 0.01).astype("float32")
    assert float(np.abs(parakeet_engine.normalize(a)).max()) > 0.003 and float(np.abs(a).max()) >= 0.003  # il passe le filtre du silence et atteint le modèle


def test_dictation_preload_uses_the_chosen_engine_and_never_raises(monkeypatch):
    calls = []

    class Mod:
        @staticmethod
        def preload():
            calls.append(1)
            return True
    monkeypatch.setattr(dictation, "engine_module", lambda name=None: Mod)
    assert dictation.preload() is True and calls == [1]

    class Broken:
        @staticmethod
        def preload():
            raise RuntimeError("disque illisible")
    monkeypatch.setattr(dictation, "engine_module", lambda name=None: Broken)
    assert dictation.preload() is False
    monkeypatch.setattr(dictation, "engine_module", lambda name=None: None)  # Apple : rien à charger
    assert dictation.preload() is False


async def test_opening_the_composer_preloads_the_model_once(user: User, box, monkeypatch) -> None:
    from mailia import ui_dictation
    calls = []
    monkeypatch.setattr(dictation, "usable", lambda: True)
    monkeypatch.setattr(dictation, "preload", lambda: calls.append(1) or True)
    ui_dictation._last_preload[0] = 0.0
    await user.open("/")
    b = box[-1]
    b.new_message()
    await asyncio.sleep(0.6)
    b.new_message()
    await asyncio.sleep(0.6)
    assert len(calls) == 1  # pas à chaque réaffichage de la rédaction
