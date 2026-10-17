"""Nettoyage local (sans IA) du texte dicté : hésitations, mots répétés, majuscules après un point. Appelé sur chaque morceau reconnu avant son affichage.

Prudent : on ne touche qu'à ce qui est presque toujours une erreur de l'oral. Les mots pouvant se répéter à juste titre (« vous vous êtes », « nous nous ») sont laissés."""
from __future__ import annotations

import re

_FILLER_WORDS = re.compile(r"(?i)(?<![\w'’-])(?:euh+|heu+|hum+|hmm+|mmh+|mhm+)(?![\w'’-])[\s,]*")
_REPEAT = re.compile(r"(?i)\b(je|tu|il|elle|le|la|les|un|une|des|du|de|et|à|que|qui|ce|ne|en|mais|ou|donc|pour|dans|avec|sur|par)\s+\1\b")
_SPACE_BEFORE = re.compile(r"\s+([,.;])")
_ABBREV = {"etc", "m", "mme", "mlle", "dr", "pr", "env", "cf", "p", "n", "tel", "tél", "ex", "av", "bd", "st", "ste"}


def _capitalize_after_stops(text: str) -> str:
    def fix(m: re.Match) -> str:
        before = text[:m.start(1)]
        last = re.findall(r"[\wÀ-ÿ]+", before)
        if m.group(1) == "." and last and last[-1].lower() in _ABBREV:
            return m.group(0)
        return m.group(1) + m.group(2) + m.group(3).upper()
    return re.sub(r"([.!?…])(\s+)([a-zà-ÿ])", fix, text)


def clean(text: str) -> str:
    t = (text or "").strip()
    if not t:
        return ""
    t = _FILLER_WORDS.sub("", t)
    prev = None
    while prev != t:  # « je je je » -> « je »
        prev = t
        t = _REPEAT.sub(lambda m: m.group(1), t)
    t = re.sub(r"^[.…,\s]+(?=\w)", "", t)  # « hmm… alors » -> « alors »
    t = _SPACE_BEFORE.sub(r"\1", t)
    t = re.sub(r"[ \t]{2,}", " ", t).strip(" ,")
    t = _capitalize_after_stops(t)
    if t and t[0].isalpha() and t[0].islower() and t[-1] in ".!?…":
        t = t[0].upper() + t[1:]  # une phrase terminée commence par une majuscule
    return t
