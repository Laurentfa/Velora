"""Nettoyage de texte : HTML -> texte, retrait des citations/signatures avant l'IA."""
import re
from html.parser import HTMLParser

_SKIP = {"script", "style", "head", "title"}
_BLOCK = {"p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "table"}


class _Extractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP:
            self._skip += 1
        elif tag in _BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in _SKIP and self._skip:
            self._skip -= 1
        elif tag in _BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    ex = _Extractor()
    try:
        ex.feed(html)
    except Exception:
        return re.sub(r"<[^>]+>", " ", html)
    return normalize("".join(ex.parts))


def normalize(text: str) -> str:
    text = text.replace("\r", "").replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


_CUT = re.compile(
    r"^(De ?:|From ?:|Le .{5,80} a écrit|On .{5,80} wrote|-{3,}\s*(Original|Message)|_{5,}|Envoyé de mon)",
    re.IGNORECASE,
)


def for_ai(body: str, limit: int = 6000) -> str:
    """Garde le message récent : coupe à la première citation et retire les lignes citées."""
    kept: list[str] = []
    for i, line in enumerate(body.splitlines()):
        if i > 0 and _CUT.match(line.strip()):
            break
        if line.lstrip().startswith(">"):
            continue
        kept.append(line)
    return normalize("\n".join(kept))[:limit]


_URL = re.compile(r"(?:https?://|www\.)[^\s<>\"']+", re.I)
_TRAIL = ".,;:!?)]}»›\u201d"


def split_links(text: str) -> list[tuple[str, str]]:
    """Découpe un texte brut en morceaux ("text", …) et ("link", url) : les adresses web (http, https, www.) deviennent cliquables.
    La ponctuation qui suit l'adresse (point, virgule, parenthèse fermante…) reste dans le texte."""
    out: list[tuple[str, str]] = []
    pos = 0
    for m in _URL.finditer(text or ""):
        url = m.group(0)
        while url and url[-1] in _TRAIL:
            if url[-1] == ")" and url.count("(") >= url.count(")"):  # parenthèse qui fait partie de l'adresse
                break
            url = url[:-1]
        if len(url) < 8:
            continue
        if m.start() > pos:
            out.append(("text", text[pos:m.start()]))
        out.append(("link", url))
        pos = m.start() + len(url)
    if pos < len(text or ""):
        out.append(("text", text[pos:]))
    return out
