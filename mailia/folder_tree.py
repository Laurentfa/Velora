"""Arbre des dossiers d'une boîte pour la colonne de gauche : la même hiérarchie que sur le serveur.

    Boîte de réception         ← le dossier INBOX, avec ses sous-dossiers en dessous
        Administratif
        Juridique
    Brouillons, Envoyés, Archive (avec 2022, 2023…), Indésirables, Corbeille, puis les autres dossiers

Seuls les dossiers choisis (« synchroniser ») sont montrés. Si un dossier montré est dans un dossier qui ne l'est pas, ce parent apparaît quand
même, en grisé (« fantôme »), pour que la hiérarchie reste lisible. Au même niveau, les dossiers suivent l'ordre choisi par glisser-déposer
(voir folder_order), puis : boîte de réception, brouillons, envoyés, archive, indésirables, corbeille, puis ordre alphabétique."""
from __future__ import annotations

UNRANKED = 10_000
ROLE_RANK = {"inbox": 0, "drafts": 1, "sent": 2, "archive": 3, "junk": 4, "trash": 5}
GHOST = "ghost:"


def norm_path(path: str, inbox_path: str) -> str:
    """IMAP range les sous-dossiers de la boîte de réception sous « INBOX/ » alors que la boîte s'affiche « Boîte de réception » : on les rapproche."""
    if path.startswith("INBOX/") and inbox_path != "INBOX":
        return inbox_path + path[len("INBOX"):]
    return path


def parent_of(npath: str) -> str:
    return npath.rsplit("/", 1)[0] if "/" in npath else ""


def _sort_key(node: dict, pos: dict[str, int]) -> tuple:
    if node["key"] == "INBOX":
        return (-1, 0, 0, "")
    return (pos.get(node["key"], UNRANKED), ROLE_RANK.get(node["role"], 6), 0, node["name"].lower())


def _tree(account_folders: list[dict], order: list[str]) -> tuple[dict[str, dict], dict[str, list[dict]]]:
    inbox = next((f for f in account_folders if f["key"] == "INBOX"), None)
    inbox_path = inbox["path"] if inbox else "Boîte de réception"
    everything = {norm_path(f["path"], inbox_path): f for f in account_folders}  # y compris les dossiers non suivis (pour nommer les parents)
    nodes: dict[str, dict] = {}
    for f in account_folders:
        if f["sync"] or f["key"] == "INBOX":
            p = norm_path(f["path"], inbox_path)
            nodes[p] = {"key": f["key"], "name": f["name"], "path": p, "parent": parent_of(p), "role": f.get("role") or "", "ghost": False, "folder": f}
    for p in list(nodes):  # les parents manquants deviennent des lignes fantômes
        parent = parent_of(p)
        while parent and parent not in nodes:
            real = everything.get(parent)
            nodes[parent] = {"key": GHOST + parent, "name": real["name"] if real else parent.rsplit("/", 1)[-1], "path": parent, "parent": parent_of(parent),
                             "role": "", "ghost": True, "folder": None}
            parent = parent_of(parent)
    pos = {k: i for i, k in enumerate(order)}
    children: dict[str, list[dict]] = {}
    for n in nodes.values():
        children.setdefault(n["parent"], []).append(n)
    for lst in children.values():
        lst.sort(key=lambda n: _sort_key(n, pos))
    return nodes, children


def build(account_folders: list[dict], order: list[str], folded: set[str], selected: str = "", account: str = "") -> list[dict]:
    """Les lignes à afficher, dans l'ordre : {key, name, path, parent, role, ghost, folder, depth, kids, open, below}.
    `folded` : « boîte|clé » des dossiers repliés ; `selected` : clé du dossier ouvert (il reste visible même sous un parent replié) ;
    `below` : les clés des dossiers réels en dessous (pour additionner les non lus d'un dossier replié)."""
    nodes, children = _tree(account_folders, order)
    out: list[dict] = []

    def below(n: dict) -> list[str]:
        keys: list[str] = []
        for c in children.get(n["path"], []):
            keys += ([] if c["ghost"] else [c["key"]]) + below(c)
        return keys

    def walk(parent: str, depth: int, hidden: bool) -> None:
        for n in children.get(parent, []):
            kids = bool(children.get(n["path"]))
            is_open = not kids or f"{account}|{n['key']}" not in folded
            row = {**n, "depth": depth, "kids": kids, "open": is_open, "below": below(n)}
            if not hidden or n["key"] == selected:  # sous un parent replié, seul le dossier ouvert reste visible
                out.append(row)
            walk(n["path"], depth + 1, hidden or not is_open)

    walk("", 0, False)
    return out


def sibling_keys(account_folders: list[dict], order: list[str], key: str) -> list[str]:
    """Les dossiers réels de même niveau que `key`, dans l'ordre d'affichage (c'est parmi eux qu'on peut réordonner)."""
    nodes, children = _tree(account_folders, order)
    me = next((n for n in nodes.values() if n["key"] == key), None)
    if me is None or me["ghost"] or me["key"] == "INBOX":
        return []
    return [n["key"] for n in children.get(me["parent"], []) if not n["ghost"] and n["key"] != "INBOX"]
