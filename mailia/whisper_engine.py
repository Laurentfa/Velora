"""Dictée avec Whisper (whisper.cpp) : pour Windows et pour les Mac qui n'ont pas la reconnaissance d'Apple (avant macOS 26).

Le modèle de langue n'est pas dans l'application : on le télécharge à la demande (un clic, avec accord), une seule fois, depuis le dépôt public de whisper.cpp
sur Hugging Face. Le fichier est vérifié (taille et empreinte SHA-256) avant d'être utilisé. Ensuite tout se passe sur l'ordinateur, sans connexion et sans l'IA.

Whisper traite des enregistrements, pas un flux : la dictée découpe la parole aux pauses, et refait la transcription du passage en cours toutes les
secondes pour afficher un texte provisoire. Le micro, le moteur et le téléchargement sont remplaçables (tests)."""
from __future__ import annotations

import hashlib
import importlib.util
import os
import queue
import threading
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from . import config, db

BASE_URL = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/"
SAMPLE_RATE = 16000


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


def download(key: str, progress=None, cancel: threading.Event | None = None) -> None:
    """Télécharge le modèle (reprend un téléchargement interrompu), le vérifie, puis le met en place. `progress(fait, total)` ; `cancel.set()` interrompt (le fichier partiel est gardé pour reprendre)."""
    m = MODELS[key]
    final = model_path(key)
    if is_installed(key):
        return
    if free_space() < m.size * 1.1:
        raise DownloadError(f"Il faut {human_mb(int(m.size * 1.1))} d'espace libre pour ce modèle.")
    part = final.with_name(m.file + ".part")
    have = part.stat().st_size if part.exists() else 0
    if have > m.size:
        part.unlink()
        have = 0
    req = urllib.request.Request(BASE_URL + m.file, headers={"User-Agent": "Velora", **({"Range": f"bytes={have}-"} if have else {})})
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
                    if progress:
                        progress(done, m.size)
    except DownloadError:
        raise
    except (OSError, ValueError) as e:
        raise DownloadError(f"Téléchargement impossible : {e}") from e
    if part.stat().st_size != m.size or _sha256(part) != m.sha256:
        part.unlink(missing_ok=True)
        raise DownloadError("Le fichier téléchargé est incomplet ou altéré : il a été supprimé. Réessayez.")
    os.replace(part, final)


# ---------------------------------------------------------------- transcription
_PHANTOMS = ("sous-titrage", "sous-titres réalisés", "amara.org", "merci d'avoir regardé", "merci d'avoir regardé cette vidéo", "thanks for watching",
             "abonnez-vous", "n'hésitez pas à vous abonner")
_runner_cache: dict[str, tuple] = {}
_cache_lock = threading.Lock()


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
    model = WModel(path, n_threads=min(4, os.cpu_count() or 2), print_progress=False, print_realtime=False)

    def run(audio) -> str:
        segs = model.transcribe(audio, language=language(), print_progress=False, **({"initial_prompt": prompt} if prompt else {}))
        return " ".join(s.text.strip() for s in segs).strip()
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


def clean_text(text: str) -> str:
    """Whisper invente parfois du texte sur du silence (« Sous-titrage… ») : on l'écarte."""
    t = (text or "").strip()
    low = t.lower()
    return "" if not t or any(p in low for p in _PHANTOMS) or set(t) <= set(".… ") else t


class MicStream:
    """Le micro, en blocs de 0,1 s (numpy float32 mono 16 kHz). Remplaçable par un faux dans les tests."""

    def __init__(self) -> None:
        self.q: queue.Queue = queue.Queue()
        self.stream = None

    def open(self) -> None:
        import sounddevice as sd
        self.stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32", blocksize=SAMPLE_RATE // 10,
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
    MAX_SEGMENT = 25.0   # une phrase très longue est coupée ici
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
            from . import dictation
            self.events.put({"t": "error", "code": "mic" if "PortAudio" in type(e).__name__ or "sounddevice" in repr(e) else "failed", "msg": str(e) or dictation.ERRORS["mic"]})
        finally:
            self.mic.close()
            self.finished = True

    def _run(self) -> None:
        import numpy as np
        self.events.put({"t": "loading"})
        path = str(model_path(self.model_key))
        from . import dictation_vocab
        self.prompt = dictation_vocab.whisper_prompt()  # vos noms propres : Whisper les reconnaît bien mieux avec cette amorce
        run = self.runner_factory(path)
        self.mic.open()
        self.events.put({"t": "ready"})
        block_s = 0.1
        buf: list = []
        pre: list = []  # quelques blocs de silence juste avant la parole : le début d'un mot est souvent plus faible que le seuil
        speech_s = silent_s = since_partial = 0.0
        floor = 0.01

        def text_of(chunks) -> str:
            return clean_text(run(np.concatenate(chunks).astype("float32")))

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
            if voiced and since_partial >= self.PARTIAL_EVERY and speech_s >= self.MIN_SPEECH:
                since_partial = 0.0
                txt = text_of(buf)
                if txt:
                    self.events.put({"t": "partial", "text": txt})
            if (speech_s and silent_s >= self.PAUSE) or len(buf) * block_s >= self.MAX_SEGMENT:
                finish_segment()
        # arrêt demandé : le dernier passage est transcrit puis tout est fini
        while True:  # blocs restants du micro
            b = self.mic.read(0.05)
            if b is None:
                break
            buf.append(b)
            speech_s += block_s if float((b.astype("float64") ** 2).mean() ** 0.5) > max(0.005, floor * 3) else 0
        finish_segment()
        keep_runner((path, self.prompt), run)
        self.events.put({"t": "done"})
