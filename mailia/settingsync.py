"""Vos consignes de l'IA et votre nom suivent la personne d'un ordinateur à l'autre, par la boîte Exchange elle-même.

Pourquoi : le partage des analyses (voir aishare) ne marche qu'entre ordinateurs qui ont les mêmes consignes de tri, les mêmes règles et le même nom. Plutôt que de demander
de les recopier à la main, Velora les garde dans un élément caché de la boîte (une « configuration utilisateur » de la boîte de réception : ce que Outlook utilise pour ses
propres réglages ; il ne l'affiche pas). Chaque ordinateur la relit toutes les deux minutes et se met à jour.

Fusion : chaque réglage est comparé à sa dernière valeur synchronisée. Modifié ici seulement : il part vers la boîte. Modifié ailleurs seulement : il est repris ici. Modifié des
deux côtés : le plus récent l'emporte. Premier contact avec des valeurs différentes (une consigne personnalisée ici, une autre ailleurs) : rien n'est écrasé, Velora vous demande
laquelle garder (Paramètres › Intelligence artificielle).
Ne sont synchronisés que : les quatre consignes de l'IA (tri, brief, style des réponses, règles personnelles) et votre nom. Rien d'autre."""
import json
import logging
import time
from datetime import datetime, timezone

from . import config, db

log = logging.getLogger("settingsync")
KEYS = ("prompt:analyze", "prompt:brief", "prompt:draft", "prompt:rules", "user_name")
LABELS = {"prompt:analyze": "Consigne de tri et de résumé des mails", "prompt:brief": "Consigne du brief du jour", "prompt:draft": "Style des réponses et des nouveaux mails",
          "prompt:rules": "Règles personnelles", "user_name": "Votre nom"}
VERSION = 1
INTERVAL = 120  # secondes entre deux lectures de la boîte
_last_tick = {"t": 0.0}


# ---------- réglage et boîte ----------
def enabled() -> bool:
    """Actif par défaut, comme le partage des analyses."""
    return db.kv_get("settingsync_on") != "0" and account() is not None


def account() -> str | None:
    """La boîte qui garde les réglages : la première boîte Exchange avec IA, par adresse (la même sur tous vos ordinateurs, quel que soit le nom donné à la boîte)."""
    ok = [(c.email.lower(), k) for k, c in config.load_accounts().items() if c.kind == "ews" and c.ai_enabled]
    return sorted(ok)[0][1] if ok else None


# ---------- comparaison ----------
def norm(key: str, v: str | None) -> str:
    """Deux valeurs « égales » : on ignore les espaces en trop et, pour le nom, les majuscules."""
    v = " ".join((v or "").split()) if key == "user_name" else (v or "").strip()
    return v.casefold() if key == "user_name" else v


def same(key: str, a: str | None, b: str | None) -> bool:
    return norm(key, a) == norm(key, b)


def decide(key: str, local: str, base: str | None, remote: dict | None, lt: str | None, now: str) -> tuple:
    """Que faire de ce réglage ? ('keep',) | ('pull',) | ('push', t) | ('conflict',). `remote` : {"v", "t"} ou None ; `lt` : moment où l'on a vu la modification locale."""
    rv, rt = (remote or {}).get("v"), (remote or {}).get("t") or ""
    if base is None:  # jamais synchronisé sur cet ordinateur
        if remote is None:
            return ("push", now) if norm(key, local) else ("keep",)
        if same(key, local, rv):
            return ("keep",)
        if not norm(key, local):
            return ("pull",)
        if not norm(key, rv):
            return ("push", now)
        return ("conflict",)
    local_changed = not same(key, local, base)
    remote_changed = remote is not None and not same(key, rv, base)
    if local_changed and remote_changed and same(key, local, rv):
        return ("keep",)
    if local_changed and not remote_changed:
        return ("push", lt or now)
    if remote_changed and not local_changed:
        return ("pull",)
    if local_changed and remote_changed:  # modifié des deux côtés : le plus récent l'emporte
        return ("push", lt or now) if (lt or now) > rt else ("pull",)
    return ("keep",)


# ---------- format ----------
def encode(keys: dict) -> str:
    return json.dumps({"v": VERSION, "by": config.machine_label(), "at": _now(), "keys": keys}, ensure_ascii=False, separators=(",", ":"))


def decode(raw: str | None) -> dict:
    """{clé: {"v", "t"}} lues dans la boîte (rien si absent ou illisible)."""
    if not raw:
        return {}
    try:
        d = json.loads(raw)
        if not isinstance(d, dict) or d.get("v") != VERSION:
            return {}
        cap = lambda k: 200 if k == "user_name" else 20000  # une valeur démesurée (boîte altérée) n'entre pas dans vos réglages
        return {k: {"v": str(x.get("v", ""))[:cap(k)], "t": str(x.get("t", ""))[:32]} for k, x in (d.get("keys") or {}).items() if k in KEYS and isinstance(x, dict)}
    except (ValueError, AttributeError):
        return {}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _get(key: str) -> str:
    return db.kv_get(key) or ""


# ---------- cycle ----------
def tick(force: bool = False) -> dict | None:
    """Relit la boîte et fusionne. Rend {"pulled": [...], "pushed": [...], "conflicts": [...]} ou None (inactif, trop tôt, boîte injoignable). Ne lève jamais d'erreur."""
    if not enabled() or (not force and time.time() - _last_tick["t"] < INTERVAL):
        return None
    _last_tick["t"] = time.time()
    try:
        from . import sync
        prov = sync.provider(account())
        remote = decode(prov.read_settings())
        out = {k: dict(v) for k, v in remote.items()}
        res: dict = {"pulled": [], "pushed": [], "conflicts": []}
        now = _now()
        for k in KEYS:
            local, base = _get(k), db.kv_get(f"ssync:base:{k}")
            lt = db.kv_get(f"ssync:lt:{k}") or None
            if base is not None and not same(k, local, base) and not lt:
                lt = now
                db.kv_set(f"ssync:lt:{k}", lt)  # on retient quand on a vu la modification locale (sert à départager deux modifications)
            act = decide(k, local, base, remote.get(k), lt, now)
            if act[0] == "pull":
                db.kv_set(k, remote[k]["v"])
                db.kv_set(f"ssync:base:{k}", remote[k]["v"])
                res["pulled"].append(k)
            elif act[0] == "push":
                out[k] = {"v": local, "t": act[1]}
                db.kv_set(f"ssync:base:{k}", local)
                res["pushed"].append(k)
            elif act[0] == "conflict":
                db.kv_set(f"ssync:conflict:{k}", json.dumps(remote[k], ensure_ascii=False))
                res["conflicts"].append(k)
                continue
            else:
                db.kv_set(f"ssync:base:{k}", local)
            db.kv_set(f"ssync:lt:{k}", "")
            db.kv_set(f"ssync:conflict:{k}", "")
        if res["pushed"]:
            prov.write_settings(encode(out))
        db.kv_set("ssync:at", now)
        if res["pulled"]:
            db.kv_set("ssync:pulled", json.dumps({"keys": res["pulled"], "t": now}))
            log.info("réglages repris d'un autre ordinateur : %s", ", ".join(res["pulled"]))
        if res["pushed"]:
            log.info("réglages envoyés vers la boîte : %s", ", ".join(res["pushed"]))
        db.kv_set("ssync:error", "")
        return res
    except Exception as e:  # noqa: BLE001 — pas de réseau, pas de droit… : on réessaiera au prochain tour
        db.kv_set("ssync:error", f"{type(e).__name__}")
        log.info("synchronisation des réglages impossible : %s", type(e).__name__)
        return None


def conflicts() -> dict[str, dict]:
    """{clé: valeur de la boîte} pour les réglages qui diffèrent de ceux de la boîte au premier contact."""
    out = {}
    for k in KEYS:
        try:
            raw = json.loads(db.kv_get(f"ssync:conflict:{k}") or "null")
        except ValueError:
            raw = None
        if isinstance(raw, dict):
            out[k] = raw
    return out


def resolve(key: str, use_remote: bool) -> None:
    """Choix de l'utilisateur pour un conflit : reprendre la valeur de la boîte, ou garder celle de cet ordinateur et l'envoyer."""
    c = conflicts().get(key)
    if key not in KEYS or not c:
        return
    if use_remote:
        db.kv_set(key, c["v"])
        db.kv_set(f"ssync:base:{key}", c["v"])
    else:
        db.kv_set(f"ssync:base:{key}", c["v"])  # la valeur de la boîte devient la référence : la nôtre est « modifiée » et partira au prochain tour
        db.kv_set(f"ssync:lt:{key}", _now())
    db.kv_set(f"ssync:conflict:{key}", "")
    _last_tick["t"] = 0.0
