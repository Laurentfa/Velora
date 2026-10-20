"""Flux RSS / Atom affiché sous l'agenda du brief : titres, résumé court, heure, vignette. Adresse réglable dans les Paramètres (par défaut : « À la une » du Monde).

Lecture seule, en https, taille limitée, sans entités XML (protection contre les fichiers piégés). Résultat gardé 15 minutes."""
from __future__ import annotations

import html
import re
import time
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime
from email.utils import parsedate_to_datetime

from . import db

DEFAULT_URL = "https://www.lemonde.fr/rss/une.xml"
MAX_BYTES = 2_000_000
TTL = 900
_cache: dict = {}
_MEDIA = "{http://search.yahoo.com/mrss/}"
_ATOM = "{http://www.w3.org/2005/Atom}"


def enabled() -> bool:
    return (db.kv_get("feed_on") or "1") == "1"


def url() -> str:
    return (db.kv_get("feed_url") or "").strip() or DEFAULT_URL


def valid(u: str) -> bool:
    return bool(re.fullmatch(r"https://[^\s/]+(/\S*)?", (u or "").strip()))


def _text(x) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", x.text or "" if x is not None else ""))).strip()


def _when(s: str) -> datetime | None:
    try:
        d = parsedate_to_datetime(s) if not s[:4].isdigit() else datetime.fromisoformat(s.replace("Z", "+00:00"))
        return d.astimezone().replace(tzinfo=None)
    except (TypeError, ValueError):
        return None


def parse(data: bytes, limit: int = 12) -> dict:
    """{"title": nom du flux, "items": [{"title","link","summary","when","image"}]} ; lève ValueError si ce n'est pas un flux."""
    if b"<!ENTITY" in data:  # seule façon de définir des entités XML : on refuse (fichier piégé)
        raise ValueError("flux refusé (entités XML)")
    try:
        root = ET.fromstring(data)
    except ET.ParseError as e:
        raise ValueError("ce n'est pas un flux RSS") from e
    chan = root.find("channel")
    nodes = chan.findall("item") if chan is not None else root.findall(f"{_ATOM}entry")
    if chan is None and root.tag != f"{_ATOM}feed":
        raise ValueError("ce n'est pas un flux RSS")
    title = _text((chan if chan is not None else root).find("title" if chan is not None else f"{_ATOM}title"))
    items = []
    for n in nodes[:limit]:
        if chan is not None:
            link = _text(n.find("link"))
            summ = _text(n.find("description"))
            when = _when(_text(n.find("pubDate")))
            ttl = _text(n.find("title"))
        else:
            ln = n.find(f"{_ATOM}link")
            link = (ln.get("href") if ln is not None else "") or ""
            summ = _text(n.find(f"{_ATOM}summary"))
            when = _when(_text(n.find(f"{_ATOM}updated")))
            ttl = _text(n.find(f"{_ATOM}title"))
        img = ""
        for tag in (f"{_MEDIA}thumbnail", f"{_MEDIA}content", "enclosure"):
            e = n.find(tag)
            if e is not None and (e.get("url") or "").startswith("https://") and (tag != "enclosure" or (e.get("type") or "").startswith("image")):
                img = e.get("url")
                break
        if ttl and link.startswith(("http://", "https://")):
            items.append({"title": ttl, "link": link, "summary": summ[:220], "when": when, "image": img})
    return {"title": title, "items": items}


def fetch(force: bool = False) -> dict:
    """Bloquant : à lancer hors de l'interface. Lève ValueError / OSError en cas d'échec."""
    u = url()
    hit = _cache.get(u)
    if hit and not force and time.monotonic() - hit[0] < TTL:
        return hit[1]
    if not valid(u):
        raise ValueError("adresse du flux invalide (https obligatoire)")
    req = urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0 (compatible; Velora)", "Accept": "application/rss+xml, application/xml;q=0.9, */*;q=0.5"})
    with urllib.request.urlopen(req, timeout=10) as r:  # noqa: S310 — https vérifié par valid()
        data = r.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError("flux trop volumineux")
    res = parse(data)
    _cache[u] = (time.monotonic(), res)
    return res
