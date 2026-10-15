"""Journal technique dans un fichier (tournant : 3 × 1 Mo) du dossier de données, et diagnostic à copier pour un dépannage.
Le diagnostic est nettoyé : adresses e-mail, clés d'API, jetons et identifiants longs sont masqués ; aucun contenu de mail n'y figure."""
import logging
import logging.handlers
import platform
import re
import sys

from . import config
from .version import APP_VERSION

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})")
_SECRETS = [(re.compile(r"(?i)bearer\s+[^\s\"',;]+"), "Bearer ***"), (re.compile(r"mia_[A-Za-z0-9_-]{16,}"), "mia_***"), (re.compile(r"sk-ant-[A-Za-z0-9_-]{10,}"), "sk-ant-***"),
            (re.compile(r"(?i)(bearer|token|password|passwd|secret|authorization)([\"'=:\s]+)[^\s\"',;]{6,}"), r"\1\2***"),
            (re.compile(r"[A-Za-z0-9+/=_-]{40,}"), "***")]


def log_dir():
    d = config.DATA_DIR / "logs"
    d.mkdir(exist_ok=True)
    return d


def setup() -> None:
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s : %(message)s")
    if not any(isinstance(h, logging.handlers.RotatingFileHandler) for h in root.handlers):
        try:
            fh = logging.handlers.RotatingFileHandler(log_dir() / "mailia.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8")
            fh.setFormatter(fmt)
            root.addHandler(fh)
        except OSError:
            pass  # dossier en lecture seule : on continue sans fichier
    if not root.handlers or all(isinstance(h, logging.handlers.RotatingFileHandler) for h in root.handlers):
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        root.addHandler(sh)
    logging.getLogger("httpx").setLevel(logging.WARNING)  # inutile et bavard
    logging.getLogger("httpx2").setLevel(logging.WARNING)


def scrub(text: str) -> str:
    text = _EMAIL.sub(lambda m: "***@" + m.group(1), text)
    for rx, rep in _SECRETS:
        text = rx.sub(rep, text)
    return text


def diagnostic(lines: int = 150) -> str:
    from . import db, i18n, vault
    parts = [f"Velora {APP_VERSION} · {platform.platform()} · Python {sys.version.split()[0]} · langue {i18n.language()}"]
    try:
        accts = config.read_accounts()
        parts.append("Boîtes : " + ", ".join(f"{a.get('kind', '?')}{'' if a.get('enabled', True) else ' (désactivée)'}" for a in accts) or "aucune")
        with db.conn() as c:
            parts.append(f"Base : {c.execute('SELECT COUNT(*) FROM messages').fetchone()[0]} mails · {config.DB_PATH.stat().st_size // 1024} Ko · "
                         f"chiffrée : {'oui' if vault.is_encrypted() else 'non'}")
        parts.append(f"IA : {'activée' if config.ai_enabled() else 'désactivée'} · trousseau : {'oui' if config.keychain_enabled() else 'non'} · "
                     f"API : {db.kv_get('api_rest_on') == '1'} · MCP : {db.kv_get('api_mcp_on') == '1'} · verrouillage : {vault.lock_enabled(db)}")
    except Exception as e:
        parts.append(f"(état partiel : {type(e).__name__})")
    f = log_dir() / "mailia.log"
    tail = f.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:] if f.exists() else []
    parts.append("--- journal (les dernières lignes) ---")
    parts += tail
    return scrub("\n".join(parts))
