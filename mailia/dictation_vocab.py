"""Vocabulaire personnel de la dictée : les noms que la reconnaissance rate le plus (vos contacts, vos boîtes, « Velora »).

- Whisper : une courte amorce de texte (`whisper_prompt`) qui lui fait privilégier ces noms. C'est très efficace.
- Apple : la reconnaissance ignore ce vocabulaire ; on corrige donc après coup les mots qui ressemblent beaucoup à un nom connu (`correct`), avec prudence."""
from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

from . import config, db, sync

MAX_PROMPT_CHARS = 380  # l'amorce de Whisper est limitée (environ 220 jetons) : on garde les noms les plus utiles
_NAME = re.compile(r"[^\W\d_][\w'’ .-]{1,38}", re.U)


def _tidy(term: str) -> str:
    """« Thomas LEROY » -> « Thomas Leroy » ; les sigles courts restent en majuscules."""
    out = []
    for w in term.split():
        out.append(w.capitalize() if w.isupper() and len(w) > 4 else w)
    return " ".join(out)


def terms(limit: int = 300) -> list[str]:
    """Vous, vos boîtes, Velora, puis vos contacts (favoris d'abord, puis les plus fréquents)."""
    raw: list[str] = []
    try:
        raw.append(config.user_name() or "")
        raw += [c.label for c in sync.ACCOUNTS.values()]
    except Exception:  # noqa: BLE001 — pas de configuration : on se contente du reste
        pass
    raw.append("Velora")
    try:
        for c in db.contacts_list(limit=limit):
            for part in re.split(r"\s+[-–—]\s+", c.get("name") or ""):  # « Thomas LEROY - VENTILEA » : le nom, puis la société
                raw.append(part)
    except Exception:  # noqa: BLE001
        pass
    seen, out = set(), []
    for t in raw:
        t = _tidy(" ".join((t or "").split()))
        if len(t) < 3 or "@" in t or not _NAME.fullmatch(t) or t.lower() in seen:
            continue
        seen.add(t.lower())
        out.append(t)
    return out[:limit]


def whisper_prompt(items: list[str] | None = None) -> str | None:
    items = terms() if items is None else items
    chosen, size = [], 0
    for t in items:
        if size + len(t) + 2 > MAX_PROMPT_CHARS:
            break
        chosen.append(t)
        size += len(t) + 2
    return ("Noms propres : " + ", ".join(chosen) + ".") if chosen else None


def _fold(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s.lower()) if unicodedata.category(c) != "Mn")


def correct(text: str, items: list[str] | None = None, min_ratio: float = 0.82) -> str:
    """Remplace un mot (ou une suite de mots) presque identique à un nom connu par ce nom : « Kerjan » -> « Kerjean », « ventilea » -> « Ventiléa ».
    Prudence : seuls les noms d'au moins 6 lettres ; une simple différence d'accent ou de majuscule est corrigée, mais une vraie différence de lettres
    seulement si le mot reconnu commence par une majuscule (un mot courant comme « marin » ne devient jamais « Martin »)."""
    items = terms() if items is None else items
    cands: dict[str, str] = {}
    for t in items:
        for piece in [t] + (t.split() if " " in t else []):  # « Aurélie Kerjean » : aussi « Kerjean » seul
            if len(piece) >= 6:
                cands.setdefault(_fold(piece), piece)
    if not cands or not text:
        return text
    tokens = re.findall(r"\s+|[\w-]+|[^\w\s]", text)
    widx = [i for i, t in enumerate(tokens) if re.fullmatch(r"[\w-]+", t)]

    def span_ok(i: int, n: int) -> bool:  # les n mots se suivent, séparés par de simples espaces
        return all(widx[i + k + 1] == widx[i + k] + 2 and tokens[widx[i + k] + 1].isspace() for k in range(n - 1))

    out = list(tokens)
    i = 0
    while i < len(widx):
        for n in (2, 1):
            if i + n > len(widx) or not span_ok(i, n):
                continue
            core = " ".join(tokens[widx[i + k]] for k in range(n))
            f = _fold(core)
            best = cands.get(f)
            if best is None and core[:1].isupper():
                best = max(((SequenceMatcher(None, f, cf).ratio(), c) for cf, c in cands.items() if (" " in cf) == (n > 1)), default=(0, None), key=lambda x: x[0])
                best = best[1] if best[0] >= min_ratio else None
            if best and best != core and (n == 1 or " " in best):
                out[widx[i]] = best
                for j in range(widx[i] + 1, widx[i + n - 1] + 1):
                    out[j] = ""
                i += n - 1
                break
        i += 1
    return "".join(out)
