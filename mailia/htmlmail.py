"""Affichage sûr des mails HTML.

Le HTML reçu n'est jamais fait confiance : il est nettoyé (plus de scripts, formulaires, cadres, gestionnaires
d'événements, liens « javascript: »), puis affiché dans un cadre isolé (sandbox) dont la politique de sécurité
interdit tout chargement externe. Les images distantes — souvent des pixels espions — restent bloquées tant que
l'utilisateur ne les autorise pas.
"""
import html as _html
import json
import re
import secrets

import nh3

from . import db

MAX_HTML = 600_000  # au-delà, on retombe sur le texte brut

TAGS = {
    "a", "abbr", "b", "blockquote", "br", "caption", "center", "cite", "code", "col", "colgroup", "dd", "del", "div",
    "dl", "dt", "em", "font", "h1", "h2", "h3", "h4", "h5", "h6", "hr", "i", "img", "ins", "li", "ol", "p", "pre", "q",
    "s", "small", "span", "strike", "strong", "style", "sub", "sup", "table", "tbody", "td", "tfoot", "th", "thead",
    "tr", "u", "ul",
}
ATTRS = {
    "*": {"style", "class", "align", "valign", "width", "height", "bgcolor", "color", "border", "cellpadding",
          "cellspacing", "colspan", "rowspan", "dir", "lang", "title", "background"},
    "a": {"href", "name"},
    "img": {"src", "alt", "width", "height"},
    "font": {"color", "face", "size"},
}
_REMOTE = re.compile(r"""(?:src|background)\s*=\s*["']?\s*https?://|url\(\s*["']?\s*https?://""", re.I)
_CID = re.compile(r"""cid:([^"'\s>)]+)""", re.I)

BASE_CSS = """
html{color-scheme:light}
body{margin:0;padding:14px 16px;font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;color:#1b2430;
     background:#fff;word-wrap:break-word;overflow-wrap:anywhere}
img{max-width:100%;height:auto}
table{max-width:100%}
a{color:#0a66e8}
blockquote{margin:8px 0 8px 4px;padding-left:12px;border-left:3px solid #d6e0ee;color:#5a6b80}
pre{white-space:pre-wrap}
"""

PAGE_JS = """
const post = d => parent.postMessage(Object.assign({mailia: 1}, d), '*');
const size = () => post({h: Math.ceil(document.documentElement.scrollHeight)});
new ResizeObserver(size).observe(document.body);
addEventListener('load', size); size();
document.addEventListener('click', e => {
  const a = e.target.closest('a[href]');
  if (a) { e.preventDefault(); post({url: a.href}); }
});
document.addEventListener('contextmenu', e => {
  e.preventDefault();
  const a = e.target.closest('a[href]');
  post({menu: 1, x: e.clientX, y: e.clientY, sel: String(getSelection()), link: a ? a.href : ''});
});
addEventListener('message', e => { if (e.data && e.data.mailia && e.data.all) document.execCommand('selectAll'); });
// un clic, une touche ou un défilement dans le message referme le menu du clic droit (affiché par la page, hors de ce cadre)
for (const t of ['mousedown', 'wheel', 'keydown']) document.addEventListener(t, e => { if (t !== 'mousedown' || e.button === 0) post({closemenu: 1}); }, {passive: true});
"""


def sanitize(raw: str, limit: int = MAX_HTML) -> str:
    return nh3.clean(
        raw[:limit], tags=TAGS, attributes=ATTRS, url_schemes={"http", "https", "mailto", "tel", "cid", "data"},
        link_rel="noopener noreferrer", clean_content_tags={"script"}, strip_comments=True,
    )


_IMG = re.compile(r"<img\b[^>]*>", re.I)
_SRC = re.compile(r"""\bsrc\s*=\s*["']?\s*(https?://[^"'\s>]+)""", re.I)
_SIZE = re.compile(r"""\b(width|height)\s*=\s*["']?\s*(\d+)\s*(?:px)?\b""", re.I)
_TINY_STYLE = re.compile(r"(?:width|height)\s*:\s*[01](?:px)?\s*(?:;|\"|'|$)", re.I)
# adresses de suivi d'ouverture connues (envoi en masse, relance commerciale) ; prudence : seulement des motifs sans ambiguïté
_TRACKER_URL = re.compile(
    r"list-manage\.com/track/open|/track/open|mandrillapp\.com/track|sendgrid\.net/wf/open|/e2t/o/|mailtrack\.io|pstmrk\.it|/open\.(?:gif|php|png)\b"
    r"|[?&/]open\.gif|/wf/open|/e/o/|tracking[./-]pixel|/pixel\.(?:gif|png)\b|beacon\.(?:gif|png)|\bopen[-_]?track", re.I)


def strip_trackers(clean: str) -> tuple[str, list[dict]]:
    """Retire les pixels espions d'un mail nettoyé : images distantes de 1 pixel (ou moins de 3) ou dont l'adresse est un suivi d'ouverture connu.
    Retourne (html sans eux, [{"host": site qui reçoit le signal, "kind": "pixel" | "adresse de suivi"}])."""
    found: list[dict] = []

    def repl(m: re.Match) -> str:
        tag = m.group(0)
        src = _SRC.search(tag)
        if not src:
            return tag  # image incorporée (cid:, data:) : jamais un pisteur
        sizes = {k.lower(): int(v) for k, v in _SIZE.findall(tag)}
        tiny = (sizes.get("width") is not None and sizes["width"] <= 2) or (sizes.get("height") is not None and sizes["height"] <= 2) or bool(_TINY_STYLE.search(tag))
        by_url = bool(_TRACKER_URL.search(src.group(1)))
        if not (tiny or by_url):
            return tag
        host = re.sub(r"^https?://([^/:?#]+).*$", r"\1", src.group(1), flags=re.I)
        found.append({"host": host, "kind": "pixel" if tiny else "adresse de suivi"})
        return ""

    return _IMG.sub(repl, clean), found


def has_remote_images(clean: str) -> bool:
    return bool(_REMOTE.search(clean))


def cid_refs(clean: str) -> list[str]:
    """Identifiants des images incrustées (cid:…) référencées par le message."""
    return list(dict.fromkeys(m.group(1).strip("<>") for m in _CID.finditer(clean)))


HIGHLIGHT_JS = r"""(function (terms) {
  const fold = s => s.split('').map(c => c.normalize('NFD')[0].toLowerCase()).join('');
  const esc = t => t.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const rx = new RegExp('(?<![\\p{L}\\p{N}_])(?:' + terms.slice().sort((a, b) => b.length - a.length).map(esc).join('|') + ')', 'gu');
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, {acceptNode: n =>
    n.parentElement && !/^(SCRIPT|STYLE|MARK)$/.test(n.parentElement.tagName) ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT});
  const nodes = [];
  while (walker.nextNode()) nodes.push(walker.currentNode);
  let first = null;
  for (const n of nodes) {
    const text = n.nodeValue, folded = fold(text);
    rx.lastIndex = 0;
    let m, last = 0, frag = null;
    while ((m = rx.exec(folded))) {
      frag = frag || document.createDocumentFragment();
      frag.append(text.slice(last, m.index));
      const mk = document.createElement('mark');
      mk.textContent = text.slice(m.index, m.index + m[0].length);
      frag.append(mk);
      first = first || mk;
      last = m.index + m[0].length;
    }
    if (frag) { frag.append(text.slice(last)); n.replaceWith(frag); }
  }
  if (first) setTimeout(() => first.scrollIntoView({block: 'center'}), 150);
})"""


MARK_CSS = "mark{background:#ffe566;color:#1d1d1f;border-radius:3px;padding:0 1px}"


def build_doc(clean: str, allow_images: bool = False, cid_map: dict[str, str] | None = None, terms: list[str] | None = None) -> str:
    """Document complet pour le cadre isolé. `cid_map` : cid -> URL `data:` de l'image incrustée. `terms` : mots d'une recherche à surligner
    (ils sont déjà en minuscules sans accents) ; le cadre saute à la première occurrence."""
    for cid, uri in (cid_map or {}).items():
        clean = re.sub(r"cid:" + re.escape(cid), lambda _: uri, clean, flags=re.I)
        clean = re.sub(r"cid:%3C" + re.escape(cid) + "%3E", lambda _: uri, clean, flags=re.I)
    nonce = secrets.token_urlsafe(12)
    imgs = "data: https: http:" if allow_images else "data:"
    csp = (f"default-src 'none'; img-src {imgs}; style-src 'unsafe-inline'; font-src data:; "
           f"script-src 'nonce-{nonce}'")
    theme = db.kv_get("theme") or "auto"  # le cadre est inversé en thème sombre (voir .mailframe) : les images le sont de nouveau pour garder leurs couleurs
    undo = "body{background:#dae0e9}img,video,mark{filter:invert(1) hue-rotate(180deg)}"  # #dae0e9 devient le fond sombre #1a2029 une fois inversé ; le surlignage garde son jaune
    dark_css = undo if theme == "dark" else f"@media (prefers-color-scheme: dark){{{undo}}}" if theme == "auto" else ""
    hl = ""
    if terms:
        data = json.dumps(terms, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
        hl = f'<script nonce="{nonce}">{HIGHLIGHT_JS}({data})</script>'
    return (f'<!doctype html><html><head><meta charset="utf-8">'
            f'<meta http-equiv="Content-Security-Policy" content="{_html.escape(csp, quote=True)}">'
            f'<style>{BASE_CSS}{MARK_CSS}{dark_css}</style></head><body>{clean}<script nonce="{nonce}">{PAGE_JS}</script>{hl}</body></html>')


# ---------- rédaction : HTML sortant ----------
OUT_ATTRS = {**ATTRS, "img": {"src", "alt", "width", "height", "style"}, "td": {"style", "colspan", "rowspan", "width", "align", "valign", "bgcolor"},
             "table": {"style", "border", "cellpadding", "cellspacing", "width", "align", "bgcolor"}}
_DATA_IMG = re.compile(r"""src=["']data:(image/[a-zA-Z0-9.+-]+);base64,([A-Za-z0-9+/=\s]+)["']""")


def sanitize_outgoing(raw: str) -> str:
    """Nettoie ce que l'utilisateur a écrit ou collé (Word, pages web…) : plus de scripts ni de <style> importés."""
    clean = nh3.clean(
        raw[:3_000_000], tags=TAGS - {"style"}, attributes=OUT_ATTRS, url_schemes={"http", "https", "mailto", "tel", "cid", "data"},
        link_rel="noopener noreferrer", clean_content_tags={"script", "style"}, strip_comments=True,
    )
    # Roboto (la police de l'appli) n'est pas installée partout : sans police de secours, certaines messageries afficheraient du Times
    clean = re.sub(r"font-family:\s*(?:&quot;|\"|')?Roboto(?:&quot;|\"|')?\s*(?=[;\"'])", "font-family: Roboto, Arial, sans-serif", clean, flags=re.I)
    return re.sub(r'face="Roboto"', 'face="Roboto, Arial, sans-serif"', clean, flags=re.I)


_URL = re.compile(r"(?<![\w@/.\-=\"'])((?:https?://|www\.)(?:(?!&nbsp;|&lt;|&gt;|&quot;|&#39;)[^\s<>\"'])+)", re.I)
_TAG_SPLIT = re.compile(r"(<[^>]*>)")
_TRAIL = ".,;:!?)]}»\u00bb\u2019"


def autolink(html: str) -> str:
    """Transforme les adresses web écrites dans le texte (https://exemple.fr/page, www.exemple.fr) en liens cliquables, à l'envoi.
    Ce qui est déjà un lien, une balise ou un attribut n'est jamais touché ; la ponctuation qui suit l'adresse n'en fait pas partie."""
    out, depth = [], 0
    for part in _TAG_SPLIT.split(html or ""):
        if part.startswith("<"):
            low = part.lower()
            if re.match(r"<a[\s>]", low):
                depth += 1
            elif low.startswith("</a"):
                depth = max(0, depth - 1)
            out.append(part)
            continue
        if depth or not part:
            out.append(part)
            continue

        def link(m: re.Match) -> str:
            url = m.group(1)
            tail = ""
            while url and (url[-1] in _TRAIL):
                if url[-1] == ")" and url.count("(") >= url.count(")"):  # parenthèse faisant partie de l'adresse (ex. Wikipédia)
                    break
                tail = url[-1] + tail
                url = url[:-1]
            if not re.search(r"[A-Za-z0-9]", url.split("://", 1)[-1]):
                return m.group(0)
            href = url if url.lower().startswith("http") else "https://" + url
            return f'<a href="{href}">{url}</a>{tail}'

        out.append(_URL.sub(link, part))
    return "".join(out)


def extract_inline(html: str) -> tuple[str, list[tuple[str, bytes, str, str]]]:
    """Remplace les images collées dans le texte (data:) par des références cid:, et retourne les images à joindre
    (cid, octets, type MIME, nom de fichier) : c'est ainsi que les messageries affichent une image dans le corps."""
    import base64
    inline: list[tuple[str, bytes, str, str]] = []

    def swap(m: re.Match) -> str:
        mime, b64 = m.group(1), re.sub(r"\s+", "", m.group(2))
        try:
            data = base64.b64decode(b64)
        except Exception:
            return m.group(0)
        n = len(inline) + 1
        cid = f"img{n}-{secrets.token_hex(4)}@mailia"
        inline.append((cid, data, mime, f"image{n}.{mime.split('/')[-1].replace('jpeg', 'jpg')}"))
        return f'src="cid:{cid}"'

    return _DATA_IMG.sub(swap, html), inline


def text_to_html(text: str) -> str:
    """Texte brut (ex. brouillon de l'IA) -> paragraphes HTML."""
    paras = [p for p in re.split(r"\n\s*\n", (text or "").strip()) if p.strip()]
    return "".join(f"<p>{_html.escape(p).replace(chr(10), '<br>')}</p>" for p in paras) or "<p><br></p>"


def wrap_email(body_html: str) -> str:
    return ('<html><head><meta charset="utf-8"></head><body style="font-family:Calibri,Arial,sans-serif;font-size:11pt;color:#000000">'
            f"{body_html}</body></html>")


_IMG_TAG = re.compile(r"<img\b[^>]*>", re.I)
_DATA_SRC = re.compile(r"""\bsrc\s*=\s*["']data:image/""", re.I)


def sanitize_quote(raw: str, images: bool = False) -> str:
    """Message d'origine cité : sans images (suivi, cid cassés) ni styles. `images` (transfert) : on garde les images incorporées déjà
    converties en data:, jamais les images distantes (pixels de suivi)."""
    if not images:
        return nh3.clean(raw[:200_000], tags=TAGS - {"style", "img"}, attributes=ATTRS, url_schemes={"http", "https", "mailto", "tel"},
                         link_rel="noopener noreferrer", clean_content_tags={"script", "style"}, strip_comments=True)
    clean = nh3.clean(raw[:20_000_000], tags=(TAGS - {"style"}) | {"img"}, attributes=OUT_ATTRS, url_schemes={"http", "https", "mailto", "tel", "data"},
                      link_rel="noopener noreferrer", clean_content_tags={"script", "style"}, strip_comments=True)
    return _IMG_TAG.sub(lambda m: m.group(0) if _DATA_SRC.search(m.group(0)) else "", clean)
