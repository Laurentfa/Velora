"""Fournisseur « compatible OpenAI » : OpenAI lui-même, mais aussi tout service qui parle le même format (Mistral, Azure OpenAI, OpenRouter, un serveur maison
comme Ollama…). Adresse, clé et modèle se règlent dans Paramètres › Intelligence artificielle ; la clé va au trousseau comme les autres.

Ce qui part chez ce fournisseur : le texte des mails à analyser ou à rédiger (et les pages numérisées, si le modèle lit les images). Rien d'autre. Le choix du service,
et de ses conditions d'utilisation des données, revient à l'utilisateur."""
import json
import logging
import re
import time

import httpx

from . import config, db, tlsutil
from .aipolicy import AIError

log = logging.getLogger("oaicompat")
MODELS_TTL = 3600
TIMEOUT = 130
MAX_OUT = 32768
VISION_HINTS = ("gpt-4o", "gpt-4.1", "gpt-4-turbo", "gpt-5", "gpt-6", "o3", "o4", "vision", "-vl", "llava", "gemini", "claude", "pixtral", "mistral-large", "mistral-medium", "mistral-small")
_REASONING = re.compile(r"^(o\d|gpt-5|gpt-6)")  # modèles qui « réfléchissent » avant de répondre : la réflexion consomme la limite de jetons de sortie


def _key() -> str:
    return config.env("OAI_API_KEY").strip()


def _client_args() -> dict:
    return {"verify": tlsutil.context(), "follow_redirects": False}  # certificat toujours vérifié ; aucune redirection suivie (la clé ne suit pas ailleurs)


def _headers(key: str = "") -> dict:
    key = (key or _key()).strip()
    h = {"Content-Type": "application/json"}
    if key:
        h["Authorization"] = f"Bearer {key}"
    return h


def _base(url: str = "") -> str:
    base = (url.strip().rstrip("/") if url else config.oai_base_url())
    if not base or not config.oai_url_ok(base):
        raise AIError("Adresse du fournisseur non valide : https://… (ou http:// vers votre réseau local).")
    return base


def _error(r: httpx.Response) -> AIError:
    """Erreur lisible d'après la réponse (au format OpenAI : {"error": {"message", "code"}})."""
    try:
        d = r.json()
        e = d.get("error")
        msg = (e.get("message") if isinstance(e, dict) else e) or d.get("message") or ""
        code = str(e.get("code") or e.get("type") or "") if isinstance(e, dict) else ""
    except (ValueError, AttributeError):
        msg, code = "", ""
    msg = str(msg or r.text[:200] or "").strip()
    low = (msg + " " + code).lower()
    if r.status_code in (401, 403):
        return AIError("Clé API refusée par le fournisseur (compatible OpenAI).", fallback=True, detail=msg[:200])
    if r.status_code == 402 or "insufficient_quota" in low or "billing" in low or "quota" in low:
        return AIError("Crédit ou quota épuisé chez le fournisseur (compatible OpenAI)" + (f" : {msg[:120]}" if msg else "") + ".", fallback=True, detail=msg[:200])
    if r.status_code == 429:
        return AIError("Limite de débit du fournisseur atteinte : réessayez dans un instant.", fallback=True, detail=msg[:200])
    if r.status_code >= 500:
        return AIError(f"Le fournisseur est momentanément indisponible (code {r.status_code}).", fallback=True)
    if r.status_code == 404:
        return AIError("Modèle ou adresse introuvable chez le fournisseur" + (f" : {msg[:160]}" if msg else "") + ".", detail=msg[:200])
    return AIError(f"Le fournisseur a refusé la demande (code {r.status_code}) : {msg[:200]}", detail=msg[:200])


def vision_setting() -> str:
    v = config.env("OAI_VISION", "auto")
    return v if v in ("auto", "1", "0") else "auto"


def can_see(model: str) -> bool:
    """Le modèle lit-il les images ? Réglage « oui » ou « non » s'il existe ; sinon : OpenAI et les modèles connus pour les lire."""
    s = vision_setting()
    if s != "auto":
        return s == "1"
    return any(h in model.lower() for h in VISION_HINTS)


def _max_param(base: str) -> str:
    """OpenAI demande « max_completion_tokens » (les modèles récents refusent « max_tokens ») ; les autres services connaissent « max_tokens »."""
    return "max_completion_tokens" if base.startswith("https://api.openai.com") else "max_tokens"


def _body(model: str, system: str, user: str, max_tokens: int, images: list[bytes] | None, param: str) -> dict:
    import base64
    if images:
        content = [{"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(b).decode()}} for b in images[:8]] + [{"type": "text", "text": user}]
    else:
        content = user
    limit = max(16, min(int(max_tokens), MAX_OUT))
    if _REASONING.match(model):
        limit = max(limit, 4000)  # la réflexion du modèle prend une part de la limite : sans marge, la réponse arrive vide
    return {"model": model, "messages": [{"role": "system", "content": system}, {"role": "user", "content": content}], param: limit}


def chat_full(model: str, system: str, user: str, max_tokens: int = 1500, images: list[bytes] | None = None) -> tuple[str, int, int]:
    """(texte, jetons envoyés, jetons reçus). Lève AIError (avec fallback=True quand l'autre fournisseur peut prendre le relais)."""
    base = _base()
    if images and not can_see(model):
        raise AIError(f"Le modèle « {model} » ne lit pas les images : choisissez-en un qui les lit (par exemple gpt-4o) ou réglez « Lit les images » sur Oui.", fallback=True)
    param = _max_param(base)
    for attempt in (1, 2):
        body = _body(model, system, user, max_tokens, images, param)
        try:
            r = httpx.post(base + "/chat/completions", headers=_headers(), json=body, timeout=TIMEOUT, **_client_args())
        except httpx.TimeoutException as e:
            raise AIError("Le fournisseur n'a pas répondu à temps (2 minutes).", fallback=True) from e
        except httpx.HTTPError as e:
            raise AIError(f"Fournisseur injoignable : {type(e).__name__}", fallback=True) from e
        if r.status_code == 400 and attempt == 1:  # un service qui connaît l'autre nom du paramètre : on réessaie une fois
            low = r.text.lower()
            if param in low and ("unsupported" in low or "not supported" in low or "unrecognized" in low or "unknown" in low or "use 'max" in low or "instead" in low):
                param = "max_tokens" if param == "max_completion_tokens" else "max_completion_tokens"
                continue
        break
    if r.status_code != 200:
        raise _error(r)
    try:
        d = r.json()
        choice = d["choices"][0]
        text = (choice.get("message") or {}).get("content") or ""
    except (ValueError, KeyError, IndexError, TypeError, AttributeError) as e:
        raise AIError("Réponse du fournisseur illisible.", fallback=True) from e
    if isinstance(text, list):  # certains services renvoient des blocs de texte
        text = "".join(str(b.get("text", "")) for b in text if isinstance(b, dict))
    if choice.get("finish_reason") == "content_filter":
        raise AIError("La modération du fournisseur a refusé de traiter ce message.")
    text = str(text).strip()
    if not text:
        why = " (le modèle a épuisé sa limite de jetons : choisissez un modèle moins bavard ou plus simple)" if choice.get("finish_reason") == "length" else ""
        raise AIError("Le fournisseur n'a renvoyé aucun texte" + why + ".", fallback=True)
    u = d.get("usage") or {}
    return text, int(u.get("prompt_tokens") or 0), int(u.get("completion_tokens") or 0)


def list_models(refresh: bool = False, key: str = "", url: str = "") -> list[str]:
    """Modèles proposés par le fournisseur (mis en cache une heure, par adresse)."""
    base = _base(url)
    if not refresh:
        try:
            c = json.loads(db.kv_get("oai_models") or "{}")
            if c.get("url") == base and c.get("ids") and time.time() - c.get("at", 0) < MODELS_TTL:
                return c["ids"]
        except ValueError:
            pass
    try:
        r = httpx.get(base + "/models", headers=_headers(key), timeout=20, **_client_args())
    except httpx.HTTPError as e:
        raise AIError(f"Fournisseur injoignable : {type(e).__name__}") from e
    if r.status_code != 200:
        raise _error(r)
    try:
        ids = sorted({str(m["id"]) for m in (r.json().get("data") or []) if isinstance(m, dict) and m.get("id")})
    except (ValueError, AttributeError) as e:
        raise AIError("Liste de modèles illisible : ce service est-il bien compatible OpenAI ?") from e
    db.kv_set("oai_models", json.dumps({"url": base, "ids": ids, "at": time.time()}))
    return ids


def known_models() -> list[str]:
    """Les modèles déjà lus (sans appel réseau), pour remplir les listes de choix."""
    try:
        c = json.loads(db.kv_get("oai_models") or "{}")
        return list(c.get("ids") or []) if c.get("url") == config.oai_base_url() else []
    except ValueError:
        return []


def test(key: str = "", url: str = "") -> tuple[bool, str]:
    """Vérifie l'adresse et la clé en lisant la liste des modèles. (ok, message pour l'utilisateur)"""
    try:
        ids = list_models(refresh=True, key=key, url=url)
    except AIError as e:
        return False, str(e)
    if not ids:
        return True, "Connexion réussie, mais le service ne liste aucun modèle : saisissez le nom du modèle à la main."
    return True, f"Connexion réussie : {len(ids)} modèles disponibles."


# $ par million de jetons (entrée, sortie), tarifs publics d'OpenAI : estimations pour le suivi des coûts. Vos propres tarifs (réglages) l'emportent ; un modèle inconnu n'est pas chiffré.
OPENAI_PRICES = {"gpt-4o-mini": (0.15, 0.60), "gpt-4o": (2.50, 10.0), "gpt-4.1-nano": (0.10, 0.40), "gpt-4.1-mini": (0.40, 1.60), "gpt-4.1": (2.0, 8.0),
                 "gpt-5-nano": (0.05, 0.40), "gpt-5-mini": (0.25, 2.0), "gpt-5": (1.25, 10.0), "o4-mini": (1.10, 4.40), "o3": (2.0, 8.0)}


def price(model: str) -> tuple[float, float] | None:
    """(entrée, sortie) en $ par million de jetons, ou None si on ne sait pas (le suivi compte alors les jetons, sans chiffrer en euros)."""
    try:
        pin, pout = float(config.env("OAI_PRICE_IN") or 0), float(config.env("OAI_PRICE_OUT") or 0)
    except ValueError:
        pin = pout = 0.0
    if pin > 0 or pout > 0:
        return pin, pout
    if not config.oai_base_url().startswith("https://api.openai.com"):
        return None  # autre service : leurs tarifs ne sont pas ceux d'OpenAI
    name = model.lower()
    for known in sorted(OPENAI_PRICES, key=len, reverse=True):  # « gpt-4o-mini-2024-07-18 » -> « gpt-4o-mini » (le nom le plus long d'abord)
        if name == known or name.startswith(known + "-"):
            return OPENAI_PRICES[known]
    return None

