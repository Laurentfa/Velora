"""Notifications du système pour ce qui mérite d'être vu tout de suite : mails urgents, messages reportés qui reviennent, relances.

macOS : centre de notifications (via osascript, sans autorisation particulière).
Windows : notification système via PowerShell (NON TESTÉ sur un vrai PC : si elle ne s'affiche pas, l'appli continue normalement)."""
import logging
import os
import subprocess
import sys
import threading

from . import db

log = logging.getLogger("notify")


def enabled() -> bool:
    return db.kv_get("notify_urgent") != "0"


def send(title: str, body: str) -> bool:
    """Affiche une notification ; ne bloque jamais et n'échoue jamais bruyamment."""
    if os.environ.get("MAILIA_NO_NOTIFY") == "1":  # tests automatiques : jamais de vraie notification sur l'écran de l'utilisateur
        log.info("notification supprimée (MAILIA_NO_NOTIFY) : %s — %s", title, body)
        return False
    if sys.platform.startswith("win"):
        return _windows_toast(title, body)
    if sys.platform != "darwin":
        log.info("notification (non affichée sur cette plateforme) : %s — %s", title, body)
        return False
    esc = lambda t: (t or "").replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")[:180]
    script = f'display notification "{esc(body)}" with title "{esc(title)}" sound name "Glass"'

    def run() -> None:
        try:
            subprocess.run(["osascript", "-e", script], capture_output=True, timeout=10)
        except Exception as e:
            log.warning("notification : %s", e)

    threading.Thread(target=run, daemon=True).start()
    return True


_PS_TOAST = r"""
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null
$t = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
$n = $t.GetElementsByTagName('text')
$n.Item(0).AppendChild($t.CreateTextNode($env:MAILIA_TITLE)) > $null
$n.Item(1).AppendChild($t.CreateTextNode($env:MAILIA_BODY)) > $null
$app = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe'
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($app).Show([Windows.UI.Notifications.ToastNotification]::new($t))
"""


def _windows_toast(title: str, body: str) -> bool:
    """Le texte passe par des variables d'environnement, jamais dans le script : aucun risque d'injection de commande."""
    def run() -> None:
        try:
            env = {**os.environ, "MAILIA_TITLE": (title or "")[:120], "MAILIA_BODY": (body or "")[:240]}
            subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-Command", _PS_TOAST],
                           env=env, capture_output=True, timeout=15, creationflags=0x08000000)  # CREATE_NO_WINDOW
        except Exception as e:
            log.warning("notification Windows : %s", e)

    threading.Thread(target=run, daemon=True).start()
    return True


def once(key: str, title: str, body: str) -> None:
    """Notifie une seule fois pour un même événement (clé en base), si les notifications sont activées."""
    if not enabled() or db.kv_get(f"notified:{key}"):
        return
    db.kv_set(f"notified:{key}", "1")
    send(title, body)
