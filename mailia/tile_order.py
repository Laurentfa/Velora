"""Ordre des tuiles des modules en haut de la colonne de gauche, choisi par glisser-déposer.

C'est une préférence d'affichage (réglage « tile_order » : la liste des identifiants de vues dans l'ordre voulu). Les modules sans position choisie (nouveaux) viennent après les autres,
dans l'ordre où ils se sont chargés ; un module retiré ou désactivé disparaît simplement de l'affichage (sa place est gardée dans la liste)."""
import json

from . import db

KEY = "tile_order"


def load() -> list[str]:
    try:
        order = json.loads(db.kv_get(KEY) or "[]")
        return [k for k in order if isinstance(k, str)]
    except ValueError:
        return []


def save(order: list[str]) -> None:
    db.kv_set(KEY, json.dumps(order, ensure_ascii=False))


def reset() -> None:
    with db.conn() as c:
        c.execute("DELETE FROM kv WHERE k=?", (KEY,))


def arrange(view_ids: list[str], order: list[str]) -> list[str]:
    """Les identifiants affichés, dans l'ordre choisi ; ceux sans position choisie à la suite, dans leur ordre d'origine."""
    present = set(view_ids)
    chosen = [k for k in dict.fromkeys(order) if k in present]
    return chosen + [k for k in view_ids if k not in set(chosen)]


def move(view_ids: list[str], order: list[str], dragged: str, target: str, after: bool) -> list[str] | None:
    """Nouvel ordre enregistré quand la tuile `dragged` est déposée avant ou après `target` ; None si ce n'est pas possible (tuile inconnue, ou la même)."""
    shown = arrange(view_ids, order)
    if dragged == target or dragged not in shown or target not in shown:
        return None
    shown.remove(dragged)
    shown.insert(shown.index(target) + (1 if after else 0), dragged)
    return shown + [k for k in order if k not in set(shown)]  # les places des modules absents pour l'instant sont gardées
