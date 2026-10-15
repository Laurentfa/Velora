"""Extrait les textes d'interface (français) du code : littéraux et f-strings, hors docstrings, journaux, SQL, CSS/JS.
Usage : python tools/extract_strings.py > /tmp/strings.json   (liste de modèles : les {expr} deviennent {0}, {1}…)"""
import ast
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FILES = [ROOT / "main.py", *sorted((ROOT / "mailia").glob("*.py")), *sorted((ROOT / "mailia" / "providers").glob("*.py"))]
SKIP_FILES = {"apicore.py", "apiserver.py", "mcp.py", "mcpbridge.py", "logs.py"}  # réponses d'API et textes de diagnostic : ce n'est pas l'interface
LOG_ATTRS = {"debug", "info", "warning", "error", "exception", "critical", "log"}
SQL = re.compile(r"\b(SELECT|INSERT|UPDATE|DELETE|CREATE TABLE|ALTER TABLE|PRAGMA|FROM\s+\w+|WHERE)\b")
FRENCH = re.compile(r"[àâçéèêëîïôûùüÿœ]|\b(mails|lus|dernier|dernière|derniers|premiers|premier|jours|jour|heure|heures|fois|selon|tous|toutes|ou|le|la|les|un|une|des|du|de|et|ou|pour|avec|sans|sur|dans|par|vous|votre|vos|ce|cette|ces|est|sont|pas|plus|aucun|aucune|mail|message|boîte|dossier|envoyer|annuler|enregistrer|ajouter|choisir|fichier|nouveau|nouvelle|lu|non)\b", re.I)


def is_ui_text(s: str) -> bool:
    s = s.strip()
    if len(s) < 2 or len(s) > 600 or "\n" in s and len(s) > 300:
        return False
    if not re.search(r"[A-Za-zÀ-ÿ]{2,}", s):
        return False
    if SQL.search(s) or s.startswith(("http", "/", "<", ".", "#", "mailto")) or (s.startswith("{") and not re.match(r"\{\d+\}", s)) or re.fullmatch(r"[\w.\-/:@]+", s) and " " not in s and not FRENCH.search(s):
        return False
    if "function" in s or "=>" in s or "document." in s or "{{" in s or "px" in s and ":" in s and ";" in s:
        return False
    return bool(FRENCH.search(s))


def template(node: ast.JoinedStr) -> str | None:
    parts, n = [], 0
    for v in node.values:
        if isinstance(v, ast.Constant) and isinstance(v.value, str):
            parts.append(v.value.replace("{", "{{").replace("}", "}}"))
        elif isinstance(v, ast.FormattedValue):
            parts.append("{%d}" % n)
            n += 1
        else:
            return None
    return "".join(parts)


UI_FUNCS = {"label", "button", "tab", "menu_item", "notify", "tooltip", "input", "select", "textarea", "number", "switch", "checkbox", "badge",
            "expansion", "item_label", "chip", "step", "radio", "toggle", "html", "markdown", "icon_label"}


def ui_args(tree) -> set[int]:
    """Identifiants des littéraux passés directement à un composant d'interface (même courts, même sans accent)."""
    ids: set[int] = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in UI_FUNCS:
            for a in n.args[:2]:
                if isinstance(a, ast.Constant) and isinstance(a.value, str):
                    ids.add(id(a))
            for kw in n.keywords:
                if kw.arg in ("label", "placeholder", "text", "message", "caption") and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                    ids.add(id(kw.value))
    return ids


def variants(node, depth: int = 0) -> list[list] | None:
    """Gabarits possibles d'une expression de texte (concaténations, conditions) : liste de listes de morceaux (str ou None = valeur)."""
    if depth > 6:
        return None
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [[node.value]]
    if isinstance(node, ast.JoinedStr):
        parts = []
        for v in node.values:
            if isinstance(v, ast.Constant) and isinstance(v.value, str):
                parts.append(v.value)
            else:
                parts.append(None)
        return [parts]
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        a, b = variants(node.left, depth + 1), variants(node.right, depth + 1)
        if a is None:
            a = [[None]]
        if b is None:
            b = [[None]]
        out = [x + y for x in a for y in b]
        return out if len(out) <= 8 else None
    if isinstance(node, ast.IfExp):
        a, b = variants(node.body, depth + 1) or [[None]], variants(node.orelse, depth + 1) or [[""]]
        return (a + b)[:8]
    return None


def render_variant(parts: list) -> str | None:
    out, n = [], 0
    for p in parts:
        if p is None:
            out.append("{%d}" % n)
            n += 1
        else:
            out.append(p.replace("{", "{{").replace("}", "}}"))
    text = "".join(out)
    return text if n and re.search(r"[A-Za-zÀ-ÿ]{3,}", re.sub(r"\{\d+\}", "", text)) or (not n and text) else None


def main() -> None:
    found: dict[str, set] = {}
    for f in FILES:
        if f.name in SKIP_FILES:
            continue
        tree = ast.parse(f.read_text(encoding="utf-8"))
        doc_nodes = set()
        for n in ast.walk(tree):
            if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n.body and isinstance(n.body[0], ast.Expr) \
                    and isinstance(n.body[0].value, ast.Constant):
                doc_nodes.add(id(n.body[0].value))
        skip_ids: set[int] = set()
        for n in ast.walk(tree):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in LOG_ATTRS:
                for sub in ast.walk(n):
                    skip_ids.add(id(sub))
            if isinstance(n, ast.Call) and getattr(n.func, "id", "") in ("repr", "getLogger", "print"):
                for sub in ast.walk(n):
                    skip_ids.add(id(sub))
        direct = ui_args(tree)
        joined_children = set()
        for n in ast.walk(tree):
            if isinstance(n, ast.JoinedStr):
                for c in n.values:
                    joined_children.add(id(c))
        # concaténations « a + b » et conditions : le texte affiché est la somme des morceaux
        add_children: set[int] = set()
        for n in ast.walk(tree):
            if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Add) and id(n) not in add_children and id(n) not in skip_ids:
                for sub in ast.walk(n):
                    if sub is not n and isinstance(sub, ast.BinOp):
                        add_children.add(id(sub))
                vs = variants(n)
                if vs:
                    for parts in vs:
                        t = render_variant(parts)
                        if t and is_ui_text(t) and sum(1 for p in parts if p is not None and p.strip()) >= 2:
                            found.setdefault(t.strip(), set()).add(f.name)
        for n in ast.walk(tree):
            if id(n) in doc_nodes or id(n) in skip_ids or id(n) in joined_children:
                continue
            text = None
            if isinstance(n, ast.Constant) and isinstance(n.value, str):
                text = n.value
            elif isinstance(n, ast.JoinedStr):
                text = template(n)
            ok = bool(text) and (is_ui_text(text) or (id(n) in direct and len(text.strip()) >= 3 and re.search(r"[A-Za-zÀ-ÿ]{3,}", text)
                                                        and not text.strip().startswith(("http", "/", "<", ".", "#")) and not SQL.search(text)))
            if text and ok:
                found.setdefault(text.strip(), set()).add(f.name)
    sys.stdout.reconfigure(encoding="utf-8")  # Windows : la console utilise sinon un encodage qui ne connaît pas les accents
    json.dump(sorted(found), sys.stdout, ensure_ascii=False, indent=0)


if __name__ == "__main__":
    main()
