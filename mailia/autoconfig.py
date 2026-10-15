"""Détection automatique des réglages d'une boîte mail à partir de l'adresse : fournisseurs connus, base publique de Mozilla Thunderbird
(autoconfig), enregistrements MX du domaine, présence d'un serveur Exchange (autodiscover). Aucune identification n'est envoyée :
seul le nom de domaine est demandé."""
import logging
import re
import urllib.request
import xml.etree.ElementTree as ET

from . import tlsutil

log = logging.getLogger("autoconfig")

# fournisseurs que l'on reconnaît par leur domaine
KNOWN = {
    "gmail.com": "gmail", "googlemail.com": "gmail",
    "outlook.com": "outlook", "hotmail.com": "outlook", "hotmail.fr": "outlook", "live.com": "outlook", "live.fr": "outlook", "msn.com": "outlook",
    "outlook.fr": "outlook", "hotmail.co.uk": "outlook",
    "icloud.com": "icloud", "me.com": "icloud", "mac.com": "icloud",
    "yahoo.com": "yahoo", "yahoo.fr": "yahoo", "ymail.com": "yahoo",
    "orange.fr": "orange", "wanadoo.fr": "orange", "free.fr": "free", "sfr.fr": "sfr", "neuf.fr": "sfr", "laposte.net": "laposte",
    "gmx.com": "gmx", "gmx.fr": "gmx", "gmx.net": "gmx", "zoho.com": "zoho", "bbox.fr": "bouygues",
}
# réglages (IMAP, SMTP) par fournisseur
PROVIDERS = {
    "gmail": {"name": "Gmail", "host": "imap.gmail.com", "smtp_host": "smtp.gmail.com", "oauth": "google",
              "help": "Gmail demande un « mot de passe d'application » (validation en 2 étapes activée) : créez-le sur myaccount.google.com/apppasswords.",
              "help_url": "https://myaccount.google.com/apppasswords"},
    "outlook": {"name": "Outlook.com / Hotmail", "host": "outlook.office365.com", "smtp_host": "smtp-mail.outlook.com", "oauth": "microsoft",
                "help": "Microsoft n'accepte plus les mots de passe simples pour les comptes Outlook.com : la connexion passe par la page Microsoft.",
                "help_url": ""},
    "m365": {"name": "Microsoft 365", "host": "outlook.office365.com", "smtp_host": "smtp.office365.com", "oauth": "microsoft",
             "help": "Microsoft 365 : la connexion passe par la page Microsoft. L'accès IMAP doit être autorisé par votre administrateur.", "help_url": ""},
    "icloud": {"name": "iCloud", "host": "imap.mail.me.com", "smtp_host": "smtp.mail.me.com", "oauth": None,
               "help": "iCloud demande un mot de passe d'application : créez-le sur appleid.apple.com (Connexion et sécurité).",
               "help_url": "https://appleid.apple.com/account/manage"},
    "yahoo": {"name": "Yahoo", "host": "imap.mail.yahoo.com", "smtp_host": "smtp.mail.yahoo.com", "oauth": None,
              "help": "Yahoo demande un mot de passe d'application (Sécurité du compte).", "help_url": "https://login.yahoo.com/account/security"},
    "orange": {"name": "Orange", "host": "imap.orange.fr", "smtp_host": "smtp.orange.fr", "oauth": None, "help": "", "help_url": ""},
    "free": {"name": "Free", "host": "imap.free.fr", "smtp_host": "smtp.free.fr", "oauth": None, "help": "", "help_url": ""},
    "sfr": {"name": "SFR", "host": "imap.sfr.fr", "smtp_host": "smtp.sfr.fr", "oauth": None, "help": "", "help_url": ""},
    "laposte": {"name": "La Poste", "host": "imap.laposte.net", "smtp_host": "smtp.laposte.net", "oauth": None, "help": "", "help_url": ""},
    "gmx": {"name": "GMX", "host": "imap.gmx.net", "smtp_host": "mail.gmx.net", "oauth": None, "help": "", "help_url": ""},
    "zoho": {"name": "Zoho", "host": "imap.zoho.com", "smtp_host": "smtp.zoho.com", "oauth": None, "help": "", "help_url": ""},
    "bouygues": {"name": "Bouygues", "host": "imap.bbox.fr", "smtp_host": "smtp.bbox.fr", "oauth": None, "help": "", "help_url": ""},
    "ovh": {"name": "OVH", "host": "ssl0.ovh.net", "smtp_host": "ssl0.ovh.net", "oauth": None, "help": "", "help_url": ""},
    "ionos": {"name": "IONOS", "host": "imap.ionos.fr", "smtp_host": "smtp.ionos.fr", "oauth": None, "help": "", "help_url": ""},
    "infomaniak": {"name": "Infomaniak", "host": "mail.infomaniak.com", "smtp_host": "mail.infomaniak.com", "oauth": None, "help": "", "help_url": ""},
}
# serveurs de messagerie du domaine (MX) -> fournisseur
MX_HINTS = [
    ("mail.protection.outlook.com", "m365"), ("google.com", "gmail"), ("googlemail.com", "gmail"), ("ovh.net", "ovh"), ("ionos.", "ionos"),
    ("kundenserver.de", "ionos"), ("infomaniak", "infomaniak"), ("zoho.", "zoho"), ("icloud.com", "icloud"), ("yahoodns.net", "yahoo"),
    ("orange.fr", "orange"), ("free.fr", "free"), ("sfr.fr", "sfr"),
]
WORKGROUP = {"gmail", "m365"}  # un domaine perso hébergé chez Google / Microsoft


def _result(provider: str | None, **extra) -> dict:
    p = PROVIDERS.get(provider or "", {})
    out = {"kind": "imap", "provider": provider or "", "name": p.get("name", ""), "host": p.get("host", ""), "port": 993, "tls": "ssl",
           "smtp_host": p.get("smtp_host", ""), "smtp_port": 587, "oauth": p.get("oauth"), "help": p.get("help", ""), "help_url": p.get("help_url", ""),
           "found": bool(p), "source": "fournisseur connu" if p else ""}
    out.update(extra)
    return out


def _get(url: str, timeout: float = 4) -> bytes | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "MailIA-autoconfig"})
        with tlsutil.opener().open(req, timeout=timeout) as r:
            return r.read(200_000) if r.status == 200 else None
    except Exception:
        return None


def parse_autoconfig(xml: bytes) -> dict | None:
    """Fichier « clientConfig » (format Mozilla) -> réglages IMAP/SMTP."""
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return None
    imap = smtp = None
    for el in root.iter("incomingServer"):
        if el.get("type") == "imap" and imap is None:
            imap = el
    for el in root.iter("outgoingServer"):
        if el.get("type") == "smtp" and smtp is None:
            smtp = el
    if imap is None:
        return None
    txt = lambda e, n: (e.findtext(n) or "").strip() if e is not None else ""
    sock = lambda e: {"SSL": "ssl", "STARTTLS": "starttls"}.get(txt(e, "socketType").upper(), "ssl")
    try:
        port = int(txt(imap, "port") or 993)
        sport = int(txt(smtp, "port") or 587)
    except ValueError:
        return None
    return {"host": txt(imap, "hostname"), "port": port, "tls": sock(imap), "smtp_host": txt(smtp, "hostname"), "smtp_port": sport,
            "name": (root.findtext(".//displayName") or "").strip()}


def mx_hosts(domain: str) -> list[str]:
    try:
        import dns.resolver
        return [str(r.exchange).rstrip(".").lower() for r in dns.resolver.resolve(domain, "MX", lifetime=4)]
    except Exception:
        return []


def has_autodiscover(domain: str) -> bool:
    try:
        import dns.resolver
        dns.resolver.resolve("autodiscover." + domain, "A", lifetime=3)
        return True
    except Exception:
        return False


def detect(email: str) -> dict:
    """Réglages probables pour cette adresse. {found, provider, name, kind, host, port, tls, smtp_host, smtp_port, oauth, help, source}"""
    m = re.fullmatch(r"[^@\s]+@([^@\s]+\.[^@\s]+)", (email or "").strip().lower())
    if not m:
        return _result(None, found=False, error="Adresse e-mail invalide.")
    domain = m.group(1)
    if domain in KNOWN:
        return _result(KNOWN[domain])
    # base publique de Mozilla : couvre des milliers de fournisseurs
    for url in (f"https://autoconfig.thunderbird.net/v1.1/{domain}", f"https://autoconfig.{domain}/mail/config-v1.1.xml"):
        data = _get(url)
        cfg = parse_autoconfig(data) if data else None
        if cfg and cfg["host"]:
            return _result(None, found=True, source="base Thunderbird", name=cfg["name"] or domain, **{k: cfg[k] for k in ("host", "port", "tls", "smtp_host", "smtp_port")})
    # serveurs de messagerie du domaine
    mx = mx_hosts(domain)
    for needle, prov in MX_HINTS:
        if any(needle in h for h in mx):
            return _result(prov, source="serveur de messagerie du domaine")
    if has_autodiscover(domain):
        return _result(None, found=True, kind="ews", name="Exchange", host="", source="Exchange (autodiscover)",
                       help="Un serveur Exchange semble répondre pour ce domaine. L'identifiant est souvent « domaine\\utilisateur » ou l'adresse e-mail.")
    if mx:  # on a au moins le serveur de messagerie : le plus courant est imap.<domaine>
        return _result(None, found=False, host=f"imap.{domain}", smtp_host=f"smtp.{domain}", source="supposé", name=domain,
                       help="Réglages supposés : vérifiez-les auprès de votre hébergeur.")
    return _result(None, found=False, host=f"imap.{domain}", smtp_host=f"smtp.{domain}", source="supposé", name=domain,
                   help="Aucun serveur de messagerie trouvé pour ce domaine : vérifiez l'adresse.")
