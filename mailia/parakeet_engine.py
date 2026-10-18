"""Dictée avec Parakeet TDT 0.6B v3 (NVIDIA), sur l'ordinateur : une alternative à Whisper pour Windows et les Mac sans la reconnaissance d'Apple.

Mesuré sur 150 extraits de parole française (corpus FLEURS) : environ 8 % de mots faux contre 15 % pour le Whisper « équilibré », et plus de 50 fois plus
rapide que la parole sur un processeur de Mac. Plus sensible au bruit de fond que la reconnaissance d'Apple. Licence du modèle : CC-BY 4.0 (NVIDIA).

Le modèle n'est pas dans l'application : téléchargement à la demande (comme Whisper), depuis une conversion au format ONNX publiée sur Hugging Face, fixée à une
version précise et vérifiée (taille et empreinte SHA-256). Il reconnaît des passages de 20 secondes au plus (au-delà il perd des mots) : la dictée découpe aux pauses
(15 s au plus). Le volume est normalisé avant la reconnaissance (gain mesuré : 17 % -> 6 % d'erreurs sur une voix faible)."""
from __future__ import annotations

import importlib.util
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from . import whisper_engine
from .whisper_engine import DownloadError, WhisperSession, _sha256, fetch_file, human_mb, models_dir

REVISION = "8f23f0c03c8761650bdb5b40aaf3e40d2c15f1ce"
BASE_URL = f"https://huggingface.co/istupakov/parakeet-tdt-0.6b-v3-onnx/resolve/{REVISION}/"
FILES = (  # (nom, taille, SHA-256) : la version « int8 » (compressée)
    ("config.json", 97, "666903c76b9798caf2c210afd4f6cd60b08a8dbf9800ec8d7a3bc0d2148ac466"),
    ("decoder_joint-model.int8.onnx", 18202004, "eea7483ee3d1a30375daedc8ed83e3960c91b098812127a0d99d1c8977667a70"),
    ("encoder-model.int8.onnx", 652183999, "6139d2fa7e1b086097b277c7149725edbab89cc7c7ae64b23c741be4055aff09"),
    ("nemo128.onnx", 139764, "a9fde1486ebfcc08f328d75ad4610c67835fea58c73ba57e3209a6f6cf019e9f"),
    ("vocab.txt", 93939, "d58544679ea4bc6ac563d1f545eb7d474bd6cfa467f0a6e2c1dc1c7d37e3c35d"),
)
TOTAL = sum(f[1] for f in FILES)
MAX_CHUNK = 20  # secondes : au-delà, Parakeet perd des mots


@dataclass(frozen=True)
class Model:
    key: str
    title: str
    size: int
    hint: str


MODELS = {"parakeet": Model("parakeet", "Parakeet (NVIDIA)", TOTAL, "très précis en français, rapide ; plus sensible au bruit de fond")}
DEFAULT_MODEL = "parakeet"
human_mb = human_mb  # noqa: PLW0127 — la fenêtre de téléchargement l'attend sur le module du moteur


def chosen() -> str:
    return "parakeet"


def model_dir() -> Path:
    return models_dir() / "parakeet-tdt-0.6b-v3-int8"


def is_installed(key: str | None = None) -> bool:
    d = model_dir()
    return all((d / n).is_file() and (d / n).stat().st_size == size for n, size, _ in FILES)


def libs_ok() -> bool:
    """Le moteur (onnx-asr et ONNX Runtime) et la capture du micro sont présents dans cette installation."""
    return all(importlib.util.find_spec(m) is not None for m in ("onnx_asr", "onnxruntime", "sounddevice", "numpy"))


def verify_files() -> list[str]:
    """Noms des fichiers du modèle absents, de mauvaise taille ou dont l'empreinte SHA-256 ne correspond pas (quelques secondes pour le gros fichier)."""
    d = model_dir()
    return [n for n, size, sha in FILES if not (d / n).is_file() or (d / n).stat().st_size != size or _sha256(d / n) != sha]


def remove(key: str | None = None) -> None:
    d = model_dir()
    for n, _, _ in FILES:
        for p in (d / n, d / (n + ".part")):
            try:
                p.unlink()
            except OSError:
                pass


def download(key: str | None = None, progress=None, cancel: threading.Event | None = None) -> None:
    """Télécharge les fichiers du modèle. `progress(fait, total)` sur l'ensemble."""
    if is_installed():
        return
    if whisper_engine.free_space() < TOTAL * 1.1:
        raise DownloadError(f"Il faut {human_mb(int(TOTAL * 1.1))} d'espace libre pour ce modèle.")
    d = model_dir()
    d.mkdir(parents=True, exist_ok=True)
    before = 0
    for name, size, sha in FILES:
        fetch_file(BASE_URL + name, d / name, size, sha, (lambda got, b=before: progress(b + got, TOTAL)) if progress else None, cancel)
        before += size


# ---------------------------------------------------------------- reconnaissance
_runner_cache: dict[str, tuple] = {}
_cache_lock = threading.Lock()


# Réactivité de la dictée (curseur dans les Paramètres) : (titre, secondes de parole entre deux textes provisoires, secondes relues, fils de calcul au plus)
PRESETS = {1: ("Économe", 1.2, 10.0, 2), 2: ("Équilibré", 0.6, 8.0, 3), 3: ("Réactif", 0.3, 6.0, 4), 4: ("Très réactif", 0.15, 5.0, 4)}
DEFAULT_REACTIVITY = 3


def reactivity() -> int:
    from . import db
    v = db.kv_get("dictation_reactivity")
    return int(v) if v and v.isdigit() and int(v) in PRESETS else DEFAULT_REACTIVITY


def threads() -> int:
    """Fils de calcul : au plus ceux du réglage de réactivité, et toujours un coeur de libre (mesuré : ×21 à 1 fil, ×55 à 4, ×65 à 8)."""
    return max(1, min(PRESETS[reactivity()][3], (os.cpu_count() or 4) - 1))


def normalize(audio, target: float = 0.06):
    """Ramène la voix à un niveau moyen (un micro trop faible fait chuter la précision) ; un silence numérique reste un silence."""
    import numpy as np
    audio = np.asarray(audio, dtype="float32")
    if len(audio) == 0 or float(np.abs(audio).max()) < 0.003:
        return audio
    rms = float(np.sqrt((audio.astype("float64") ** 2).mean())) + 1e-9
    return np.clip(audio * min(target / rms, 60.0), -1.0, 1.0).astype("float32")


CONFIG_JSON = '{\n    "model_type": "nemo-conformer-tdt",\n    "features_size": 128,\n    "subsampling_factor": 8\n}'


def ensure_config(path: str) -> None:
    """Le moteur lit config.json au chargement : s'il est vide ou illisible (téléchargement interrompu, antivirus…), on le remet à neuf (son contenu est connu)."""
    import json
    cfg = Path(path) / "config.json"
    try:
        json.loads(cfg.read_text(encoding="utf-8"))
        return
    except (OSError, ValueError):
        pass
    cfg.write_text(CONFIG_JSON, encoding="utf-8")


def load_runner(path: str):
    """Retourne « audio (numpy float32, 16 kHz) -> texte ». Le modèle chargé est gardé en mémoire quelques minutes après la dictée."""
    key = f"{path}|{threads()}"  # un autre réglage de réactivité = un autre nombre de fils = un autre chargement
    with whisper_engine._load_lock:  # un seul chargement de modèle à la fois (préchargement et clic sur le micro)
        with _cache_lock:
            hit = _runner_cache.get(key)
            if hit:
                hit[1].cancel()
        if hit:
            return hit[0]
        run = _build_runner(path)
        keep_runner(path, run)
        return run


def _build_runner(path: str):
    import numpy as np
    import onnx_asr
    import onnxruntime as ort
    ensure_config(path)
    so = ort.SessionOptions()
    so.intra_op_num_threads = threads()
    try:
        model = onnx_asr.load_model("nemo-conformer-tdt", path=path, quantization="int8", sess_options=so, providers=["CPUExecutionProvider"])
    except Exception as e:  # noqa: BLE001 — avant de blâmer le moteur, on vérifie que les fichiers sont intacts
        bad = verify_files()
        for n in bad:  # un fichier abîmé est supprimé : le modèle apparaît « pas encore téléchargé » et se retélécharge d'un clic
            try:
                (model_dir() / n).unlink()
            except OSError:
                pass
        if bad:
            raise RuntimeError(f"Fichiers du modèle abîmés ({', '.join(bad)}) : supprimés. Retéléchargez le modèle dans Paramètres › Dictée vocale. Détail : {e}") from e
        raise RuntimeError(f"Le modèle ne se charge pas, alors que ses fichiers sont intacts (empreintes vérifiées). Détail : {e}") from e

    def run(audio, fast: bool = False) -> str:
        a = normalize(audio)
        if float(np.abs(a).max()) < 0.003:
            return ""
        step = MAX_CHUNK * whisper_engine.SAMPLE_RATE
        parts = [str(model.recognize(a[i:i + step])).strip() for i in range(0, len(a), step)]
        return " ".join(p for p in parts if p)
    run.needs_warmup = True
    # le préchauffage utilise un très léger bruit : un silence numérique est écarté avant d'atteindre le modèle, qui ne serait donc pas préchauffé
    run.warmup_audio = (np.random.default_rng(0).standard_normal(whisper_engine.SAMPLE_RATE) * 0.01).astype("float32")
    run.system_info = lambda: f"Parakeet TDT 0.6B v3 (int8) · ONNX Runtime {ort.__version__} · {threads()} fils de calcul"
    return run


def keep_runner(path: str, run, minutes: float = whisper_engine.KEEP_MINUTES) -> None:
    key = f"{path}|{threads()}"

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


def preload() -> bool:
    """Charge et préchauffe Parakeet en tâche de fond (à l'ouverture de la rédaction). Sans effet s'il est déjà chargé."""
    if not (libs_ok() and is_installed()):
        return False
    path = str(model_dir())
    with _cache_lock:
        if f"{path}|{threads()}" in _runner_cache:
            return True
    t0 = time.monotonic()
    ensure_config(path)
    whisper_engine.warm_up(load_runner(path))
    whisper_engine.log.info("dictée Parakeet : modèle préchargé en %.1f s", time.monotonic() - t0)
    return True


class ParakeetSession(WhisperSession):
    """Même dictée que Whisper (micro, pauses, texte provisoire), avec Parakeet comme moteur."""

    def __init__(self, mic=None, runner_factory=None) -> None:
        super().__init__("parakeet", mic=mic, runner_factory=runner_factory or load_runner)
        _, self.PARTIAL_EVERY, self.PARTIAL_WINDOW, _ = PRESETS[reactivity()]  # le texte provisoire suit le curseur de réactivité

    def _model_path(self) -> str:
        return str(model_dir())

    def _make_prompt(self) -> str | None:
        return None  # Parakeet n'accepte pas de vocabulaire : les noms propres sont corrigés après coup (voir dictation_vocab.correct)

    def _keep(self, path: str, run) -> None:
        keep_runner(path, run)
