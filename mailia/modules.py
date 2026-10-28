"""Modules : des fonctionnalités personnelles ajoutées à Velora sans faire partie de l'application.

Un module est un dossier dans « <dossier de données>/modules/ » :
    mon_module/module.json    {"name": "Mon module", "version": "1.0", "api": 1, "description": "…"}
    mon_module/module.py      def register(api): …   (api : voir ModuleAPI)

Rien n'est chargé tant que les modules ne sont pas activés dans les Paramètres, puis module par module. Le code d'un module s'exécute avec vos droits :
il n'est jamais téléchargé ni chargé depuis un mail ou un site, seulement depuis ce dossier. Un module qui échoue est noté dans le journal et
n'empêche jamais l'application de démarrer. Ce dossier ne fait pas partie de ce qui est distribué."""
import importlib.util
import json
import logging
import re

from mailia import config, db

log = logging.getLogger("mailia")

API_VERSION = 1
DIR = config.DATA_DIR / "modules"
views: dict[str, dict] = {}   # identifiant de vue -> {"title", "icon", "build", "module"}
status: dict[str, str] = {}   # identifiant de module -> "ok" ou message d'erreur


def master_on() -> bool:
    return db.kv_get("modules_on") == "1"


def enabled_ids() -> set[str]:
    try:
        return set(json.loads(db.kv_get("modules_enabled") or "[]"))
    except ValueError:
        return set()


def set_enabled(mid: str, on: bool) -> None:
    ids = enabled_ids()
    ids.add(mid) if on else ids.discard(mid)
    db.kv_set("modules_enabled", json.dumps(sorted(ids)))


def discover() -> list[dict]:
    """Les modules présents dans le dossier : {id, name, version, description, api, error?}."""
    out = []
    if not DIR.is_dir():
        return out
    for d in sorted(p for p in DIR.iterdir() if p.is_dir() and not p.name.startswith((".", "_"))):
        info = {"id": d.name, "name": d.name, "version": "", "description": "", "api": 1, "dir": d}
        try:
            info.update({k: v for k, v in json.loads((d / "module.json").read_text(encoding="utf-8")).items() if k in ("name", "version", "description", "api")})
        except (OSError, ValueError):
            info["error"] = "module.json absent ou illisible"
        if not (d / "module.py").is_file():
            info["error"] = info.get("error") or "module.py absent"
        out.append(info)
    return out


class ModuleAPI:
    """Ce qu'un module peut faire (version 1) : ajouter une vue à la barre latérale, garder ses réglages, travailler hors du fil de la fenêtre."""

    def __init__(self, info: dict):
        self.id, self.dir, self.name = info["id"], info["dir"], info["name"]
        self.log = logging.getLogger(f"mailia.module.{self.id}")

    def add_view(self, view_id: str, title: str, icon: str, build) -> str:
        """Ajoute une vue (entrée de la barre latérale). build(api) dessine son contenu avec NiceGUI ; retourne l'identifiant complet de la vue."""
        from mailia.ui_common import NOLIST_VIEWS, VIEWS
        vid = f"mod_{self.id}_{re.sub(r'[^a-z0-9_]', '_', view_id.lower())}"
        views[vid] = {"title": title, "icon": icon, "build": build, "module": self.id}
        VIEWS[vid] = (icon, title)
        NOLIST_VIEWS.add(vid)
        return vid

    def get(self, key: str, default=None):
        """Réglage propre au module (gardé dans la base de Velora)."""
        try:
            return json.loads(db.kv_get(f"module:{self.id}:cfg") or "{}").get(key, default)
        except ValueError:
            return default

    def set(self, key: str, value) -> None:
        try:
            cfg = json.loads(db.kv_get(f"module:{self.id}:cfg") or "{}")
        except ValueError:
            cfg = {}
        cfg[key] = value
        db.kv_set(f"module:{self.id}:cfg", json.dumps(cfg))

    async def run_blocking(self, fn, *args):
        """Exécute fn(*args) hors du fil de la fenêtre (réseau, programmes externes) : l'interface ne se fige pas."""
        from nicegui import run
        return await run.io_bound(fn, *args)


def load_all() -> None:
    """Charge les modules activés. Appelé une fois au démarrage ; un changement dans les Paramètres s'applique au redémarrage."""
    views.clear()
    status.clear()
    if not master_on():
        return
    on = enabled_ids()
    for info in discover():
        mid = info["id"]
        if mid not in on:
            continue
        if info.get("error"):
            status[mid] = info["error"]
            continue
        if int(info.get("api") or 1) > API_VERSION:
            status[mid] = "demande une version plus récente de Velora"
            continue
        try:
            spec = importlib.util.spec_from_file_location(f"velora_module_{mid}", info["dir"] / "module.py")
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            mod.register(ModuleAPI(info))
            status[mid] = "ok"
        except Exception as e:  # noqa: BLE001 — un module ne doit jamais empêcher Velora de démarrer
            log.exception("module %s : échec au chargement", mid)
            status[mid] = f"{type(e).__name__} : {str(e)[:160]}"
            for vid in [v for v, d in views.items() if d["module"] == mid]:  # ses vues à moitié ajoutées sont retirées
                views.pop(vid, None)


def render(view_id: str) -> None:
    """Dessine la vue d'un module ; une erreur du module s'affiche à sa place, sans toucher au reste."""
    from nicegui import ui
    v = views.get(view_id)
    if not v:
        return
    api = ModuleAPI({"id": v["module"], "dir": DIR / v["module"], "name": v["title"]})
    try:
        v["build"](api)
    except Exception as e:  # noqa: BLE001
        log.exception("module %s : échec d'affichage", v["module"])
        ui.label(f"Ce module a rencontré une erreur : {type(e).__name__} : {str(e)[:200]}").classes("text-negative").style("padding:12px")
