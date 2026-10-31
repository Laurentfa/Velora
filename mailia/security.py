"""Alerte hameçonnage : repère les signes classiques d'un faux message, sans IA ni appel réseau.

Le résultat est une aide à la prudence, pas un verdict : une alerte « suspect » demande de vérifier, une alerte
« élevé » conseille de ne cliquer sur rien."""
import json
import re
from html import unescape
from urllib.parse import urlparse

# marque -> expressions régulières des domaines légitimes (domaine enregistrable, ex. « credit-agricole.fr »)
BRANDS: dict[str, list[str]] = {
    "crédit agricole": [r"credit-agricole\.(fr|com)", r"ca-[a-z0-9-]+\.fr", r"ca-[a-z0-9-]+\.com"],
    "bnp paribas": [r"bnpparibas\.(com|net|fr)", r"bnpparibas-[a-z0-9-]+\.com", r"mabanque\.bnpparibas"],
    "société générale": [r"societegenerale\.(fr|com)", r"socgen\.com"],
    "lcl": [r"lcl\.fr", r"lcl\.com"],
    "caisse d'épargne": [r"caisse-epargne\.fr", r"caisse-epargne\.com", r"groupebpce\.(com|fr)"],
    "banque postale": [r"labanquepostale\.fr", r"labanquepostale\.com"],
    "crédit mutuel": [r"creditmutuel\.(fr|com)", r"cmb\.fr", r"cic\.fr"],
    "boursorama": [r"boursorama\.com", r"boursorama-banque\.com"],
    "paypal": [r"paypal\.(com|fr)", r"paypal-communication\.com"],
    "amazon": [r"amazon\.[a-z.]+", r"amazonses\.com", r"amazon-adsystem\.com"],
    "apple": [r"apple\.com", r"icloud\.com", r"me\.com"],
    "microsoft": [r"microsoft\.com", r"outlook\.com", r"office\.com", r"live\.com", r"microsoftonline\.com", r"office365\.com"],
    "google": [r"google\.com", r"gmail\.com", r"youtube\.com", r"googlemail\.com"],
    "netflix": [r"netflix\.com"],
    "orange": [r"orange\.(fr|com)", r"orange-business\.com", r"wanadoo\.fr"],
    "sfr": [r"sfr\.(fr|com)", r"neuf\.fr"],
    "bouygues": [r"bouyguestelecom\.fr", r"bbox\.fr"],
    "la poste": [r"laposte\.(fr|net|com)", r"colissimo\.fr", r"newsletter-laposte\.fr", r"laposte-[a-z0-9-]+\.(fr|net)", r"lapostemobile\.fr", r"laposte\.[a-z.]+"],
    "colissimo": [r"colissimo\.fr", r"laposte\.fr"],
    "chronopost": [r"chronopost\.fr", r"laposte\.fr"],
    "dhl": [r"dhl\.(com|de|fr)"],
    "ups": [r"ups\.com"],
    "fedex": [r"fedex\.com"],
    "assurance maladie": [r"ameli\.fr", r"assurance-maladie\.fr", r"cpam[a-z0-9-]*\.fr"],
    "ameli": [r"ameli\.fr", r"assurance-maladie\.fr"],
    "impots.gouv": [r"impots\.gouv\.fr", r"dgfip\.finances\.gouv\.fr", r"finances\.gouv\.fr"],
    "france travail": [r"francetravail\.fr", r"pole-emploi\.fr"],
    "caf": [r"caf\.fr"],
    "edf": [r"edf\.(fr|com)"],
    "engie": [r"engie\.(fr|com)"],
    "docusign": [r"docusign\.(com|net)"],
    "wetransfer": [r"wetransfer\.com"],
    "linkedin": [r"linkedin\.com"],
    "facebook": [r"facebook(mail)?\.com", r"meta\.com"],
    "securpass": [r"credit-agricole\.fr", r"ca-[a-z0-9-]+\.fr"],
}
URGENCY = (
    "mise à jour obligatoire", "mise à jour de votre", "compte suspendu", "compte sera suspendu", "compte a été bloqué",
    "votre compte sera", "vérifiez votre identité", "vérification de votre", "dernier avertissement", "action requise",
    "confirmez vos informations", "colis en attente", "remboursement", "dispositif de sécurité", "sous 24", "sous 48",
    "your account has been", "verify your account", "unusual activity", "activité inhabituelle", "mot de passe expire",
    "cliquez ci-dessous", "régulariser", "dernier rappel", "amende", "impayé",
)
SHORTENERS = {"bit.ly", "tinyurl.com", "goo.gl", "ow.ly", "is.gd", "cutt.ly", "rb.gy", "t.co", "shorturl.at", "tiny.cc"}
RISKY_TLDS = {"xyz", "top", "click", "work", "icu", "rest", "buzz", "monster", "cfd", "sbs", "shop", "online", "site", "live"}
SECOND_LEVEL = {"co", "com", "org", "net", "gov", "ac", "gouv"}
_LINK = re.compile(r"""<a\b[^>]*?href\s*=\s*["']([^"']+)["'][^>]*>(.*?)</a>""", re.I | re.S)
_TAG = re.compile(r"<[^>]+>")
_URLISH = re.compile(r"^(?:https?://)?((?:[a-z0-9-]+\.)+[a-z]{2,})(?:[/:?#]|$)", re.I)


def registrable(host: str) -> str:
    """« mail.exemple.co.uk » -> « exemple.co.uk » (approximation suffisante pour comparer deux adresses)."""
    parts = (host or "").lower().strip(".").split(".")
    if len(parts) >= 3 and len(parts[-1]) == 2 and parts[-2] in SECOND_LEVEL:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def _domain_of(address: str) -> str:
    return registrable((address or "").rsplit("@", 1)[-1])


def _matches(domain: str, patterns: list[str]) -> bool:
    return any(re.fullmatch(p, domain) for p in patterns)


def _mentions(text: str, brand: str) -> bool:
    return re.search(rf"(?<![a-zà-ÿ0-9]){re.escape(brand)}(?![a-zà-ÿ0-9])", text) is not None


def is_trusted(sender: str) -> bool:
    """Correspondant connu : quelqu'un à qui vous avez déjà écrit, ou une adresse de votre propre domaine."""
    from . import db, sync
    e = (sender or "").strip().lower()
    if registrable(e.rsplit("@", 1)[-1]) in {registrable(c.email.rsplit("@", 1)[-1]) for c in sync.ACCOUNTS.values()}:
        return True
    with db.conn() as c:
        r = c.execute("SELECT n_to FROM contacts WHERE email=?", (e,)).fetchone()
    return bool(r and r["n_to"])


# pièces jointes : extensions qui s'exécutent, installent ou montent un volume (jamais une pièce jointe légitime d'un courrier ordinaire)
EXECUTABLE = {"exe", "scr", "com", "pif", "bat", "cmd", "msi", "msp", "jar", "js", "jse", "vbs", "vbe", "wsf", "wsh", "ps1", "psm1", "hta", "cpl",
              "lnk", "reg", "dll", "sh", "command", "app", "pkg", "dmg", "apk", "iso", "img", "vhd", "vhdx", "chm", "inf", "gadget", "scf", "url"}
MACRO = {"docm", "xlsm", "pptm", "dotm", "xltm", "potm", "xlam", "ppam", "xlsb"}
WEB_ATTACHMENT = {"html", "htm", "shtml", "xhtml", "mht", "mhtml", "svg"}  # des formulaires de faux site de connexion arrivent souvent ainsi
ARCHIVE = {"zip", "rar", "7z", "gz", "tar", "tgz", "bz2", "xz", "cab", "ace", "arj", "z"}
DOCUMENT = {"pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "txt", "rtf", "jpg", "jpeg", "png", "gif", "csv", "odt", "ods"}


def attachment_risk(name: str) -> tuple[int, str]:
    """(poids, raison) d'un nom de pièce jointe : 0 si rien d'inquiétant."""
    n = (name or "").strip()
    low = n.lower()
    if "\u202e" in n or "\u202d" in n:  # caractère qui inverse le sens d'écriture : « facture\u202efdp.exe » s'affiche « facture exe.pdf »
        return 5, f"La pièce jointe « {n} » cache son vrai type par un caractère spécial."
    parts = low.rsplit(".", 2)
    ext = parts[-1].strip() if "." in low else ""
    if len(parts) == 3 and parts[-2].strip() in DOCUMENT and ext in (EXECUTABLE | MACRO | WEB_ATTACHMENT | ARCHIVE):
        return 5, f"La pièce jointe « {n} » a une double extension : elle se fait passer pour un document."
    if ext in EXECUTABLE:
        return 5, f"La pièce jointe « {n} » est un programme (.{ext}) : ne l'ouvrez jamais."
    if ext in MACRO:
        return 3, f"La pièce jointe « {n} » est un document à macros (.{ext}) : les macros sont le moyen d'attaque le plus courant."
    if ext in WEB_ATTACHMENT:
        return 3, f"La pièce jointe « {n} » est une page web (.{ext}) : souvent un faux formulaire de connexion."
    if ext in ARCHIVE:
        return 1, f"La pièce jointe « {n} » est une archive (.{ext}) dont le contenu n'est pas vérifié."
    return 0, ""


def risky_attachment(name: str) -> str:
    """Raison à montrer avant de télécharger un fichier dangereux (poids ≥ 3), sinon une chaîne vide."""
    w, why = attachment_risk(name)
    return why if w >= 3 else ""


def links_of(html: str, limit: int = 15) -> list[tuple[str, str]]:
    """(texte affiché, destination) des liens d'un message, pour l'analyse par l'IA."""
    out = []
    for href, inner in _LINK.findall(html or ""):
        href = unescape(href).strip()
        if href.lower().startswith(("http://", "https://")):
            out.append((unescape(_TAG.sub("", inner)).strip()[:80], href[:200]))
        if len(out) >= limit:
            break
    return out


def is_regular(sender: str, minimum: int = 3) -> bool:
    """Expéditeur déjà vu au moins `minimum` fois dans vos mails reçus (ou à qui vous avez déjà écrit) : une boîte partagée, un secrétariat, une
    imprimante… qui écrit depuis l'extérieur avec une adresse de votre organisation. Un usurpateur, lui, écrit en général pour la première fois."""
    from . import db
    e = (sender or "").strip().lower()
    with db.conn() as c:
        n = c.execute("SELECT COUNT(*) FROM messages WHERE lower(sender)=? AND folder='INBOX'", (e,)).fetchone()[0]
        r = c.execute("SELECT n_to FROM contacts WHERE email=?", (e,)).fetchone()
    return n >= minimum or bool(r and r["n_to"])


def is_known(sender: str) -> bool:
    """Correspondant connu (vous lui avez écrit, votre domaine) ou habituel (au moins 5 messages reçus de lui) : le nom d'une marque cité
    dans l'objet — « Facture Orange du mois » envoyée par votre comptable — n'est alors pas un signe d'hameçonnage."""
    return is_trusted(sender) or is_regular(sender, 5)


def check(sender: str, name: str = "", subject: str = "", html: str = "", body: str = "", trusted: bool = False,
          auth: dict | None = None, attachments: list | None = None, bulk: bool = False) -> dict:
    """{'level': '' | 'suspect' | 'élevé', 'reasons': [...], 'score': n}.
    `trusted` : correspondant connu, on ne se méfie pas d'un nom de marque cité dans l'objet.
    `auth` : résultats SPF/DKIM/DMARC et « Répondre à » (voir authcheck) ; `attachments` : [{'name': …}] ; `bulk` : message de liste de diffusion."""
    dom = _domain_of(sender)
    if re.fullmatch(r"microsoftexchange[0-9a-f]{32}@.+", (sender or "").lower()) and (auth or {}).get("authas") != "anonymous":
        return {"level": "", "score": 0, "reasons": []}  # compte système d'Exchange (notifications d'échec, boîte pleine…), « Microsoft Outlook » : pas une usurpation de marque
    reasons: list[tuple[int, str]] = []
    name_l, subj_l = (name or "").lower(), (subject or "").lower()

    # 1. se présente comme une marque connue, mais l'adresse n'est pas celle de cette marque
    for brand, pats in BRANDS.items():
        if ((_mentions(name_l, brand) or (_mentions(subj_l, brand) and not trusted))) and dom and not _matches(dom, pats):
            reasons.append((3, f"Se présente comme « {brand} » mais l'adresse d'envoi est @{dom}."))
            break
    # 2. le nom affiché contient une autre adresse ou un autre domaine
    m = re.search(r"([\w.+-]+@[\w-]+(?:\.[\w-]+)+)", name or "")
    if m and _domain_of(m.group(1)) != dom:
        reasons.append((3, f"Le nom affiché contient l'adresse {m.group(1)}, différente de l'adresse réelle (@{dom})."))
    # 3. domaine imitant une marque (ex. paypal-secure.com) sans être le sien
    base = dom.split(".")[0] if dom else ""
    for brand, pats in BRANDS.items():
        token = re.sub(r"[^a-z0-9]", "", brand)
        if len(token) >= 5 and token in re.sub(r"[^a-z0-9]", "", base) and not _matches(dom, pats):
            reasons.append((3, f"Le domaine @{dom} imite « {brand} » sans lui appartenir."))
            break
    if dom.rsplit(".", 1)[-1] in RISKY_TLDS:
        reasons.append((1, f"Domaine d'envoi peu courant (.{dom.rsplit('.', 1)[-1]})."))
    if "xn--" in (sender or "").lower():
        reasons.append((2, "Adresse avec caractères déguisés (xn--)."))

    # 4. liens : texte affiché ≠ destination, raccourcisseurs, adresses IP
    links = 0
    seen_mismatch = 0
    for href, inner in _LINK.findall(html or ""):
        href = unescape(href).strip()
        if not href.lower().startswith(("http://", "https://")):
            continue
        links += 1
        target = urlparse(href).hostname or ""
        shown = unescape(_TAG.sub("", inner)).strip()
        u = _URLISH.match(shown)
        if u and registrable(u.group(1)) != registrable(target) and seen_mismatch < 2:
            seen_mismatch += 1
            reasons.append((2, f"Un lien affiche « {u.group(1)} » mais mène vers {registrable(target)}."))
        if re.fullmatch(r"\d{1,3}(\.\d{1,3}){3}", target):
            reasons.append((3, "Un lien pointe vers une adresse IP au lieu d'un nom de site."))
        elif registrable(target) in SHORTENERS:
            reasons.append((1, "Un lien passe par un raccourcisseur d'adresse."))
        elif "xn--" in target:
            reasons.append((2, "Un lien utilise un domaine aux caractères déguisés."))
    # 5. ton d'urgence ou demande d'identifiants, surtout s'il y a des liens
    text = f"{subj_l} {(body or '')[:6000].lower()}"
    hits = [u for u in URGENCY if u in text]
    if hits and (links or "http" in text):
        reasons.append((min(2, len(hits)), "Ton d'urgence ou de menace (« " + hits[0] + " »)."))

    # 6. authentification de l'expéditeur par votre serveur (SPF / DKIM / DMARC) et adresse « Répondre à »
    auth = auth or {}
    if auth.get("dmarc") in ("fail", "permerror"):
        reasons.append((3, f"Votre serveur n'a pas pu vérifier que ce message vient bien de @{dom or 'cet expéditeur'} (DMARC échoué) : l'adresse est peut-être usurpée."))
    elif auth.get("spf") in ("fail", "permerror") and auth.get("dkim") in ("fail", "permerror", None):
        reasons.append((3, "L'envoi n'est pas autorisé par le domaine de l'expéditeur (SPF échoué) : l'adresse est peut-être usurpée."))
    elif auth.get("dkim") in ("fail", "permerror"):
        reasons.append((2, "La signature numérique du message est invalide (DKIM échoué) : il a pu être modifié ou usurpé."))
    elif auth.get("spf") == "softfail":
        reasons.append((1, "Le domaine de l'expéditeur n'autorise pas clairement ce serveur d'envoi (SPF « softfail »)."))
    if auth.get("authas") == "anonymous" and dom:  # usurpation d'un collègue : l'adresse est celle de votre organisation, mais le message vient de l'extérieur
        from . import sync
        if dom in {registrable(c.email.rsplit("@", 1)[-1]) for c in sync.ACCOUNTS.values()} and not is_regular(sender):
            reasons.append((3, f"Ce message prétend venir de votre organisation (@{dom}) mais Exchange l'a reçu de l'extérieur : l'adresse est peut-être usurpée."))
    rt = _domain_of(auth.get("reply_to", ""))
    if rt and dom and rt != dom and not trusted and not bulk:
        reasons.append((2, f"Les réponses iraient à @{rt}, un autre domaine que celui de l'expéditeur (@{dom})."))
    # 7. pièces jointes à risque (la plus grave seulement : une alerte suffit)
    worst = max(((w, why) for w, why in (attachment_risk(a.get("name", "")) for a in (attachments or [])) if w), default=None, key=lambda x: x[0])
    if worst:
        w, why = worst
        if w == 1 and any(k in f"{subj_l} {(body or '')[:3000].lower()}" for k in ("mot de passe", "password", "passwort")):
            w, why = 3, why + " Le message parle d'un mot de passe : méthode classique pour échapper aux antivirus."
        reasons.append((w, why))

    score = sum(w for w, _ in reasons)
    level = "élevé" if score >= 5 else "suspect" if score >= 3 else ""
    return {"level": level, "score": score, "reasons": [r for _, r in reasons]}


def quick(sender: str, name: str = "", subject: str = "") -> str:
    """Niveau d'alerte pour une ligne de liste (sans le contenu du message)."""
    first = check(sender, name, subject)["level"]
    return check(sender, name, subject, trusted=is_known(sender))["level"] if first else ""


def _json(v, default):
    try:
        return json.loads(v) if isinstance(v, str) and v[:1] in "[{" else default
    except ValueError:
        return default


# ---------- « ce n'est pas une arnaque » : le jugement de l'utilisateur l'emporte sur les règles ----------
def safe_senders() -> list[str]:
    """Adresses (ou domaines) que l'utilisateur a jugés sûrs : plus jamais signalés comme suspects."""
    from . import db
    return [x for x in _json(db.kv_get("phish_safe"), []) if isinstance(x, str)]


def set_safe_senders(items: list[str]) -> None:
    from . import db
    clean = sorted({i.strip().lower() for i in items if i.strip()})
    db.kv_set("phish_safe", json.dumps(clean, ensure_ascii=False))


def mark_safe(mid: str = "", sender: str = "") -> None:
    """Retient que ce message (`mid`), ou tous les messages de cet expéditeur (`sender`), ne sont pas suspects."""
    from . import db
    if sender.strip():
        set_safe_senders([*safe_senders(), sender])
    if mid:
        seen = [x for x in _json(db.kv_get("phish_safe_msgs"), []) if isinstance(x, str) and x != mid]
        db.kv_set("phish_safe_msgs", json.dumps((seen + [mid])[-500:]))


def is_safe(m: dict) -> bool:
    from . import aipolicy, db
    if m.get("id") and m["id"] in _json(db.kv_get("phish_safe_msgs"), []):
        return True
    sender = (m.get("sender") or "").strip().lower()
    return bool(sender) and any(aipolicy.matches_sender(p, sender) for p in safe_senders())


def assess(m: dict) -> dict:
    """Évalue un message de la base avec tout ce qu'on en sait (expéditeur, liens, authentification, pièces jointes, liste de diffusion)."""
    if is_safe(m):
        return {"level": "", "reasons": [], "score": 0}
    return check(m["sender"], m.get("sender_name") or "", m.get("subject") or "", m.get("body_html") or "", m.get("body") or "",
                 trusted=is_known(m["sender"]), auth=_json(m.get("auth"), {}), attachments=_json(m.get("att_json"), []),
                 bulk=bool(_json(m.get("unsub"), {})))


def quick_row(m: dict) -> str:
    """Niveau d'alerte d'une ligne de liste : l'expéditeur seul d'abord (rapide), puis le détail si le message a déjà été ouvert (authentification, pièces jointes)."""
    if is_safe(m):
        return ""
    first = quick(m["sender"], m.get("sender_name") or "", m.get("subject") or "")
    if first:
        return first
    if m.get("auth") or m.get("att_json") not in (None, "[]"):
        return check(m["sender"], m.get("sender_name") or "", m.get("subject") or "", "", "", trusted=is_known(m["sender"]),
                     auth=_json(m.get("auth"), {}), attachments=_json(m.get("att_json"), []), bulk=bool(_json(m.get("unsub"), {})))["level"]
    return ""
