"""Lecture de XML venu de l'extérieur (flux RSS, configuration d'un fournisseur de messagerie, réponse d'un scanner) : refuse les déclarations d'entités, seule façon de
fabriquer un fichier qui s'étend démesurément en mémoire (« milliard de rires »). Les entités externes ne sont de toute façon jamais chargées par ElementTree."""
import re
import xml.etree.ElementTree as ET  # noqa: S405 — protégé par fromstring ci-dessous

_ENTITY = re.compile(rb"<!ENTITY", re.I)
ParseError = ET.ParseError


def fromstring(data: bytes | str) -> ET.Element:
    raw = data if isinstance(data, bytes) else data.encode("utf-8", "replace")
    if _ENTITY.search(raw):
        raise ET.ParseError("entités XML refusées")
    return ET.fromstring(raw)  # noqa: S314
