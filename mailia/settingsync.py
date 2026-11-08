"""Vos consignes de l'IA et votre nom suivent la personne d'un ordinateur à l'autre, par la boîte Exchange elle-même.

Pourquoi : le partage des analyses (voir aishare) ne marche qu'entre ordinateurs qui ont les mêmes consignes de tri, les mêmes règles et le même nom. Plutôt que de demander
de les recopier à la main, Velora les garde dans un élément caché de la boîte (une « configuration utilisateur » de la boîte de réception : ce que Outlook utilise pour ses
propres réglages ; il ne l'affiche pas). Chaque ordinateur la relit toutes les deux minutes et se met à jour.

Fusion : chaque réglage est comparé à sa dernière valeur synchronisée. Modifié ici seulement : il part vers la boîte. Modifié ailleurs seulement : il est repris ici. Modifié des
deux côtés : le plus récent l'emporte. Premier contact avec des valeurs différentes (une consigne personnalisée ici, une autre ailleurs) : rien n'est écrasé, Velora vous demande
laquelle garder (Paramètres › Intelligence artificielle).
Ne sont synchronisés que : les quatre consignes de l'IA (tri, brief, style des réponses, règles personnelles), votre nom et vos corrections de classement (« Toujours classer ainsi
les mails de … », par adresse ou par domaine). Une correction retirée sur un ordinateur est retirée sur les autres. Rien d'autre."""
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
PREFIXES = ("sender_cat:", "domain_cat:")  # corrections de classement : un réglage par adresse ou par domaine
MAX_DYNAMIC = 1500  # plafond du nombre de corrections synchronisées (la boîte garde un seul petit élément)
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


def is_key(k: str) -> bool:
    return k in KEYS or (k.startswith(PREFIXES) and 12 < len(k) < 200)


def _valid(k: str, v: str) -> bool:
    """Une correction de classement ne peut valoir que l'une des catégories (ou rien : correction retirée)."""
    from . import aipolicy
    return k in KEYS or v == "" or v in aipolicy.CATS


def decode(raw: str | None) -> dict:
    """{clé: {"v", "t"}} lues dans la boîte (rien si absent ou illisible)."""
    if not raw:
        return {}
    try:
        d = json.loads(raw)
        if not isinstance(d, dict) or d.get("v") != VERSION:
            return {}
        cap = lambda k: 200 if k == "user_name" else 20000  # une valeur démesurée (boîte altérée) n'entre pas dans vos réglages
        out = {k: {"v": str(x.get("v", ""))[:cap(k)], "t": str(x.get("t", ""))[:32]} for k, x in (d.get("keys") or {}).items() if is_key(k) and isinstance(x, dict)}
        return {k: x for k, x in list(out.items())[:len(KEYS) + MAX_DYNAMIC] if _valid(k, x["v"])}
    except (ValueError, AttributeError):
        return {}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _get(key: str) -> str:
    return db.kv_get(key) or ""


def _dynamic_keys(remote: dict) -> list[str]:
    """Corrections de classement à examiner : celles d'ici, celles de la boîte, et celles qu'on a déjà synchronisées (pour voir une suppression)."""
    found: set[str] = {k for k in remote if k not in KEYS}
    with db.conn() as c:
        for p in PREFIXES:
            found |= {r["k"] for r in c.execute("SELECT k FROM kv WHERE k LIKE ? AND v<>''", (p + "%",))}
            found |= {r["k"][len("ssync:base:"):] for r in c.execute("SELECT k FROM kv WHERE k LIKE ? AND v<>''", ("ssync:base:" + p + "%",))}
    return sorted(k for k in found if is_key(k))[:MAX_DYNAMIC]


def _store(k: str, v: str) -> None:
    """Applique ici une valeur reprise de la boîte. Pour une correction de classement : les mails déjà reçus sont reclassés, comme si la correction avait été faite ici."""
    if k in KEYS:
        db.kv_set(k, v)
        return
    from . import aipolicy
    target = k.split(":", 1)[1]
    if v:
        db.kv_set(k, v)
        (aipolicy.apply_domain if k.startswith("domain_cat:") else aipolicy.apply_sender)(target, v)
    else:  # correction retirée ailleurs
        with db.conn() as c:
            c.execute("DELETE FROM kv WHERE k=?", (k,))
        if k.startswith("domain_cat:"):
            db.kv_set(f"domain_cat_n:{target}", "0")


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
        for k in [*KEYS, *_dynamic_keys(remote)]:
            local, base = _get(k), db.kv_get(f"ssync:base:{k}")
            lt = db.kv_get(f"ssync:lt:{k}") or None
            if base is not None and not same(k, local, base) and not lt:
                lt = now
                db.kv_set(f"ssync:lt:{k}", lt)  # on retient quand on a vu la modification locale (sert à départager deux modifications)
            act = decide(k, local, base, remote.get(k), lt, now)
            if act[0] == "conflict" and k not in KEYS:
                act = ("push", now)  # deux classements différents pour la même adresse au premier contact : celui de cet ordinateur est envoyé (ce n'est qu'une préférence de tri)
            if act[0] == "pull":
                _store(k, remote[k]["v"])
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
                if db.kv_get(f"ssync:base:{k}") != local:
                    db.kv_set(f"ssync:base:{k}", local)
            for aux in (f"ssync:lt:{k}", f"ssync:conflict:{k}"):
                if db.kv_get(aux):  # on n'écrit que ce qui change : une centaine de corrections ne doit pas laisser des centaines de lignes de plus à chaque tour
                    db.kv_set(aux, "")
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
