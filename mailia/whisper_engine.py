"""Dictée avec Whisper (whisper.cpp) : pour Windows et pour les Mac qui n'ont pas la reconnaissance d'Apple (avant macOS 26).

Le modèle de langue n'est pas dans l'application : on le télécharge à la demande (un clic, avec accord), une seule fois, depuis le dépôt public de whisper.cpp
sur Hugging Face. Le fichier est vérifié (taille et empreinte SHA-256) avant d'être utilisé. Ensuite tout se passe sur l'ordinateur, sans connexion et sans l'IA.

Whisper traite des enregistrements, pas un flux : la dictée découpe la parole aux pauses, et refait la transcription du passage en cours toutes les
secondes pour afficher un texte provisoire. Le micro, le moteur et le téléchargement sont remplaçables (tests)."""
from __future__ import annotations

import collections
import hashlib
import importlib.util
import inspect
import os
import queue
import re
import logging
import threading
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from . import config, db

log = logging.getLogger("dictation")

BASE_URL = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/"
SAMPLE_RATE = 16000
FAST_PARTIALS = False  # audio_ctx raccourcit le calcul mais favorise les répétitions de Whisper : le préchauffage suffit à rendre le texte provisoire assez rapide
TRACE: "collections.deque[dict]" = collections.deque(maxlen=40)  # dernières transcriptions (brut / gardé) : montrées dans les Paramètres pour comprendre un défaut


@dataclass(frozen=True)
class Model:
    key: str
    file: str
    size: int
    sha256: str
    title: str
    hint: str


MODELS = {m.key: m for m in (
    Model("rapide", "ggml-base-q5_1.bin", 59_707_625, "422f1ae452ade6f30a004d7e5c6a43195e4433bc370bf23fac9cc591f01a8898", "Rapide", "léger, moins précis"),
    Model("equilibre", "ggml-small-q5_1.bin", 190_085_487, "ae85e4a935d7a567bd102fe55afc16bb595bdb618e11b2fc7591bc08120411bb", "Équilibré", "recommandé"),
    Model("precis", "ggml-medium-q5_0.bin", 539_212_467, "19fea4b380c3a618ec4723c3eef2eb785ffba0d0538cf43f8f235e7b3b34220f", "Précis", "plus lent, meilleur sur les mots techniques"),
)}
DEFAULT_MODEL = "equilibre"


def human_mb(n: int) -> str:
    return f"{round(n / 1_000_000)} Mo"


def models_dir() -> Path:
    d = config.DATA_DIR / "models"
    d.mkdir(parents=True, exist_ok=True)
    return d


def chosen() -> str:
    key = db.kv_get("dictation_model") or DEFAULT_MODEL
    return key if key in MODELS else DEFAULT_MODEL


def set_chosen(key: str) -> None:
    if key in MODELS:
        db.kv_set("dictation_model", key)


def model_path(key: str) -> Path:
    return models_dir() / MODELS[key].file


def is_installed(key: str | None = None) -> bool:
    key = key or chosen()
    p = model_path(key)
    return p.is_file() and p.stat().st_size == MODELS[key].size


def libs_ok() -> bool:
    """Le moteur (pywhispercpp) et la capture du micro (sounddevice) sont présents dans cette installation."""
    return all(importlib.util.find_spec(m) is not None for m in ("pywhispercpp", "sounddevice", "numpy"))


class DownloadError(Exception):
    pass


def free_space() -> int:
    import shutil
    return shutil.disk_usage(models_dir()).free


def remove(key: str) -> None:
    for p in (model_path(key), model_path(key).with_name(MODELS[key].file + ".part")):
        try:
            p.unlink()
        except OSError:
            pass


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fetch_file(url: str, final: Path, size: int, sha256: str, on_bytes=None, cancel: threading.Event | None = None) -> None:
    """Télécharge un fichier (reprend un téléchargement interrompu), vérifie sa taille et son empreinte, puis le met en place. `on_bytes(octets_reçus)` ; `cancel.set()`
    interrompt (le fichier partiel est gardé pour reprendre). Sert aux modèles de Whisper et de Parakeet."""
    if final.is_file() and final.stat().st_size == size:
        if on_bytes:
            on_bytes(size)
        return
    part = final.with_name(final.name + ".part")
    have = part.stat().st_size if part.exists() else 0
    if have > size:
        part.unlink()
        have = 0
    req = urllib.request.Request(url, headers={"User-Agent": "Velora", **({"Range": f"bytes={have}-"} if have else {})})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            if have and resp.status != 206:  # le serveur ignore la reprise : on repart de zéro
                have = 0
            with open(part, "ab" if have else "wb") as f:
                done = have
                while True:
                    if cancel is not None and cancel.is_set():
                        raise DownloadError("Téléchargement interrompu.")
                    chunk = resp.read(1 << 20)
                    if not chunk:
                        break
                    f.write(chunk)
                    done += len(chunk)
                    if on_bytes:
                        on_bytes(done)
                f.flush()
                os.fsync(f.fileno())  # sur le disque pour de bon : après une coupure de courant, Windows peut sinon laisser un fichier de la bonne taille mais rempli de zéros
    except DownloadError:
        raise
    except (OSError, ValueError) as e:
        raise DownloadError(f"Téléchargement impossible : {e}") from e
    if part.stat().st_size != size or _sha256(part) != sha256:
        part.unlink(missing_ok=True)
        raise DownloadError("Le fichier téléchargé est incomplet ou altéré : il a été supprimé. Réessayez.")
    os.replace(part, final)


def download(key: str, progress=None, cancel: threading.Event | None = None) -> None:
    """Télécharge le modèle Whisper `key`. `progress(fait, total)`."""
    m = MODELS[key]
    if is_installed(key):
        return
    if free_space() < m.size * 1.1:
        raise DownloadError(f"Il faut {human_mb(int(m.size * 1.1))} d'espace libre pour ce modèle.")
    fetch_file(BASE_URL + m.file, model_path(key), m.size, m.sha256, (lambda d: progress(d, m.size)) if progress else None, cancel)


# ---------------------------------------------------------------- transcription
_PHANTOMS = ("sous-titrage", "sous-titres réalisés", "amara.org", "merci d'avoir regardé", "merci d'avoir regardé cette vidéo", "thanks for watching",
             "abonnez-vous", "n'hésitez pas à vous abonner")
_runner_cache: dict[str, tuple] = {}
_cache_lock = threading.Lock()


def default_threads() -> int:
    return max(2, min(8, (os.cpu_count() or 4) - 1))


def chosen_threads() -> int | None:
    v = db.kv_get("dictation_threads")
    return int(v) if v and v.isdigit() and int(v) > 0 else None


def warm_up(run) -> float:
    """Le premier calcul de Whisper est bien plus lent que les suivants (le modèle se charge réellement en mémoire) : on le fait avant la dictée, sur 1 s de silence.
    Les runners factices des tests n'ont pas d'« état » et sont laissés tels quels."""
    if not hasattr(run, "state") and not getattr(run, "needs_warmup", False):
        return 0.0
    import numpy as np
    t0 = time.monotonic()
    quiet = np.zeros(SAMPLE_RATE, dtype="float32")
    run(quiet)
    run(quiet, fast=True)
    took = time.monotonic() - t0
    log.info("dictée Whisper : préchauffage en %.1f s", took)
    return took


def autotune(run, audio, timer=time.monotonic) -> tuple[int, dict[int, float]]:
    """Essaie plusieurs nombres de fils de calcul sur le même enregistrement et garde le plus rapide (sur les processeurs à coeurs mixtes, utiliser tous les
    coeurs ralentit souvent). Retourne (le meilleur, {fils: secondes}) et le mémorise."""
    cores = os.cpu_count() or 4
    cands = sorted({n for n in (2, 4, 6, 8, cores // 2, cores - 1) if 2 <= n <= max(2, cores)})
    times: dict[int, float] = {}
    for n in cands:
        run.state["n"] = n
        t0 = timer()
        run(audio)
        times[n] = timer() - t0
    best = min(times, key=times.get)
    run.state["n"] = best
    db.kv_set("dictation_threads", str(best))
    return best, times


def language() -> str:
    return "en" if db.kv_get("ui_lang") == "en" else "fr"


def load_runner(path: str, prompt: str | None = None):
    """Retourne une fonction « audio (numpy float32, 16 kHz) -> texte ». `prompt` : amorce de texte (noms propres) qui oriente la reconnaissance.
    Le modèle chargé est gardé en mémoire quelques minutes après la dictée."""
    with _cache_lock:
        hit = _runner_cache.get((path, prompt))
        if hit:
            hit[1].cancel()
    if hit:
        return hit[0]
    from pywhispercpp.model import Model as WModel
    model = WModel(path, n_threads=default_threads(), print_progress=False, print_realtime=False)
    state = {"n": chosen_threads() or default_threads()}

    def run(audio, fast: bool = False) -> str:
        # fast (texte provisoire) : Whisper traite toujours 30 s d'audio même pour une phrase de 3 s ; audio_ctx limite le calcul à la durée réellement parlée
        extra = {"audio_ctx": min(1500, int(len(audio) / SAMPLE_RATE * 50) + 100), "single_segment": True, "no_timestamps": True} if FAST_PARTIALS and fast and len(audio) < SAMPLE_RATE * 25 else {}
        # no_context : ne pas se laisser influencer par le passage précédent ; suppress_nst : pas de « [musique] », « (rires) ». Le repli de Whisper (nouvel essai à température plus haute quand il se répète) reste actif : sans lui, il peut boucler
        segs = model.transcribe(audio, language=language(), print_progress=False, no_context=True, suppress_nst=True,
                                n_threads=state["n"], **extra, **({"initial_prompt": prompt} if prompt else {}))
        return " ".join(s.text.strip() for s in segs).strip()
    run.state = state
    run.system_info = lambda: str(model.system_info())
    return run


def keep_runner(key, run, minutes: float = 3) -> None:
    def drop():
        with _cache_lock:
            _runner_cache.pop(key, None)
    with _cache_lock:
        old = _runner_cache.get(key)
        if old:
            old[1].cancel()
        t = threading.Timer(minutes * 60, drop)
        t.daemon = True
        t.start()
        _runner_cache[key] = (run, t)


def _norm(tok: str) -> str:
    return tok.strip(".,;:!?…«»\"'()").lower()


def drop_loops(text: str) -> str:
    """Whisper boucle parfois (« L'Ophe, L'Ophe, L'Ophe… », « je suis un amour, et je suis un amour… »). Une suite de 1 à 8 mots répétée au moins 6 fois de suite est
    supprimée en entier (c'est du bruit, pas de la parole) ; répétée 4 ou 5 fois, elle est réduite à un exemplaire ; jusqu'à 3 fois (« non non non »), elle est gardée."""
    toks = text.split()
    changed = True
    while changed:
        changed = False
        for n in range(1, 9):
            i = 0
            while i + 2 * n <= len(toks):
                unit = [_norm(t) for t in toks[i:i + n]]
                if not any(unit):
                    i += 1
                    continue
                reps = 1
                while i + (reps + 1) * n <= len(toks) and [_norm(t) for t in toks[i + reps * n:i + (reps + 1) * n]] == unit:
                    reps += 1
                if reps >= 4:
                    keep = [] if reps >= 6 else toks[i:i + n]
                    if keep:  # la ponctuation de la dernière répétition (« allez allez allez allez, on y va ») est conservée
                        tail = re.search(r"[.,;:!?…]+$", toks[i + reps * n - 1])
                        if tail and not re.search(r"[.,;:!?…]$", keep[-1]):
                            keep[-1] += tail.group(0)
                    toks[i:i + reps * n] = keep
                    changed = True
                else:
                    i += 1
    return " ".join(toks)


def clean_text(text: str) -> str:
    """Whisper invente parfois du texte sur du silence (« Sous-titrage… ») ou boucle : on écarte l'un et l'autre."""
    t = drop_loops((text or "").strip())
    low = t.lower()
    return "" if not t or any(p in low for p in _PHANTOMS) or set(t) <= set(".… ") else t


def input_devices() -> list[tuple[int, str]]:
    """Les micros de l'ordinateur : (numéro, nom)."""
    import sounddevice as sd
    out = []
    for i, d in enumerate(sd.query_devices()):
        if d.get("max_input_channels", 0) > 0:
            out.append((i, d["name"]))
    return out


def chosen_device() -> int | None:
    v = db.kv_get("dictation_device")
    return int(v) if v and v.lstrip("-").isdigit() else None


def device_name(device: int | None = None) -> str:
    import sounddevice as sd
    try:
        return sd.query_devices(device if device is not None else sd.default.device[0])["name"]
    except Exception:  # noqa: BLE001
        return "?"


class MicStream:
    """Le micro, en blocs de 0,1 s (numpy float32 mono 16 kHz). Remplaçable par un faux dans les tests."""

    def __init__(self, device: int | None = None) -> None:
        self.q: queue.Queue = queue.Queue()
        self.stream = None
        self.device = chosen_device() if device is None else device

    def open(self) -> None:
        import sounddevice as sd
        self.stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32", blocksize=SAMPLE_RATE // 10, device=self.device,
                                     callback=lambda data, frames, t, status: self.q.put(data[:, 0].copy()))
        self.stream.start()

    def read(self, timeout: float = 0.3):
        try:
            return self.q.get(timeout=timeout)
        except queue.Empty:
            return None

    def close(self) -> None:
        if self.stream is not None:
            try:
                self.stream.stop()
                self.stream.close()
            except Exception:
                pass


class WhisperSession:
    """Même interface que dictation.Session : `events` reçoit ready / partial / final / done / error, avec `loading` en plus pendant le chargement du modèle."""
    PAUSE = 0.8          # secondes de silence qui terminent une phrase
    PARTIAL_EVERY = 1.2  # secondes de parole nouvelle entre deux textes provisoires
    PARTIAL_WINDOW = 10.0  # le texte provisoire ne relit que les dernières secondes : le coût ne grandit pas avec la phrase
    SOFT_PAUSE = 0.3     # une phrase déjà longue (70 % de la fenêtre) se fige à cette courte pause, pour que le texte provisoire ne perde pas son début
    MAX_SEGMENT = 15.0   # une phrase très longue est coupée ici (les longs passages sans pause sont ceux où Whisper boucle le plus)
    MIN_SPEECH = 0.35    # moins que ça : du bruit, pas de la parole

    def __init__(self, model_key: str | None = None, mic=None, runner_factory=None) -> None:
        self.model_key = model_key or chosen()
        self.mic = mic or MicStream()
        self.prompt = None
        self.runner_factory = runner_factory or (lambda path: load_runner(path, self.prompt))
        self.events: queue.Queue[dict] = queue.Queue()
        self.finished = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # points d'extension : la dictée avec Parakeet réutilise tout le reste (micro, découpage aux pauses, texte provisoire)
    def _model_path(self) -> str:
        return str(model_path(self.model_key))

    def _make_prompt(self) -> str | None:
        from . import dictation_vocab
        return dictation_vocab.whisper_prompt()  # vos noms propres : Whisper les reconnaît bien mieux avec cette amorce

    def _keep(self, path: str, run) -> None:
        keep_runner((path, self.prompt), run)

    def start(self) -> None:
        self._thread = threading.Thread(target=self._guard, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def kill(self) -> None:
        self._stop.set()
        self.finished = True

    def _guard(self) -> None:
        try:
            self._run()
        except Exception as e:  # noqa: BLE001 — on ne laisse jamais la dictée se figer : l'erreur est montrée
            self.events.put({"t": "error", "code": "mic" if "PortAudio" in type(e).__name__ or "sounddevice" in repr(e) else "failed", "msg": describe_error(e)})
        finally:
            self.mic.close()
            self.finished = True

    def _run(self) -> None:
        import numpy as np
        self.events.put({"t": "loading"})
        path = self._model_path()
        self.prompt = self._make_prompt()
        run = self.runner_factory(path)
        warm_up(run)
        self.mic.open()
        self.events.put({"t": "ready"})
        block_s = 0.1
        buf: list = []
        pre: list = []  # quelques blocs de silence juste avant la parole : le début d'un mot est souvent plus faible que le seuil
        speech_s = silent_s = since_partial = 0.0
        floor = 0.01

        slow = {"n": 0, "off": False}

        fast_ok = "fast" in inspect.signature(run).parameters

        def text_of(chunks, partial: bool = False) -> str:
            audio = np.concatenate(chunks).astype("float32")
            if partial and len(audio) > SAMPLE_RATE * self.PARTIAL_WINDOW:
                audio = audio[-int(SAMPLE_RATE * self.PARTIAL_WINDOW):]
            t0 = time.monotonic()
            raw = run(audio, fast=True) if partial and fast_ok else run(audio)
            txt = clean_text(raw)
            took, length = time.monotonic() - t0, len(audio) / SAMPLE_RATE
            TRACE.append({"kind": "provisoire" if partial else "définitif", "audio_s": round(length, 1), "took": round(took, 1), "raw": raw, "kept": txt,
                          "rms": round(float((audio.astype("float64") ** 2).mean() ** 0.5), 4)})
            log.info("dictée Whisper : %.1f s d'audio transcrites en %.1f s (%s)", length, took, "provisoire" if partial else "définitif")
            if partial and not slow["off"]:
                slow["n"] = slow["n"] + 1 if took > length * 0.8 else 0
                if slow["n"] >= 2:  # l'ordinateur ne suit pas : plus de texte provisoire, seulement aux pauses
                    slow["off"] = True
                    self.events.put({"t": "slow"})
            return txt

        def finish_segment() -> None:
            nonlocal buf, speech_s, silent_s, since_partial
            if speech_s >= self.MIN_SPEECH:
                txt = text_of(buf)
                if txt:
                    self.events.put({"t": "final", "text": txt})
            buf, speech_s, silent_s, since_partial = [], 0.0, 0.0, 0.0

        while not self._stop.is_set():
            block = self.mic.read()
            if block is None:
                continue
            rms = float((block.astype("float64") ** 2).mean() ** 0.5)
            floor = rms if rms < floor else min(0.02, floor + (rms - floor) * 0.002)
            voiced = rms > max(0.005, floor * 3)
            if voiced:
                speech_s += block_s
                silent_s = 0.0
                since_partial += block_s
            else:
                silent_s += block_s
            if not buf and not voiced:
                pre = (pre + [block])[-3:]
                continue  # silence avant la parole : rien à garder
            if not buf:
                buf, pre = pre, []
            buf.append(block)
            if voiced and not slow["off"] and since_partial >= self.PARTIAL_EVERY and speech_s >= self.MIN_SPEECH:
                since_partial = 0.0
                txt = text_of(buf, partial=True)
                if txt:
                    self.events.put({"t": "partial", "text": txt})
            long_phrase = len(buf) * block_s >= self.PARTIAL_WINDOW * 0.7
            if (speech_s and silent_s >= (self.SOFT_PAUSE if long_phrase and self.PARTIAL_WINDOW < self.MAX_SEGMENT else self.PAUSE)) or len(buf) * block_s >= self.MAX_SEGMENT:
                finish_segment()
        # arrêt demandé : le dernier passage est transcrit puis tout est fini
        while True:  # blocs restants du micro
            b = self.mic.read(0.05)
            if b is None:
                break
            buf.append(b)
            speech_s += block_s if float((b.astype("float64") ** 2).mean() ** 0.5) > max(0.005, floor * 3) else 0
        finish_segment()
        self._keep(path, run)
        self.events.put({"t": "done"})


def describe_error(e: BaseException) -> str:
    """Message d'erreur utile pour un diagnostic à distance : le texte, le type de l'erreur et les deux derniers endroits du code traversés."""
    import traceback
    frames = traceback.extract_tb(e.__traceback__)[-2:]
    where = " › ".join(f"{os.path.basename(f.filename)}:{f.lineno} ({f.name})" for f in frames)
    log.warning("dictée : erreur", exc_info=e)
    return f"{str(e) or 'Aucun micro utilisable.'} [{type(e).__name__}{' · ' + where if where else ''}]"


def record_audio(seconds: float, mic=None, on_level=None):
    """Enregistre `seconds` secondes au micro : (audio numpy float32 16 kHz, micro utilisé). Lève une exception si le micro est inutilisable."""
    import numpy as np
    mic = mic or MicStream()
    blocks = []
    try:
        mic.open()
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            b = mic.read()
            if b is None:
                continue
            blocks.append(b)
            if on_level:
                on_level(float(np.abs(b).max()), (time.monotonic() - (end - seconds)) / seconds)
    finally:
        mic.close()
    if not blocks:
        raise OSError("Aucun son reçu du micro.")
    return np.concatenate(blocks).astype("float32"), mic


def _engine_factory(name: str | None = None):
    """(moteur, fonction qui crée le « runner » à partir du dossier ou fichier du modèle) pour le moteur choisi dans les Paramètres."""
    from . import dictation
    name = name or dictation.engine()
    if name == "parakeet":
        from . import parakeet_engine as pe
        return "parakeet", (lambda path: pe.load_runner(str(pe.model_dir())))
    return "whisper", (lambda path: load_runner(str(model_path(chosen())), None))


def _measure(run, audio, tune: bool) -> dict:
    import numpy as np
    t0 = time.monotonic()
    text = clean_text(run(audio))
    took = time.monotonic() - t0
    audio_s = len(audio) / SAMPLE_RATE
    fast_took = None
    if FAST_PARTIALS and "fast" in inspect.signature(run).parameters:  # vitesse du texte provisoire (ce qui compte pour la dictée en direct)
        t1 = time.monotonic()
        run(audio, fast=True)
        fast_took = time.monotonic() - t1
    out = {"fast_took": fast_took, "peak": float(np.abs(audio).max()), "rms": float((audio.astype("float64") ** 2).mean() ** 0.5), "text": text, "audio_s": audio_s,
           "took": took, "threads": getattr(run, "state", {}).get("n"), "engine": run.system_info() if hasattr(run, "system_info") else ""}
    if tune and hasattr(run, "state") and took > audio_s * 0.35:  # lent : on cherche le meilleur réglage du processeur (Whisper)
        before = took
        best, times = autotune(run, audio)
        out.update(tuned=True, best_threads=best, times=times, took_before=before, took=times[best])
        if fast_took is not None:
            t2 = time.monotonic()
            run(audio, fast=True)
            out["fast_took"] = time.monotonic() - t2
    return out


def test_microphone(seconds: float = 5.0, mic=None, runner_factory=None, on_level=None, tune: bool = True) -> dict:
    """Enregistre quelques secondes et transcrit avec le moteur choisi : donne de quoi comprendre un problème (mauvais micro, niveau trop faible, ordinateur lent).
    Retourne {"device", "peak", "rms", "text", "audio_s", "took", …} ou {"error": …}."""
    try:
        factory = runner_factory or _engine_factory()[1]
        run = factory(str(model_path(chosen())))
        warm_up(run)
        audio, mic = record_audio(seconds, mic, on_level)
    except Exception as e:  # noqa: BLE001
        return {"error": describe_error(e)}
    try:
        out = _measure(run, audio, tune)
    except Exception as e:  # noqa: BLE001
        return {"error": describe_error(e)}
    out["device"] = device_name(getattr(mic, "device", None)) if isinstance(mic, MicStream) else ""
    return out


def compare_microphone(seconds: float = 5.0, mic=None, on_level=None, factories: dict | None = None) -> dict:
    """Un seul enregistrement, transcrit par chaque moteur installé (Whisper, Parakeet) : « {"device", "audio_s", "results": [{"engine", "text", "took"}]} » ou {"error": …}."""
    from . import parakeet_engine as pe
    factories = factories or {}
    if "whisper" not in factories and is_installed():
        factories["whisper"] = lambda: load_runner(str(model_path(chosen())), None)
    if "parakeet" not in factories and pe.is_installed():
        factories["parakeet"] = lambda: pe.load_runner(str(pe.model_dir()))
    if len(factories) < 2:
        return {"error": "Installez les deux moteurs (Whisper et Parakeet) pour les comparer."}
    try:
        runs = {}
        for name, make in factories.items():
            run = make()
            warm_up(run)
            runs[name] = run
        audio, mic = record_audio(seconds, mic, on_level)
    except Exception as e:  # noqa: BLE001
        return {"error": describe_error(e)}
    results = []
    for name, run in runs.items():
        try:
            m = _measure(run, audio, tune=False)
            results.append({"engine": name, "text": m["text"], "took": m["took"]})
        except Exception as e:  # noqa: BLE001 — un moteur qui échoue n'empêche pas de voir l'autre
            results.append({"engine": name, "text": "(échec : " + describe_error(e) + ")", "took": 0.0})
    return {"device": device_name(getattr(mic, "device", None)) if isinstance(mic, MicStream) else "", "audio_s": len(audio) / SAMPLE_RATE, "peak": float(abs(audio).max()), "results": results}
