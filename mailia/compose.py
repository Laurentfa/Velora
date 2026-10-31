"""Contenu initial d'un message : signature mise en forme et message d'origine cité."""
import html as _html
import json
import re
import uuid
from datetime import datetime

from . import config, db, htmlmail, tlsutil

JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
MOIS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre", "novembre", "décembre"]


def signature_on() -> bool:
    return db.kv_get("signature_on") != "0"


def signature_html(account: str = "") -> str:
    """Signature de la boîte si elle en a une, sinon la signature générale."""
    if account:
        v = db.kv_get(f"signature_html:{account}")
        if v and v.strip():
            return v
    v = db.kv_get("signature_html")
    return v if v and v.strip() else f"<p>{_html.escape(config.signature())}</p>"


# Modèles de signature de départ, copiés une seule fois parmi les vôtres (modifiables et supprimables). {{nom}} : votre nom (Paramètres › Affichage et signature).
# Tableaux et styles en ligne seulement : c'est ce que lisent Outlook, Gmail et Apple Mail ; des émojis plutôt que des images, qui seraient bloquées.
STARTER_MODELS = {
    "noel_classique": ("Noël classique",
        '<table cellpadding="0" cellspacing="0" style="border-collapse:collapse;font-family:Arial,Helvetica,sans-serif"><tr>'
        '<td style="border-top:3px solid #c62828;border-bottom:3px solid #2e7d32;padding:8px 16px">'
        '<div style="font-size:18px;font-weight:bold;color:#c62828">🎄 Joyeux Noël ! 🎄</div>'
        '<div style="font-size:14px;font-weight:bold;color:#222222;padding-top:2px">{{nom}}</div>'
        '<div style="font-size:12px;color:#2e7d32;padding-top:2px">Que cette fin d\'année vous apporte joie et sérénité.</div></td></tr></table>'),
    "noel_dore": ("Noël doré",
        '<table cellpadding="0" cellspacing="0" style="border-collapse:collapse;font-family:Georgia,\'Times New Roman\',serif"><tr>'
        '<td style="background-color:#14452f;border-left:4px solid #d4af37;padding:10px 18px">'
        '<div style="font-size:17px;color:#f5d06f">✨ Joyeuses fêtes ✨</div>'
        '<div style="font-size:14px;color:#ffffff;padding-top:3px">{{nom}}</div>'
        '<div style="font-size:12px;color:#f5d06f;padding-top:3px">Tous mes vœux pour cette nouvelle année 🎁</div></td></tr></table>'),
    "voeux_sobres": ("Vœux sobres",
        '<table cellpadding="0" cellspacing="0" style="border-collapse:collapse;font-family:Arial,Helvetica,sans-serif"><tr>'
        '<td style="border-left:3px solid #c62828;padding:2px 12px">'
        '<div style="font-size:14px;font-weight:bold;color:#222222">{{nom}}</div>'
        '<div style="font-size:12px;color:#666666;padding-top:2px">❄ Bonnes fêtes de fin d\'année</div></td></tr></table>'),
}
NO_SIGNATURE = "none"


def user_models() -> dict[str, tuple[str, str]]:
    """Modèles créés par l'utilisateur : identifiant -> (nom, HTML)."""
    if not db.kv_get("sig_models_seeded"):  # première utilisation : les modèles de départ deviennent les vôtres
        try:
            mine = [r for r in json.loads(db.kv_get("sig_models_user") or "[]") if isinstance(r, dict)]  # ceux déjà créés avant sont conservés
        except ValueError:
            mine = []
        have = {r.get("id") for r in mine}
        rows = [{"id": k, "name": v[0], "html": v[1]} for k, v in STARTER_MODELS.items() if k not in have] + mine
        db.kv_set("sig_models_user", json.dumps(rows, ensure_ascii=False))
        db.kv_set("sig_models_seeded", "1")
    try:
        rows = json.loads(db.kv_get("sig_models_user") or "[]")
        return {r["id"]: (r["name"], r["html"]) for r in rows if isinstance(r, dict) and r.get("id") and r.get("name")}
    except (ValueError, TypeError, KeyError):
        return {}


def all_models() -> dict[str, tuple[str, str]]:
    """Les modèles de signature (les vôtres, dont ceux de départ si vous les avez gardés)."""
    return user_models()


def save_user_model(model_id: str | None, name: str, html: str) -> str:
    """Crée (model_id None) ou modifie un de vos modèles ; retourne son identifiant."""
    models = user_models()
    mid = model_id if model_id in models else f"u{uuid.uuid4().hex[:8]}"
    models[mid] = (name.strip()[:80] or "Sans nom", htmlmail.sanitize_outgoing(html or ""))
    db.kv_set("sig_models_user", json.dumps([{"id": k, "name": v[0], "html": v[1]} for k, v in models.items()], ensure_ascii=False))
    return mid


def delete_user_model(model_id: str) -> None:
    models = user_models()
    if models.pop(model_id, None) is not None:
        db.kv_set("sig_models_user", json.dumps([{"id": k, "name": v[0], "html": v[1]} for k, v in models.items()], ensure_ascii=False))


def christmas_auto() -> bool:
    """Signature de Noël posée d'office en décembre (réglable dans Paramètres › Affichage et signature)."""
    return db.kv_get("sig_xmas_auto") != "0"


def christmas_model() -> str:
    models = all_models()
    m = db.kv_get("sig_xmas_model")
    return m if m in models else next(iter(models), "")


def default_model() -> str | None:
    """Modèle posé d'office : celui de Noël en décembre si le réglage est actif, sinon aucun (votre signature habituelle)."""
    return (christmas_model() or None) if christmas_auto() and datetime.now().month == 12 else None


def model_html(model: str) -> str:
    return all_models()[model][1].replace("{{nom}}", _html.escape(config.signature() or ""))


def sig_block(account: str = "", model: str | None = None) -> str:
    """Signature à insérer. model : None = automatique (voir default_model), un identifiant de STARTER_MODELS, ou NO_SIGNATURE ; "" = ma signature."""
    explicit = model is not None
    model = default_model() if model is None else model
    if model == NO_SIGNATURE or (not explicit and not signature_on()):
        return ""
    if model in all_models():
        return f'<div><br></div><div class="msig">{htmlmail.sanitize_outgoing(model_html(model))}</div>'
    # images incorporées dès l'insertion : l'éditeur (WebKit) n'affiche pas toujours celles d'un hébergeur distant, le message final les aura de toute façon
    body = embed_signature_images(htmlmail.sanitize_outgoing(signature_html(account)), account)
    return f'<div><br></div><div class="msig">{body}</div>'


def swap_signature(html: str, account: str, model: str | None = None) -> str:
    """Remplace la signature d'un message en cours d'écriture par celle d'une autre boîte (changement de « De ») ou par un autre modèle.
    Sans signature en place, la nouvelle est ajoutée avant le message cité (ou à la fin)."""
    marker = '<div class="msig">'
    html = html or ""
    i = html.find(marker)
    if i < 0:
        if model is None:
            return html
        j = html.find("<hr")
        j = len(html) if j < 0 else j
        return html[:j] + sig_block(account, model) + html[j:]
    # la signature va de son <div class="msig"> jusqu'à la citation (<hr) ou la fin
    j = html.find("<hr", i)
    j = len(html) if j < 0 else j
    start = html.rfind("<div><br></div>", 0, i)
    start = start if start >= 0 and html[start:i].strip() == "<div><br></div>" else i
    return html[:start] + sig_block(account, model) + html[j:]


def quote_html(m: dict, images: bool = False) -> str:
    """En-tête « De / Envoyé / À / Objet » puis le message d'origine, comme dans Outlook."""
    d = datetime.fromisoformat(m["date"]).astimezone()
    when = f"{JOURS[d.weekday()]} {d.day} {MOIS[d.month - 1]} {d.year} {d:%H:%M}"
    who = f"{_html.escape(m['sender_name'] or m['sender'])} &lt;{_html.escape(m['sender'])}&gt;"
    head = f"<b>De :</b> {who}<br><b>Envoyé :</b> {when}<br><b>À :</b> {_html.escape(m['to_addrs'] or '')}<br>"
    if m.get("cc_addrs"):
        head += f"<b>Cc :</b> {_html.escape(m['cc_addrs'])}<br>"
    head += f"<b>Objet :</b> {_html.escape(m['subject'] or '')}"
    original = htmlmail.sanitize_quote(m["body_html"], images) if m.get("body_html") else htmlmail.text_to_html((m.get("body") or "")[:20000])
    return ('<hr style="border:0;border-top:1px solid #b5c4df">'
            f'<p style="font-size:10pt;color:#44546a">{head}</p>'
            f'<blockquote style="margin:0 0 0 4px;padding-left:12px;border-left:3px solid #d6e0ee">{original}</blockquote>')


def initial_html(reply_to: dict | None = None, account: str = "", images: bool = False) -> str:
    return "<p><br></p>" + sig_block(account) + (quote_html(reply_to, images) if reply_to else "")


def from_text(text: str, reply_to: dict | None = None, account: str = "") -> str:
    """Brouillon de l'IA (texte) -> contenu de l'éditeur, avec signature et message cité."""
    return htmlmail.text_to_html(text) + sig_block(account) + (quote_html(reply_to) if reply_to else "")


_MARKS = re.compile(r'(<div>\s*<br\s*/?>\s*</div>\s*)?<div class="msig"|<hr', re.I)


def split_user_part(html: str) -> tuple[str, str]:
    """(ce que l'utilisateur a écrit, signature + message cité). Sert à corriger / réécrire sans toucher au reste."""
    m = _MARKS.search(html or "")
    return ((html[:m.start()], html[m.start():]) if m else (html or "", ""))


_TITLES = {"docteur", "dr", "mme", "madame", "m.", "monsieur", "mr", "me", "maître", "pr", "professeur"}


def first_name(display: str) -> str:
    words = re.sub(r"[<>\"@].*$", "", display or "").replace("-", " ").split()
    for w in words:
        if w.lower().strip(".") not in {t.strip(".") for t in _TITLES}:
            return w.capitalize() if w.isupper() else w
    return ""


def fill_template(html: str, m: dict | None = None) -> str:
    """Variables des modèles : {{prenom}} {{nom}} {{date}} {{objet}} (selon le message auquel on répond)."""
    now = datetime.now()
    values = {
        "prenom": first_name((m or {}).get("sender_name", "")), "nom": (m or {}).get("sender_name", ""),
        "date": f"{JOURS[now.weekday()]} {now.day} {MOIS[now.month - 1]} {now.year}", "objet": (m or {}).get("subject", ""),
    }
    # valeur inconnue (nouveau message, pas d'interlocuteur) : le marqueur reste visible pour être complété à la main
    return re.sub(r"\{\{\s*(\w+)\s*\}\}", lambda x: _html.escape(values.get(x.group(1).lower()) or x.group(0)), html)


STARTER_TEMPLATES = [
    ("Accusé de réception", "", "<p>Bonjour {{prenom}},</p><p>Bien reçu, je prends en charge votre demande et je reviens vers vous rapidement.</p><p>Cordialement,</p>"),
    ("Demande de précisions", "", "<p>Bonjour {{prenom}},</p><p>Merci pour votre message. Pourriez-vous me préciser les points suivants afin que je puisse avancer :</p><ul><li>…</li><li>…</li></ul><p>Cordialement,</p>"),
    ("Relance cordiale", "", "<p>Bonjour {{prenom}},</p><p>Je me permets de revenir vers vous au sujet de mon précédent message. Auriez-vous pu y jeter un œil ?</p><p>Je reste à votre disposition.</p><p>Cordialement,</p>"),
]


def seed_templates() -> None:
    """Quelques modèles de départ, créés une seule fois."""
    if db.kv_get("templates_seeded"):
        return
    if not db.templates_list():
        for name, subject, body in STARTER_TEMPLATES:
            db.template_save(None, name, subject, body)
    db.kv_set("templates_seeded", "1")


# ---------- images distantes de la signature : intégrées au message à l'envoi ----------
_IMG_SRC = re.compile(r"""(<img\b[^>]*?\bsrc\s*=\s*)(["'])(https?://[^"']+)\2""", re.I)


def _fetch_signature_image(url: str) -> tuple[bytes, str] | None:
    """Image distante de la signature, téléchargée une fois puis gardée sur le disque (elle survit à la disparition de l'hébergeur)."""
    import hashlib
    import ipaddress
    import socket
    import urllib.request
    from urllib.parse import urlparse
    cache = config.DATA_DIR / "imgcache"
    key = hashlib.sha1(url.encode()).hexdigest()
    hit = next(iter(cache.glob(key + ".*")), None) if cache.exists() else None
    if hit:
        return hit.read_bytes(), "image/" + hit.suffix.lstrip(".").replace("jpg", "jpeg")
    def public(u: str) -> bool:
        """Adresse publique seulement : on ne va jamais chercher une image sur le réseau local (ni via une redirection)."""
        h = urlparse(u).hostname or ""
        if urlparse(u).scheme not in ("http", "https") or not h:
            return False
        try:
            for fam, *_rest, sa in socket.getaddrinfo(h, None):
                ip = ipaddress.ip_address(sa[0])
                if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
                    return False
        except (OSError, ValueError):
            return False
        return True

    if not public(url):
        return None
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Macintosh) AppleWebKit/605.1.15", "Accept": "image/*"})
        with tlsutil.opener(check_redirect=public).open(req, timeout=8) as r:
            mime = (r.headers.get_content_type() or "").lower()
            data = r.read(700_000)
    except Exception:
        return None
    ext = {"image/png": "png", "image/jpeg": "jpg", "image/gif": "gif", "image/webp": "webp"}.get(mime)
    if not ext or len(data) >= 700_000 or not data:
        return None
    cache.mkdir(parents=True, exist_ok=True)
    (cache / f"{key}.{ext}").write_bytes(data)
    return data, mime


def embed_signature_images(html: str, account: str = "") -> str:
    """Remplace par des images incorporées (data:) les images distantes qui viennent de la signature : elles s'affichent chez tous les
    destinataires, même quand leur messagerie bloque les images externes ou si l'hébergement du logo disparaît."""
    import base64
    wanted = {_html.unescape(m.group(3)) for m in _IMG_SRC.finditer(signature_html(account))}
    if not wanted:
        return html

    def repl(m: re.Match) -> str:
        url = _html.unescape(m.group(3))
        if url not in wanted:
            return m.group(0)
        got = _fetch_signature_image(url)
        if not got:
            return m.group(0)
        data, mime = got
        return f"{m.group(1)}{m.group(2)}data:{mime};base64,{base64.b64encode(data).decode()}{m.group(2)}"

    return _IMG_SRC.sub(repl, html)
