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
