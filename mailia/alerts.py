"""Alertes qui restent affichées jusqu'à ce que l'utilisateur clique sur OK (par exemple : 1min.AI refuse la connexion).
Une alerte n'est montrée qu'une fois : après le clic sur OK elle ne revient que si le problème a disparu (resolve) puis reparaît."""
import threading

_lock = threading.Lock()
_pending: dict[str, dict] = {}   # clé -> {"key", "title", "text"}, dans l'ordre d'arrivée
_acked: set[str] = set()


def raise_alert(key: str, title: str, text: str) -> None:
    """Signale un problème ; sans effet s'il est déjà signalé ou si l'utilisateur a déjà cliqué sur OK."""
    with _lock:
        if key not in _pending and key not in _acked:
            _pending[key] = {"key": key, "title": title, "text": text}


def pending() -> list[dict]:
    with _lock:
        return list(_pending.values())


def acknowledge(key: str) -> None:
    """L'utilisateur a cliqué sur OK."""
    with _lock:
        _pending.pop(key, None)
        _acked.add(key)


def resolve(prefix: str) -> None:
    """Le problème n'existe plus (la connexion fonctionne de nouveau) : les alertes de ce groupe peuvent reparaître s'il revient."""
    with _lock:
        for k in [k for k in list(_pending) + list(_acked) if k.startswith(prefix)]:
            _pending.pop(k, None)
            _acked.discard(k)
