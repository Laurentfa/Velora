"""Crée le fichier .env (droits 600). Les secrets sont saisis en masqué, jamais affichés.

Usage : .venv/bin/python scripts/setup.py
"""
import secrets
import stat
from getpass import getpass
from pathlib import Path

ENV = Path(__file__).resolve().parent.parent / ".env"


def load() -> dict[str, str]:
    d: dict[str, str] = {}
    if ENV.exists():
        for line in ENV.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                d[k] = v
    return d


def ask(d: dict[str, str], key: str, prompt: str) -> None:
    cur = "déjà défini, Entrée pour garder" if d.get(key) else "vide"
    val = getpass(f"{prompt} ({cur}) : ").strip()
    if val:
        d[key] = val


d = load()
ask(d, "ANTHROPIC_API_KEY", "Clé API Claude")
ask(d, "EWS_PASSWORD", "Mot de passe Exchange")
ask(d, "IMAP_PASSWORD", "Mot de passe de la boîte IMAP")
d.setdefault("APP_TOKEN", secrets.token_urlsafe(24))
ENV.write_text("".join(f"{k}={v}\n" for k, v in d.items()))
ENV.chmod(stat.S_IRUSR | stat.S_IWUSR)
print(f"\n.env écrit ({ENV}). Jeton d'accès à l'appli : voir la ligne APP_TOKEN du fichier.")
