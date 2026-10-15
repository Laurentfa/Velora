"""Configuration. Les réglages vivent dans un fichier .env, dans le dossier de données de l'utilisateur :
 - appli installée : dossier de données du système (Mac, Windows, Linux) ;
 - développement : ~/.mailia-dev, jamais dans le dossier du projet (un mot de passe ne doit pas pouvoir finir dans un dépôt) ;
 - MAILIA_HOME : dossier de votre choix (tests).
"""
import logging
import json
import os
import re
import secrets
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values, load_dotenv
from platformdirs import user_data_dir

ROOT = Path(__file__).resolve().parent.parent


def _data_dir() -> Path:
    if os.environ.get("MAILIA_HOME"):
        d = Path(os.environ["MAILIA_HOME"])
    elif not getattr(sys, "frozen", False):
        d = Path.home() / ".mailia-dev"
    else:
        d = Path(user_data_dir("MailIA", appauthor=False))
    d.mkdir(parents=True, exist_ok=True)
    try:
        d.chmod(0o700)  # dossier privé (sans effet sous Windows)
    except OSError:
        pass
    return d


DATA_DIR = _data_dir()
ENV_PATH = DATA_DIR / ".env"
DB_PATH = DATA_DIR / "mail.db"
load_dotenv(ENV_PATH, interpolate=False)


def env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


_SIMPLE = re.compile(r"[A-Za-z0-9_@%+=:,./-]+")


def _quote(value: str) -> str:
    """Valeur écrite dans le .env : telle quelle si elle est « simple », sinon entre apostrophes (rien n'y est interprété : ni « # », ni « ${…} »,
    ni espaces de bord, ni retour à la ligne). Un mot de passe revient toujours identique."""
    if _SIMPLE.fullmatch(value):
        return value
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _read_env_file() -> dict[str, str]:
    return {k: v for k, v in dotenv_values(ENV_PATH, interpolate=False).items() if v is not None}


def atomic_write(path: Path, text: str, private: bool = True) -> None:
    """Écriture sans risque de fichier tronqué : on écrit à côté, on force sur disque, puis on remplace d'un coup."""
    tmp = path.with_name(path.name + ".tmp")
    if private:
        try:
            tmp.touch(mode=0o600)
            tmp.chmod(0o600)
        except OSError:
            pass  # Windows : droits gérés par le profil utilisateur
    with open(tmp, "w", encoding="utf-8", newline="") as f:  # fsync exige un fichier ouvert en écriture (Windows refuse sinon)
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _write_env_file(cur: dict[str, str]) -> None:
    atomic_write(ENV_PATH, "".join(f"{k}={_quote(v)}\n" for k, v in cur.items()))


# réglages secrets (hors mots de passe de boîtes) : confiés au trousseau quand il est activé
SECRET_ENV = ("ANTHROPIC_API_KEY", "ONEMIN_API_KEY", "OAUTH_GOOGLE_CLIENT_SECRET", "GCAL_REFRESH_TOKEN")


def _secret_name(name: str) -> str:
    return "env:" + name


def write_env(updates: dict[str, str]) -> None:
    """Fusionne `updates` dans le .env (valeurs vides ignorées) et met l'environnement à jour. Les secrets vont au trousseau s'il est activé."""
    cur = _read_env_file()
    clean = {k: v for k, v in updates.items() if v not in (None, "")}
    if keychain_enabled() and (kr := _keyring()):
        for name in [n for n in clean if n in SECRET_ENV]:
            try:
                kr.set_password(KEYCHAIN_SERVICE, _secret_name(name), clean[name])
                if kr.get_password(KEYCHAIN_SERVICE, _secret_name(name)) == clean[name]:
                    os.environ[name] = clean.pop(name)
                    cur.pop(name, None)
            except Exception:
                pass  # trousseau indisponible : le secret reste dans le fichier plutôt que d'être perdu
    cur.update(clean)
    _write_env_file(cur)
    os.environ.update(cur)


def forget_env(name: str) -> None:
    """Retire une valeur du .env, du trousseau et de l'environnement (write_env ignore les valeurs vides)."""
    cur = _read_env_file()
    cur.pop(name, None)
    _write_env_file(cur)
    os.environ.pop(name, None)
    if name in SECRET_ENV and (kr := _keyring()):
        try:
            kr.delete_password(KEYCHAIN_SERVICE, _secret_name(name))
        except Exception:
            pass


def calendar_backend() -> str:
    """Agenda utilisé pour les rendez-vous : « exchange » (défaut) ou « google » (relu à chaque appel)."""
    return "google" if env("CALENDAR_BACKEND") == "google" else "exchange"


def hydrate_secrets() -> None:
    """Au démarrage : remet dans l'environnement les secrets gardés dans le trousseau (la bibliothèque IA les lit là)."""
    if not keychain_enabled() or not (kr := _keyring()):
        return
    for name in SECRET_ENV:
        if not os.environ.get(name):
            try:
                v = kr.get_password(KEYCHAIN_SERVICE, _secret_name(name))
                if v:
                    os.environ[name] = v
            except Exception:
                logging.getLogger("config").warning("trousseau illisible pour %s", name, exc_info=True)


def unset_env(keys: list[str]) -> None:
    cur = _read_env_file()
    for k in keys:
        cur.pop(k, None)
        os.environ.pop(k, None)
    _write_env_file(cur)


def ensure(key: str) -> str:
    """Valeur générée une fois pour toutes (jeton d'accès mobile, secret de session)."""
    if not env(key):
        write_env({key: secrets.token_urlsafe(24)})
    return env(key)


# Un modèle par tâche, choisi dans les paramètres ; Haiku par défaut (le moins cher).
MODEL_CHOICES = {
    "claude-haiku-4-5": "Haiku 4.5 — le moins cher",
    "claude-sonnet-5-5": "Sonnet 5.5 — équilibré (~2x Haiku)",
    "claude-opus-5-5": "Opus 5.5 — le plus capable (~4x Haiku)",
}
TASKS = {
    "ANALYZE": "Tri et résumé des mails reçus",
    "BRIEF": "Synthèse du brief du jour",
    "DRAFT": "Brouillons de réponse",
    "ASK": "Questions à ma boîte, traduction, tâches et rendez-vous",
}


PROVIDERS = {"anthropic": "Claude (Anthropic)", "onemin": "1min.AI"}
ONEMIN_DEFAULT_MODEL = "gpt-4o-mini"  # exemple de la documentation 1min.AI ; la liste réelle se lit avec la clé (réglages)
ONEMIN_PREFIX = "1min:"  # un modèle « 1min:xxx » est servi par 1min.AI ; sans préfixe, par Claude


def ai_provider(task: str | None = None) -> str:
    """Fournisseur d'une tâche : son réglage propre s'il existe, sinon le fournisseur général (Claude par défaut). Relu à chaque appel."""
    own = env(f"PROVIDER_{task}") if task else ""
    if own in PROVIDERS:
        return own
    g = env("AI_PROVIDER", "anthropic")
    return g if g in PROVIDERS else "anthropic"


def provider_ready(provider: str) -> bool:
    return bool(env("ONEMIN_API_KEY" if provider == "onemin" else "ANTHROPIC_API_KEY"))


def model(task: str, provider: str | None = None) -> str:
    """Modèle d'une tâche, relu à chaque appel : un changement de paramètre s'applique tout de suite.
    Pour 1min.AI, le nom est préfixé (« 1min:gpt-4o-mini ») : c'est ce préfixe qui aiguille l'appel vers le bon fournisseur."""
    if (provider or ai_provider(task)) == "onemin":
        return ONEMIN_PREFIX + (env(f"MODEL1_{task}") or ONEMIN_DEFAULT_MODEL)
    m = env(f"MODEL_{task}", "claude-haiku-4-5")
    return m if m in MODEL_CHOICES else "claude-haiku-4-5"


def conversations_enabled() -> bool:
    """Regroupe les mails d'un même fil en une conversation (désactivé : liste mail par mail)."""
    return env("CONVERSATIONS", "1") == "1"


def ai_enabled() -> bool:
    """Interrupteur général : désactivé, plus aucun appel à l'API Claude et aucune fonction IA affichée."""
    return env("AI_ENABLED", "1") == "1"


def ai_unread_only() -> bool:
    """Analyse automatique limitée aux mails non lus (relu à chaque appel)."""
    return env("AI_UNREAD_ONLY", "1") == "1"


def ai_max_age_days() -> int:
    """Pas d'analyse automatique au-delà de N jours (0 = sans limite)."""
    try:
        return max(0, int(float(env("AI_MAX_AGE_DAYS", "30"))))
    except ValueError:
        return 30


# Interrogation légère et fréquente (la synchro est incrémentale : un cycle sans nouveauté ne télécharge rien)
SYNC_INTERVAL = {"ews": int(env("SYNC_INTERVAL_EWS", "20")), "imap": int(env("SYNC_INTERVAL_IMAP", "45"))}
# Notifications serveur (Exchange streaming / IMAP IDLE) : désactivées, ces hébergeurs n'envoient pas d'événements
PUSH = env("PUSH_NOTIFICATIONS", "0") == "1"
def sync_limit() -> int:
    """Nombre de mails récents gardés par dossier (modifiable dans la fenêtre « Dossiers »)."""
    try:
        return max(50, int(env("SYNC_LIMIT", "150")))
    except ValueError:
        return 150



AI_BATCH = int(env("AI_BATCH", "15"))
STYLE = env("REPLY_STYLE", "Professionnel, direct et courtois. Tutoiement uniquement si l'interlocuteur tutoie.")
NAME_TOKEN, FIRST_TOKEN = "‹‹NOM››", "‹‹PRENOM››"  # repères des consignes de l'IA : remplacés par le nom de l'utilisateur à l'envoi (jamais dans les mails)


def user_name() -> str:
    """Nom de l'utilisateur (Paramètres › Affichage et signature, ou assistant de première configuration). Sert à l'IA et à la signature par défaut."""
    from . import db
    try:
        v = (db.kv_get("user_name") or "").strip()
    except Exception:
        v = ""
    return v or env("SIGNATURE").strip()  # SIGNATURE : ancienne variable d'environnement


def user_first_name() -> str:
    parts = user_name().split()
    return parts[0] if parts else ""


def signature() -> str:
    return user_name()


def persona(text: str) -> str:
    """Remplace dans une consigne de l'IA les repères de nom par le nom de l'utilisateur ; sans nom connu : « l'utilisateur »."""
    return text.replace(NAME_TOKEN, user_name() or "l'utilisateur").replace(FIRST_TOKEN, user_first_name() or "l'utilisateur")


@dataclass
class AccountCfg:
    key: str
    label: str
    kind: str  # "ews" | "imap"
    email: str
    login: str
    password: str
    ai_enabled: bool
    host: str = ""
    port: int = 993
    smtp_host: str = ""
    smtp_port: int = 587
    enabled: bool = True  # False : boîte suspendue (aucune connexion, masquée partout, copies locales conservées)
    tls: str = "ssl"  # IMAP : "ssl" (993) ou "starttls" (143)
    tls_insecure: bool = False  # True : accepte un certificat non valide (serveur à certificat auto-signé), au choix de l'utilisateur


# ---------- liste des boîtes mail (modifiable dans l'appli) ----------
ACCOUNTS_PATH = DATA_DIR / "accounts.json"  # sans secret ; les mots de passe sont dans le .env (PW_<clé>)


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", unicodedata.normalize("NFKD", text.lower()).encode("ascii", "ignore").decode()).strip("-") or "compte"


def pw_var(key: str) -> str:
    return "PW_" + re.sub(r"[^A-Za-z0-9]", "_", key).upper()


KEYCHAIN_SERVICE = "MailIA"


def keychain_enabled() -> bool:
    """Option : mots de passe dans le Trousseau (macOS) / le Gestionnaire d'identifiants (Windows) plutôt que dans le fichier .env."""
    return env("USE_KEYCHAIN") == "1"


def _keyring():
    try:
        import keyring
        return keyring
    except Exception:
        return None


def keychain_available() -> bool:
    """Le trousseau fonctionne-t-il vraiment ? (écriture, relecture, effacement d'un élément d'essai)"""
    kr = _keyring()
    if kr is None:
        return False
    try:
        kr.set_password(KEYCHAIN_SERVICE, "_essai", "ok")
        ok = kr.get_password(KEYCHAIN_SERVICE, "_essai") == "ok"
        kr.delete_password(KEYCHAIN_SERVICE, "_essai")
        return ok
    except Exception:
        return False


def get_password(key: str) -> str:
    if keychain_enabled() and (kr := _keyring()):
        try:
            v = kr.get_password(KEYCHAIN_SERVICE, key)
            if v:
                return v
        except Exception:
            pass  # trousseau verrouillé ou indisponible : on retombe sur le .env s'il y a encore un mot de passe
    # anciens réglages (avant la liste de comptes) : EWS_PASSWORD / IMAP_PASSWORD
    return env(pw_var(key)) or {"exchange": env("EWS_PASSWORD"), "perso": env("IMAP_PASSWORD")}.get(key, "")


def set_password(key: str, password: str) -> None:
    if keychain_enabled() and (kr := _keyring()):
        try:
            kr.set_password(KEYCHAIN_SERVICE, key, password)
            if kr.get_password(KEYCHAIN_SERVICE, key) == password:
                unset_env([pw_var(key)])
                return
        except Exception:
            logging.getLogger("config").warning("trousseau : mot de passe de %s non vérifié", key, exc_info=True)
    write_env({pw_var(key): password})


def use_keychain(on: bool) -> tuple[int, list[str]]:
    """Déplace les mots de passe vers le Trousseau (on=True) ou les ramène dans le .env (on=False).
    Chaque mot de passe n'est retiré de son ancien emplacement qu'après avoir été relu à son nouvel emplacement."""
    kr = _keyring()
    if kr is None:
        return 0, ["Le module Trousseau n'est pas disponible."]
    moved, errors = 0, []
    for name in SECRET_ENV:  # clé API et secret OAuth : mêmes garanties que les mots de passe (relus avant d'être retirés de leur ancien emplacement)
        try:
            if on:
                v = os.environ.get(name) or _read_env_file().get(name, "")
                if not v:
                    continue
                kr.set_password(KEYCHAIN_SERVICE, _secret_name(name), v)
                if kr.get_password(KEYCHAIN_SERVICE, _secret_name(name)) != v:
                    raise RuntimeError("relecture différente")
                cur = _read_env_file(); cur.pop(name, None); _write_env_file(cur)
            else:
                v = kr.get_password(KEYCHAIN_SERVICE, _secret_name(name))
                if not v:
                    continue
                cur = _read_env_file(); cur[name] = v; _write_env_file(cur)
                kr.delete_password(KEYCHAIN_SERVICE, _secret_name(name))
            moved += 1
        except Exception as e:
            errors.append(f"{name} : {e}")
    for a in read_accounts():
        key = a["key"]
        try:
            if on:
                pw = env(pw_var(key)) or get_password(key)
                if not pw:
                    continue
                kr.set_password(KEYCHAIN_SERVICE, key, pw)
                if kr.get_password(KEYCHAIN_SERVICE, key) != pw:
                    raise RuntimeError("relecture différente")
                unset_env([pw_var(key), *{"exchange": ["EWS_PASSWORD"], "perso": ["IMAP_PASSWORD"]}.get(key, [])])
            else:
                pw = kr.get_password(KEYCHAIN_SERVICE, key)
                if not pw:
                    continue
                write_env({pw_var(key): pw})
                kr.delete_password(KEYCHAIN_SERVICE, key)
            moved += 1
        except Exception as e:
            errors.append(f"{a.get('label', key)} : {e}")
    if not errors or moved:
        write_env({"USE_KEYCHAIN": "1" if on else "0"})
    return moved, errors


_acc_cache: tuple = (None, [])


def read_accounts() -> list[dict]:
    global _acc_cache
    if ACCOUNTS_PATH.exists():
        try:
            mtime = ACCOUNTS_PATH.stat().st_mtime_ns
            if _acc_cache[0] != mtime:  # relu seulement si le fichier a changé (appelé très souvent par l'interface)
                _acc_cache = (mtime, json.loads(ACCOUNTS_PATH.read_text(encoding="utf-8")))
            return [dict(a) for a in _acc_cache[1]]
        except (OSError, json.JSONDecodeError):
            return []
    return []


def write_accounts(data: list[dict]) -> None:
    global _acc_cache
    atomic_write(ACCOUNTS_PATH, json.dumps(data, ensure_ascii=False, indent=1), private=False)
    _acc_cache = (None, [])


def set_enabled(key: str, enabled: bool) -> None:
    write_accounts([{**a, "enabled": enabled} if a["key"] == key else a for a in read_accounts()])


def enabled_keys() -> list[str]:
    return [a["key"] for a in read_accounts() if a.get("enabled", True)]


def unique_key(label: str) -> str:
    taken = {a["key"] for a in read_accounts()}
    base, n, key = slug(label), 2, slug(label)
    while key in taken:
        key, n = f"{base}-{n}", n + 1
    return key


def save_account(data: dict, password: str = "") -> str:
    """Crée ou met à jour une boîte (mot de passe vide = on garde l'actuel). Retourne sa clé."""
    accounts = read_accounts()
    key = data.get("key") or unique_key(data["label"])
    data = {**data, "key": key}
    accounts = [data if a["key"] == key else a for a in accounts] if any(a["key"] == key for a in accounts) else [*accounts, data]
    write_accounts(accounts)
    if password:
        set_password(key, password)
    return key


def remove_account(key: str) -> None:
    write_accounts([a for a in read_accounts() if a["key"] != key])
    if kr := _keyring():
        try:
            kr.delete_password(KEYCHAIN_SERVICE, key)
        except Exception:
            pass
    unset_env([pw_var(key), *({"exchange": ["EWS_PASSWORD"], "perso": ["IMAP_PASSWORD"]}.get(key, []))])


def load_accounts(enabled_only: bool = True) -> dict[str, AccountCfg]:
    """Les boîtes actives (par défaut) ou toutes, y compris celles que l'utilisateur a désactivées."""
    out: dict[str, AccountCfg] = {}
    for a in read_accounts():
        if enabled_only and not a.get("enabled", True):
            continue
        out[a["key"]] = AccountCfg(
            key=a["key"], label=a.get("label") or a["key"], kind=a.get("kind", "imap"), email=a.get("email", ""),
            login=a.get("login") or a.get("email", ""), password=get_password(a["key"]), ai_enabled=bool(a.get("ai", True)),
            host=a.get("host", ""), port=int(a.get("port") or 993), smtp_host=a.get("smtp_host", ""),
            smtp_port=int(a.get("smtp_port") or 587), tls=a.get("tls", "ssl"), enabled=bool(a.get("enabled", True)),
            tls_insecure=bool(a.get("tls_insecure", False)),
        )
    return out


import multiprocessing as _mp  # noqa: E402

if _mp.current_process().name == "MainProcess":  # la fenêtre native (processus fils) n'a pas besoin de la clé IA
    hydrate_secrets()
