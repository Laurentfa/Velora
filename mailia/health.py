"""Ce qui ne marche pas en ce moment, en une liste de phrases : alimente la pastille d'avertissement de la colonne de gauche."""
import time

from . import aipolicy, chromebridge, fleet, modules, sync


def refresh() -> None:
    """Contrôles qui interrogent d'autres éléments (extension Chrome). Hors du fil de l'interface."""
    try:
        chromebridge.check_extension()
    except Exception:  # noqa: BLE001 — un contrôle de santé ne doit jamais devenir lui-même un problème
        pass


def problems() -> list[str]:
    out: list[str] = []
    left = sync._ai_pause["until"] - time.monotonic()
    if left > 0 and sync._ai_pause["fp"] == sync._ai_fingerprint():
        out.append(f"Analyse IA suspendue ({sync._ai_pause.get('why') or 'refus du fournisseur'}) — reprise dans {max(1, int(left // 60))} min. "
                   "Corrigez la clé ou les crédits dans Paramètres › IA.")
    cap = aipolicy.daily_cap_reached()
    if cap:
        out.append(f"Plafond quotidien de crédits 1min.AI atteint ({cap[0]:,} sur {cap[1]:,}) : l'IA est suspendue jusqu'à demain. ".replace(",", " ")
                   + "Relevez le plafond dans Paramètres › Utilisation et données.")
    for mid, st in sorted(modules.status.items()):
        if st and st != "ok":
            out.append(f"Module « {mid} » en erreur : {st}")
    out.extend(fleet.problems())
    old = chromebridge.extension_outdated()
    if old:
        out.append(old)
    return out
