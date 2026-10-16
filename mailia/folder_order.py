"""Ordre des dossiers dans la colonne de gauche, choisi par glisser-déposer.

L'ordre est une préférence d'affichage, propre à chaque boîte (réglage « folder_order:<boîte> » : la liste des clés de dossiers dans l'ordre voulu) ;
rien n'est modifié sur le serveur. On ne réordonne que des dossiers du même niveau (même dossier parent) : un dossier reste sous son parent.
Les dossiers sans position choisie (nouveaux) viennent après les autres de leur niveau, par ordre alphabétique."""
import json

from . import db

PREFIX = "folder_order:"
UNRANKED = 10_000


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


def parent_of(path: str) -> str:
    return path.rsplit("/", 1)[0] if "/" in path else ""


def sort_folders(rows: list[dict], order: list[str]) -> list[dict]:
    """Les dossiers dans l'ordre d'affichage : en profondeur d'abord, chaque niveau selon l'ordre choisi puis l'alphabet."""
    pos = {k: i for i, k in enumerate(order)}
    by_path = {f["path"]: f for f in rows}

    def rank(path: str) -> tuple:
        f = by_path.get(path)
        return (pos.get(f["key"], UNRANKED) if f else UNRANKED, path.rsplit("/", 1)[-1].lower())

    def key(f: dict) -> list:
        parts = f["path"].split("/")
        return [rank("/".join(parts[:i + 1])) for i in range(len(parts))]

    return sorted(rows, key=key)


def move(rows: list[dict], order: list[str], dragged: str, target: str, after: bool) -> list[str] | None:
    """Nouvel ordre quand `dragged` est déposé avant ou après `target` ; None si ce n'est pas possible (autre niveau, dossier inconnu, même dossier)."""
    by_key = {f["key"]: f for f in rows}
    if dragged == target or dragged not in by_key or target not in by_key:
        return None
    parent = parent_of(by_key[dragged]["path"])
    if parent_of(by_key[target]["path"]) != parent:
        return None
    siblings = [f["key"] for f in sort_folders([f for f in rows if parent_of(f["path"]) == parent], order)]
    siblings.remove(dragged)
    siblings.insert(siblings.index(target) + (1 if after else 0), dragged)
    return [k for k in order if k not in siblings] + siblings
