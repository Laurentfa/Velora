"""Ordre des dossiers dans la colonne de gauche, choisi par glisser-déposer.

L'ordre est une préférence d'affichage, propre à chaque boîte (réglage « folder_order:<boîte> » : la liste des clés de dossiers dans l'ordre voulu) ;
rien n'est modifié sur le serveur. On ne réordonne que des dossiers du même niveau (même dossier parent) : un dossier reste sous son parent.
Les dossiers sans position choisie (nouveaux) viennent après les autres de leur niveau (voir folder_tree)."""
import json

from . import db, folder_tree

PREFIX = "folder_order:"


def load(account: str) -> list[str]:
    try:
        order = json.loads(db.kv_get(PREFIX + account) or "[]")
        return [k for k in order if isinstance(k, str)]
    except ValueError:
        return []


def save(account: str, order: list[str]) -> None:
    db.kv_set(PREFIX + account, json.dumps(order, ensure_ascii=False))


def reset_all() -> None:
    """Retour à l'ordre alphabétique pour toutes les boîtes."""
    with db.conn() as c:
        c.execute("DELETE FROM kv WHERE k LIKE ?", (PREFIX + "%",))


def move(account_folders: list[dict], order: list[str], dragged: str, target: str, after: bool) -> list[str] | None:
    """Nouvel ordre quand `dragged` est déposé avant ou après `target` ; None si ce n'est pas possible (autre niveau, dossier inconnu, même dossier,
    boîte de réception, ligne fantôme)."""
    sib = folder_tree.sibling_keys(account_folders, order, dragged)
    if dragged == target or dragged not in sib or target not in sib:
        return None
    sib.remove(dragged)
    sib.insert(sib.index(target) + (1 if after else 0), dragged)
    return [k for k in order if k not in sib] + sib
