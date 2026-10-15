"""Client de l'API 1min.AI. Deux points d'entrée, choisis automatiquement selon le modèle :
 - compatible OpenAI (https://api.1min.ai/openai/v1) : texte seulement, ~59 modèles (GPT, Mistral, Qwen, DeepSeek, GLM…) ;
 - API native « Chat with AI » (https://api.1min.ai/api) : tous les modèles dont Claude (AWS Bedrock) et Gemini, et les images
   (envoyées d'abord à l'espace de stockage 1min.AI, puis référencées). Elle n'a pas de champ « consigne système » : la consigne
   est placée en tête du message, et la plateforme ajoute sa propre consigne (≈ 110 jetons d'entrée par appel).
La consommation native est donnée en crédits 1min.AI (et le solde du compte)."""
import json
import logging
import threading
import time
from collections import deque

import httpx

from . import config, db
from .aipolicy import AIError

log = logging.getLogger("onemin")
BASE = "https://api.1min.ai/openai/v1"
NATIVE = "https://api.1min.ai/api"
# Modèles que seule l'API native sert (liste de la documentation 1min.AI ; ils n'apparaissent pas dans /openai/v1/models)
NATIVE_MODELS = (
    "us.anthropic.claude-haiku-4-5-20251001-v1:0", "us.anthropic.claude-haiku-5-5", "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
    "us.anthropic.claude-sonnet-4-6", "us.anthropic.claude-sonnet-5", "us.anthropic.claude-sonnet-5-5",
    "us.anthropic.claude-opus-4-5-20251101-v1:0", "us.anthropic.claude-opus-4-6-v1", "us.anthropic.claude-opus-4-7",
    "us.anthropic.claude-opus-4-8", "us.anthropic.claude-opus-5", "us.anthropic.claude-opus-5-5",
    "us.anthropic.claude-fable-5", "us.anthropic.claude-fable-5-1",
    "gemini-3.5-flash", "gemini-3.6-flash", "gemini-3.7-flash", "gemini-3.8-flash", "gemini-3.1-pro-preview",
    "gemini-3.1-flash-lite-preview", "gemini-3-flash-preview", "grok-4.3", "grok-4.5", "grok-4.6", "grok-4.7",
    "command-r-08-2024", "moonshotai/kimi-k3", "gpt-5.5-pro", "gpt-5.4-pro", "gpt-5.2-pro", "o3-pro", "gpt-6.1-sol", "gpt-5.3-codex",
)
VISION_HINTS = ("claude", "gemini", "gpt-4o", "gpt-4.1", "gpt-5", "gpt-6", "grok-4", "-vl-", "qwen-vl", "qwen3-vl")
IMAGE_FALLBACK = "us.anthropic.claude-haiku-4-5-20251001-v1:0"  # lecture d'images quand le modèle choisi n'en lit pas
MAX_OUT = 32768          # jetons de sortie maximum annoncés
MAX_CHARS = 95_000       # la requête entière est limitée à 100 000 caractères
RATE_PER_MIN = 55        # limite annoncée : 60 requêtes par minute et par utilisateur
MODELS_TTL = 3600

_calls: deque = deque()
_lock = threading.Lock()


def api_key() -> str:
    return config.env("ONEMIN_API_KEY")


def _headers(key: str = "") -> dict:
    key = key or api_key()
    if not key:
        raise AIError("Clé 1min.AI absente (Paramètres › Intelligence artificielle).", fallback=True)
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}


def _throttle() -> None:
    """Reste sous la limite de débit du fournisseur : une file de rattrapage ne doit jamais provoquer de refus."""
    while True:
        with _lock:
            now = time.time()
            while _calls and now - _calls[0] > 60:
                _calls.popleft()
            if len(_calls) < RATE_PER_MIN:
                _calls.append(now)
                return
            wait = 60 - (now - _calls[0]) + 0.1
        if wait > 25:
            raise AIError("Limite de débit 1min.AI atteinte : réessayez dans un instant.", fallback=True)
        time.sleep(wait)


def _error(r: httpx.Response) -> AIError:
    try:
        d = r.json()
        msg = (d.get("error") or {}).get("message") if isinstance(d.get("error"), dict) else d.get("message") or d.get("error")
    except ValueError:
        msg = ""
    msg = str(msg or r.text[:200] or "").strip()
    low = msg.lower()
    if r.status_code in (401, 403):
        return AIError("Clé 1min.AI refusée ou invalide.", fallback=True)
    if r.status_code == 402 or "credit" in low or "crédit" in low or "insufficient" in low or "quota" in low:
        return AIError("Crédits 1min.AI insuffisants" + (f" ({msg[:120]})" if msg else "") + ".", fallback=True)
    if r.status_code == 429:
        return AIError("Limite de débit 1min.AI atteinte : réessayez dans un instant.", fallback=True)
    if r.status_code >= 500:
        return AIError(f"1min.AI est momentanément indisponible (code {r.status_code}).", fallback=True)
    return AIError(f"1min.AI a refusé la demande (code {r.status_code}) : {msg[:200]}")


def list_models(refresh: bool = False, key: str = "") -> list[str]:
    """Identifiants des modèles disponibles avec votre clé (mis en cache une heure)."""
    if not refresh:
        try:
            cached = json.loads(db.kv_get("onemin_models") or "{}")
            if cached.get("ids") and time.time() - cached.get("at", 0) < MODELS_TTL:
                return cached["ids"]
        except ValueError:
            pass
    try:
        r = httpx.get(BASE + "/models", headers=_headers(key), timeout=20)
    except httpx.HTTPError as e:
        raise AIError(f"1min.AI injoignable : {type(e).__name__}", fallback=True) from e
    if r.status_code != 200:
        raise _error(r)
    openai_ids = sorted({m["id"] for m in (r.json().get("data") or []) if m.get("id")})
    ids = sorted({*openai_ids, *NATIVE_MODELS})
    db.kv_set("onemin_models", json.dumps({"ids": ids, "openai": openai_ids, "at": time.time()}))
    return ids


def _openai_ids() -> set[str]:
    try:
        list_models()
        return set(json.loads(db.kv_get("onemin_models") or "{}").get("openai") or [])
    except (AIError, ValueError):
        return set()


def can_see(model: str) -> bool:
    return any(h in model for h in VISION_HINTS)


def chat(model: str, system: str, user: str, max_tokens: int = 1500, images: list[bytes] | None = None) -> tuple[str, int, int]:
    """Une réponse de texte. Retourne (texte, jetons envoyés, jetons reçus). Avec des images (JPEG) ou un modèle absent du point d'entrée
    OpenAI, l'appel passe par l'API native."""
    if len(system) + len(user) > MAX_CHARS:  # on coupe la partie variable (le message), jamais la consigne
        user = user[:max(1000, MAX_CHARS - len(system))] + "\n[… texte coupé : limite de taille du fournisseur]"
    if images or model not in _openai_ids():
        return _native(model, system, user, images or [])
    body = {"model": model, "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "max_tokens": max(16, min(int(max_tokens), MAX_OUT))}
    _throttle()
    try:
        r = httpx.post(BASE + "/chat/completions", headers=_headers(), json=body, timeout=130)
    except httpx.TimeoutException as e:
        raise AIError("1min.AI n'a pas répondu à temps (2 minutes).", fallback=True) from e
    except httpx.HTTPError as e:
        raise AIError(f"1min.AI injoignable : {type(e).__name__}", fallback=True) from e
    if r.status_code != 200:
        raise _error(r)
    try:
        d = r.json()
        choice = d["choices"][0]
        text = (choice.get("message") or {}).get("content") or ""
    except (ValueError, KeyError, IndexError, TypeError) as e:
        raise AIError("Réponse 1min.AI illisible.", fallback=True) from e
    if choice.get("finish_reason") == "content_filter":
        raise AIError("La modération du fournisseur a refusé de traiter ce message.")
    u = d.get("usage") or {}
    return text.strip(), int(u.get("prompt_tokens") or 0), int(u.get("completion_tokens") or 0)


def _upload(data: bytes, n: int) -> str:
    """Envoie une image à l'espace de stockage 1min.AI ; retourne sa clé, à citer dans la requête."""
    try:
        r = httpx.post(NATIVE + "/assets", headers={"API-KEY": api_key()}, files={"asset": (f"image{n}.jpg", data, "image/jpeg")}, timeout=90)
    except httpx.HTTPError as e:
        raise AIError(f"Envoi de l'image à 1min.AI impossible : {type(e).__name__}", fallback=True) from e
    if r.status_code != 200:
        raise _error(r)
    try:
        return r.json()["asset"]["key"]
    except (ValueError, KeyError, TypeError) as e:
        raise AIError("Réponse 1min.AI illisible (envoi d'image).", fallback=True) from e


def _native(model: str, system: str, user: str, images: list[bytes]) -> tuple[str, int, int]:
    _headers()  # lève si la clé est absente
    keys = []
    for i, data in enumerate(images[:8], 1):
        _throttle()
        keys.append(_upload(data, i))
    prompt = f"{system}\n\n---\n\n{user}" if system else user
    po = {"prompt": prompt}
    if keys:
        po["attachments"] = {"images": keys}
    _throttle()
    try:
        r = httpx.post(NATIVE + "/chat-with-ai", headers={"API-KEY": api_key(), "Content-Type": "application/json"},
                       json={"type": "UNIFY_CHAT_WITH_AI", "model": model, "promptObject": po}, timeout=130)
    except httpx.TimeoutException as e:
        raise AIError("1min.AI n'a pas répondu à temps (2 minutes).", fallback=True) from e
    except httpx.HTTPError as e:
        raise AIError(f"1min.AI injoignable : {type(e).__name__}", fallback=True) from e
    if r.status_code != 200:
        raise _error(r)
    try:
        rec = r.json()["aiRecord"]
        res = rec["aiRecordDetail"]["resultObject"]
        meta = rec.get("metadata") or {}
    except (ValueError, KeyError, TypeError) as e:
        raise AIError("Réponse 1min.AI illisible.", fallback=True) from e
    if isinstance(res, dict):  # erreur renvoyée dans le corps d'une réponse 200
        raise _error(FakeStatus(400, res))
    text = "".join(res) if isinstance(res, list) else str(res or "")
    if meta.get("finishReason") == "content_filter":
        raise AIError("La modération du fournisseur a refusé de traiter ce message.")
    _note_credits(rec, meta)
    return text.strip(), int(meta.get("inputToken") or 0), int(meta.get("totalToken") or 0) - int(meta.get("inputToken") or 0)


class FakeStatus:
    """Réponse d'erreur trouvée dans un corps 200 (même forme qu'une httpx.Response pour _error)."""

    def __init__(self, status: int, data: dict) -> None:
        self.status_code, self._d, self.text = status, data, json.dumps(data)[:300]

    def json(self) -> dict:
        return self._d


def _note_credits(rec: dict, meta: dict) -> None:
    """Retient les crédits consommés ce mois-ci (affichés dans Paramètres › Utilisation)."""
    try:
        with _lock:
            month = time.strftime("%Y-%m")
            cur = json.loads(db.kv_get("onemin_credits") or "{}")
            if cur.get("month") != month:
                cur = {"month": month, "credits": 0, "calls": 0}
            cur["credits"] += int(meta.get("credit") or 0)
            cur["calls"] += 1
            db.kv_set("onemin_credits", json.dumps(cur))
    except Exception:
        log.warning("suivi des crédits impossible", exc_info=True)


def credits_summary() -> dict:
    try:
        d = json.loads(db.kv_get("onemin_credits") or "{}")
    except ValueError:
        return {}
    return d if d.get("month") == time.strftime("%Y-%m") else {}


def test(key: str = "") -> tuple[bool, str]:
    """Vérifie la clé en lisant la liste des modèles. (ok, message pour l'utilisateur)"""
    try:
        ids = list_models(refresh=True, key=key)
    except AIError as e:
        return False, str(e)
    return True, f"Connexion réussie : {len(ids)} modèles disponibles (dont Claude et Gemini via l'API native)."
