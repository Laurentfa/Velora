"""Désabonnement des newsletters : lecture de l'en-tête List-Unsubscribe, désabonnement « en un clic » (RFC 8058)."""
import json
import re
import urllib.error
import urllib.request

from . import tlsutil
from html import unescape

_KEYWORDS = re.compile(r"unsubscribe|d[ée]sabonn|d[ée]sinscri|se d[ée]sinscrire|opt[- ]?out|ne plus recevoir|se retirer", re.I)
_LINK = re.compile(r"""<a\b[^>]*?href\s*=\s*["']([^"']+)["'][^>]*>(.*?)</a>""", re.I | re.S)
_TAG = re.compile(r"<[^>]+>")


def parse(list_unsubscribe: str = "", list_unsubscribe_post: str = "", html: str = "") -> str:
    """JSON des moyens de se désabonner (chaîne vide s'il n'y en a aucun)."""
    https = re.findall(r"<(https?://[^>\s]+)>", list_unsubscribe or "")
    mailto = re.findall(r"<(mailto:[^>\s]+)>", list_unsubscribe or "")
    one_click = bool(https) and "one-click" in (list_unsubscribe_post or "").lower()
    link = ""
    if not https:
        for href, inner in _LINK.findall(html or ""):
            text = unescape(_TAG.sub("", inner))
            if href.lower().startswith("https://") and (_KEYWORDS.search(text) or _KEYWORDS.search(href)):
                link = unescape(href)
                break
    if not (https or mailto or link):
        return ""
    return json.dumps({"https": https[0] if https else "", "mailto": mailto[0] if mailto else "", "one_click": one_click, "link": link})


def one_click(url: str) -> tuple[bool, str]:
    """Envoie la demande de désabonnement (POST « List-Unsubscribe=One-Click »), sans suivre de redirection."""
    if not url.lower().startswith("https://"):
        return False, "Adresse non sécurisée."

    req = urllib.request.Request(url, data=b"List-Unsubscribe=One-Click", method="POST",
                                 headers={"Content-Type": "application/x-www-form-urlencoded", "User-Agent": "Mozilla/5.0"})
    try:
        with tlsutil.opener(follow=False).open(req, timeout=15) as r:
            return 200 <= r.status < 400, f"HTTP {r.status}"
    except urllib.error.HTTPError as e:
        return 200 <= e.code < 400, f"HTTP {e.code}"  # 3xx = accepté, le serveur redirige vers une page de confirmation
    except Exception as e:
        return False, str(e)[:120]
