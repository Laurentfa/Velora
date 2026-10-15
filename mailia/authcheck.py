"""Lecture des résultats d'authentification de l'expéditeur (SPF, DKIM, DMARC) et de l'adresse « Répondre à », dans les en-têtes du message.

Ces résultats sont calculés par VOTRE serveur de messagerie à la réception (en-tête « Authentication-Results »). Un échec est un signal fort
d'usurpation ; une réussite prouve seulement que le domaine d'envoi est bien celui qui l'a envoyé, pas que le message est honnête (un escroc
peut avoir son propre domaine authentifié). Si le serveur n'ajoute pas cet en-tête, on ne dit rien : l'absence d'information n'est pas un défaut."""
import re
from email.utils import getaddresses

_RESULT = re.compile(r"\b(spf|dkim|dmarc)\s*=\s*([a-z]+)", re.I)
_RECEIVED_SPF = re.compile(r"^\s*(pass|fail|softfail|neutral|none|temperror|permerror)\b", re.I)
_RANK = {"pass": 0, "none": 1, "neutral": 2, "softfail": 3, "temperror": 3, "permerror": 4, "fail": 5}  # du meilleur au pire


def parse(headers: dict[str, list[str]]) -> dict:
    """`headers` : nom en minuscules -> toutes les valeurs de l'en-tête. Retourne {'spf','dkim','dmarc','reply_to'} (seulement ce qui est connu)."""
    out: dict = {}
    # le premier « Authentication-Results » est celui de votre serveur (les suivants viennent des serveurs d'avant, qu'on ne contrôle pas)
    for value in headers.get("authentication-results", []):
        found = {}
        for name, res in _RESULT.findall(value or ""):
            name, res = name.lower(), res.lower()
            if name not in found or _RANK.get(res, 6) < _RANK.get(found[name], 6):  # plusieurs signatures DKIM : la meilleure compte
                found[name] = res
        if found:
            out.update(found)
            break
    if "spf" not in out:
        for value in headers.get("received-spf", []):
            m = _RECEIVED_SPF.match(value or "")
            if m:
                out["spf"] = m.group(1).lower()
                break
    # Exchange : « Internal » = expéditeur authentifié dans l'organisation ; « Anonymous » = venu de l'extérieur (même si l'adresse affichée est interne)
    for value in headers.get("x-ms-exchange-organization-authas", []):
        if (value or "").strip().lower() in ("internal", "anonymous"):
            out["authas"] = value.strip().lower()
            break
    reply = [a for _, a in getaddresses(headers.get("reply-to", [])) if "@" in a]
    if reply:
        out["reply_to"] = reply[0].lower()[:200]
    return out


def verified(auth: dict) -> bool:
    """Le serveur a vérifié l'expéditeur : DMARC réussi, SPF et DKIM réussis, ou collègue authentifié dans l'organisation (Exchange)."""
    return (auth.get("dmarc") == "pass" or (auth.get("spf") == "pass" and auth.get("dkim") == "pass")
            or auth.get("authas") == "internal")


def summary(auth: dict) -> str:
    """« SPF ✓ · DKIM ✓ · DMARC ✕ » (ce qui est connu), ou une chaîne vide."""
    mark = lambda r: "✓" if r == "pass" else ("✕" if r in ("fail", "permerror") else "?")
    parts = [f"{n.upper()} {mark(auth[n])}" for n in ("spf", "dkim", "dmarc") if n in auth]
    return " · ".join(parts)

