"""Les tests tournent dans un dossier de données jetable : jamais vos vrais mails, mots de passe ou trousseau."""
import json
import os
import sys
import tempfile
from pathlib import Path

HOME = Path(tempfile.mkdtemp(prefix="mailia-tests-"))
os.environ["MAILIA_HOME"] = str(HOME)
os.environ["MAILIA_DRY_RUN"] = "1"  # aucun envoi réel
os.environ["MAILIA_NO_NOTIFY"] = "1"  # ni notification sur l'écran de l'utilisateur
os.environ["PW_TESTBOX"] = "motdepasse-de-test"
(HOME / "accounts.json").write_text(json.dumps([
    {"key": "testbox", "label": "Boîte de test", "kind": "imap", "email": "moi@example.org", "login": "", "host": "imap.invalid", "port": 993,
     "tls": "ssl", "smtp_host": "smtp.invalid", "smtp_port": 587, "ai": True, "enabled": True},
    {"key": "noai", "label": "Sans IA", "kind": "imap", "email": "prive@example.org", "login": "", "host": "imap.invalid", "port": 993,
     "tls": "ssl", "smtp_host": "smtp.invalid", "smtp_port": 587, "ai": False, "enabled": True}]))
os.environ["PW_NOAI"] = "x"
os.environ["ANTHROPIC_API_KEY"] = "sk-ant-cle-factice-pour-les-tests"  # sans clé, les fonctions IA sont masquées : les tests de l'interface les supposent présentes
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def clean_db():
    """Base propre avant chaque test."""
    from mailia import db
    db.init()
    with db.conn() as c:
        for t in ("messages", "fts", "kv", "drafts", "draft_files", "outbox", "outbox_files", "api_keys", "api_log", "api_idem", "corrections",
                  "contacts", "groups", "group_members", "usage", "usage_log", "snoozes", "folders"):
            c.execute(f"DELETE FROM {t}")
    yield


def add_message(mid="testbox|INBOX|1", account="testbox", sender="alice@example.com", subject="Bonjour", body="texte", date=None,
                **extra):
    from datetime import datetime, timedelta, timezone

    from mailia import db
    date = date or (datetime.now(timezone.utc) - timedelta(hours=2)).replace(microsecond=0).isoformat()  # toujours « récent » : aucun test ne dépend de la date du jour
    row = {"id": mid, "account": account, "uid": mid.split("|")[-1], "subject": subject, "sender": sender, "sender_name": sender.split("@")[0].title(),
           "to_addrs": "moi@example.org", "date": date, "body": body, "is_read": 0, "has_att": 0, "folder": "INBOX", "ckey": mid,
           "att_json": "[]", "body_html": "", "unsub": "", "bcc_addrs": "", "auth": "{}", "receipt_to": ""}
    row.update(extra)
    with db.conn() as c:
        cols = ",".join(row)
        c.execute(f"INSERT OR REPLACE INTO messages({cols}) VALUES({','.join('?' * len(row))})", list(row.values()))
        c.execute("INSERT INTO fts(id,subject,sender,body) VALUES(?,?,?,?)", (mid, subject, sender, body))
    return mid


async def wait_until(cond, timeout: float = 15.0, step: float = 0.05) -> None:
    """Attend que la condition devienne vraie (au plus `timeout` secondes) : une attente fixe échoue sur une machine lente (serveur Windows de la CI)."""
    import asyncio
    import time
    end = time.monotonic() + timeout
    while not cond():
        if time.monotonic() > end:
            raise AssertionError("condition jamais remplie après %.0f s" % timeout)
        await asyncio.sleep(step)
