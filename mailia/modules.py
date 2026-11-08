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
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

from mailia import config, db

log = logging.getLogger("mailia")

API_VERSION = 1
DIR = config.DATA_DIR / "modules"
views: dict[str, dict] = {}   # identifiant de vue -> {"title", "icon", "build", "module"}
services: dict[str, dict] = {}  # services optionnels publiés par les modules : nom -> {"module", "fn"} (un module peut s'en servir s'il existe, et fonctionne sans sinon)
slots: dict[str, list] = {"reader": []}  # emplacements dans l'interface : "reader" = sous les réponses rapides d'un message
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


_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
MAX_UNZIPPED = 50 * 1024 * 1024  # un module est un peu de code : au-delà, ce n'est pas un module


def _root_of(folder: Path) -> Path:
    """Le dossier qui contient module.json : la racine, ou l'unique sous-dossier (archive faite depuis le dossier du module)."""
    if (folder / "module.json").is_file():
        return folder
    subs = [d for d in folder.iterdir() if d.is_dir() and not d.name.startswith((".", "__"))]
    if len(subs) == 1 and (subs[0] / "module.json").is_file():
        return subs[0]
    raise ValueError("Ce n'est pas un module : il manque le fichier module.json.")


def _safe_extract(archive: Path, dest: Path) -> None:
    with zipfile.ZipFile(archive) as z:
        if sum(i.file_size for i in z.infolist()) > MAX_UNZIPPED:
            raise ValueError("Archive trop volumineuse pour un module.")
        for i in z.infolist():
            if i.filename.startswith(("/", "\\")) or ".." in Path(i.filename).parts or ":" in i.filename:
                raise ValueError("Archive refusée : chemins inattendus.")
        z.extractall(dest)


def _vtuple(v: str) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", v or ""))


def update_from_repo(repo: str) -> list[tuple[str, str, str]]:
    """Met à jour, depuis un dépôt GitHub (même privé), les modules DÉJÀ installés dont la version du dépôt est plus récente.
    Passe par l'outil « gh » de l'ordinateur (connecté à votre compte GitHub) : Velora ne garde aucun jeton. Rend [(identifiant, ancienne, nouvelle version)].
    Les modules ne sont pas rechargés ici (voir apply). ValueError avec un message lisible si « gh » manque ou si le dépôt est inaccessible."""
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", repo or ""):
        raise ValueError("Indiquez le dépôt sous la forme « compte/depot ».")
    gh = shutil.which("gh")
    if not gh:
        raise ValueError("L'outil GitHub « gh » n'est pas installé sur cet ordinateur : importez plutôt le dossier du module ou une archive .zip.")
    tmp = Path(tempfile.mkdtemp(prefix="velora-modules-"))
    try:
        try:
            r = subprocess.run([gh, "api", f"repos/{repo}/zipball"], capture_output=True, timeout=120)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise ValueError(f"Dépôt injoignable : {e}") from None
        if r.returncode or not r.stdout.startswith(b"PK"):
            raise ValueError("Dépôt inaccessible (êtes-vous connecté à GitHub avec « gh auth login » ?) : " + r.stderr.decode("utf-8", "replace").strip()[:200])
        archive = tmp / "repo.zip"
        archive.write_bytes(r.stdout)
        _safe_extract(archive, tmp / "x")
        tops = [d for d in (tmp / "x").iterdir() if d.is_dir()]
        root = tops[0] if len(tops) == 1 else tmp / "x"  # GitHub range tout dans un dossier « compte-depot-<commit> »
        installed = {i["id"]: i for i in discover() if not i.get("error")}
        done = []
        for d in sorted(p for p in root.iterdir() if p.is_dir() and p.name in installed and (p / "module.py").is_file()):
            try:
                new = json.loads((d / "module.json").read_text(encoding="utf-8")).get("version", "")
            except (OSError, ValueError):
                continue
            old = installed[d.name]["version"]
            if _vtuple(new) > _vtuple(old):
                import_module(d, True)
                done.append((d.name, old, new))
        return done
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def import_bundle(src, replace: bool = False) -> list[str]:
    """Comme import_module, mais accepte aussi une archive ou un dossier qui contient PLUSIEURS modules (un sous-dossier par module).
    Rend les identifiants importés. FileExistsError(noms séparés par des virgules) si certains existent déjà et replace est faux : rien n'est alors copié."""
    src = Path(src)
    try:
        return [import_module(src, replace)]
    except ValueError as first:
        if "module.json" not in str(first):
            raise
    tmp = None
    try:
        if src.is_file():
            tmp = Path(tempfile.mkdtemp(prefix="velora-modules-"))
            _safe_extract(src, tmp)
            base = tmp
        else:
            base = src
        subs = [d for d in base.iterdir() if d.is_dir() and not d.name.startswith((".", "__"))]
        if len(subs) == 1 and not (subs[0] / "module.json").is_file():
            subs = [d for d in subs[0].iterdir() if d.is_dir() and not d.name.startswith((".", "__"))]  # archive faite depuis un dossier parent
        found = [d for d in subs if (d / "module.json").is_file() and (d / "module.py").is_file()]
        if not found:
            raise ValueError("Ce n'est pas un module : il manque le fichier module.json.")
        existing = [d.name for d in found if (DIR / re.sub(r"[^A-Za-z0-9_-]", "_", d.name)).exists()]
        if existing and not replace:
            raise FileExistsError(", ".join(existing))
        return [import_module(d, replace) for d in found]
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)


def import_module(src, replace: bool = False) -> str:
    """Copie un module (dossier, ou archive .zip) dans le dossier des modules et rend son identifiant.
    FileExistsError s'il y en a déjà un du même nom (replace=True le remplace) ; ValueError si ce n'est pas un module. Le module n'est pas activé."""
    src = Path(src)
    tmp = None
    try:
        name = src.stem if src.is_file() else src.name
        if src.is_file():
            if src.suffix.lower() != ".zip":
                raise ValueError("Choisissez le dossier du module, ou une archive .zip.")
            tmp = Path(tempfile.mkdtemp(prefix="velora-module-"))
            _safe_extract(src, tmp)
            root = _root_of(tmp)
            name = root.name if root != tmp else name
        else:
            root = _root_of(src)
            name = root.name
        if not (root / "module.py").is_file():
            raise ValueError("Ce n'est pas un module : il manque le fichier module.py.")
        mid = re.sub(r"[^A-Za-z0-9_-]", "_", name).strip("_-") or "module"
        if not _ID.match(mid):
            raise ValueError("Nom de module invalide.")
        DIR.mkdir(parents=True, exist_ok=True)
        dest = DIR / mid
        if dest.exists():
            if root.resolve() == dest.resolve():
                raise ValueError("Ce dossier est déjà le module installé : choisissez le dossier d'origine.")  # sinon le remplacement effacerait sa propre source
            if not replace:
                raise FileExistsError(mid)
            shutil.rmtree(dest)
        shutil.copytree(root, dest, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache", ".DS_Store"))
        return mid
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)


def remove_module(mid: str) -> None:
    """Supprime le dossier d'un module et ses réglages (sans retour possible). Pour qu'il disparaisse tout de suite, appeler apply(mid)."""
    if not _ID.match(mid or ""):
        raise ValueError("Nom de module invalide.")
    set_enabled(mid, False)
    shutil.rmtree(DIR / mid, ignore_errors=True)
    db.kv_set(f"module:{mid}:cfg", "")
    db.kv_set(f"module:{mid}:src", "")
    status.pop(mid, None)


def discover() -> list[dict]:
    """Les modules présents dans le dossier : {id, name, version, description, api, error?}."""
    out = []
    try:
        DIR.mkdir(parents=True, exist_ok=True)  # le dossier existe toujours : l'utilisateur n'a rien à créer à la main
    except OSError:
        pass
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

    def add_reader_widget(self, build, order: int = 100) -> None:
        """Ajoute un outil sous les réponses rapides d'un message. build(api, mailbox, message) le dessine avec NiceGUI (message : le message affiché, un dict).
        order : position parmi les outils des modules (les plus petits d'abord, à égalité par nom de module)."""
        slots["reader"].append({"module": self.id, "build": build, "order": order})

    def provide(self, name: str, fn) -> None:
        """Publie un service optionnel que d'autres modules peuvent utiliser (voir `service`). fn(*args) est bloquant : à appeler avec run_blocking."""
        services[name] = {"module": self.id, "fn": fn}

    def service(self, name: str):
        """Le service publié sous ce nom par un autre module, ou None s'il n'existe pas (module absent, éteint ou en panne) : un module doit alors fonctionner sans lui."""
        it = services.get(name)
        return it["fn"] if it and it["module"] != self.id else None

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

    async def ask_ai(self, system: str, user: str, max_tokens: int = 400) -> str:
        """Pose une question à l'IA de Velora : fournisseur, plafond de dépense et compteur de consommation sont ceux de Velora (la consommation apparaît
        sous « Module : <identifiant> » dans Paramètres › Utilisation). Lève ai.AIError si l'IA est coupée ou si le plafond est atteint.
        Ce que le module envoie quitte l'ordinateur : à lui de n'envoyer que le nécessaire (jamais d'identité de patient, par exemple)."""
        from nicegui import run

        from mailia import ai
        return await run.io_bound(lambda: ai._ask(system, user, model=config.model("ANALYZE"), max_tokens=max_tokens, feature=f"module:{self.id}"))

    async def run_blocking(self, fn, *args):
        """Exécute fn(*args) hors du fil de la fenêtre (réseau, programmes externes) : l'interface ne se fige pas."""
        from nicegui import run
        return await run.io_bound(fn, *args)


on_change = lambda: None  # posé par l'interface : redessine la barre latérale et le message affiché quand un module apparaît ou disparaît


def unload(mid: str) -> None:
    """Retire de Velora tout ce qu'un module avait ajouté (vues, outils sous les messages, services). Ses tâches de fond éventuelles tournent jusqu'au prochain lancement."""
    from mailia.ui_common import NOLIST_VIEWS, VIEWS
    for vid in [v for v, d in views.items() if d["module"] == mid]:
        views.pop(vid, None)
        VIEWS.pop(vid, None)
        NOLIST_VIEWS.discard(vid)
    for lst in slots.values():
        lst[:] = [x for x in lst if x["module"] != mid]
    for name in [n for n, it in services.items() if it["module"] == mid]:
        services.pop(name, None)
    status.pop(mid, None)
    from mailia import updater
    updater.hooks.pop(mid, None)  # une source de mise à jour ajoutée par ce module


def _load(info: dict) -> None:
    mid = info["id"]
    if info.get("error"):
        status[mid] = info["error"]
        return
    if int(info.get("api") or 1) > API_VERSION:
        status[mid] = "demande une version plus récente de Velora"
        return
    try:
        spec = importlib.util.spec_from_file_location(f"velora_module_{mid}", info["dir"] / "module.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod.register(ModuleAPI(info))
        status[mid] = "ok"
    except Exception as e:  # noqa: BLE001 — un module ne doit jamais empêcher Velora de démarrer
        log.exception("module %s : échec au chargement", mid)
        unload(mid)  # ses vues et outils à moitié ajoutés sont retirés
        status[mid] = f"{type(e).__name__} : {str(e)[:160]}"


def load_all() -> None:
    """Charge les modules activés (au démarrage, ou quand l'interrupteur général change)."""
    for mid in list(status) + [d["module"] for d in views.values()]:
        unload(mid)
    views.clear()
    status.clear()
    services.clear()
    for lst in slots.values():
        lst.clear()
    if not master_on():
        return
    on = enabled_ids()
    for info in discover():
        if info["id"] in on:
            _load(info)


def apply(mid: str | None = None) -> None:
    """Applique tout de suite un changement (activation, suppression, import) sans redémarrer : un module, ou tous si mid est None."""
    if mid is None:
        load_all()
    else:
        unload(mid)
        if master_on() and mid in enabled_ids():
            info = next((i for i in discover() if i["id"] == mid), None)
            if info:
                _load(info)
    try:
        on_change()
    except Exception:  # noqa: BLE001 — l'interface n'est peut-être pas affichée
        log.debug("rafraîchissement après changement de module impossible", exc_info=True)


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


def render_slot(name: str, mailbox, message: dict) -> None:
    """Dessine les outils des modules dans un emplacement ; l'erreur d'un module reste dans son coin (journal + petite ligne grise)."""
    from nicegui import ui
    for it in sorted(slots.get(name, []), key=lambda x: (x.get("order", 100), x["module"])):
        api = ModuleAPI({"id": it["module"], "dir": DIR / it["module"], "name": it["module"]})
        try:
            it["build"](api, mailbox, message)
        except Exception as e:  # noqa: BLE001
            log.exception("module %s : échec d'affichage", it["module"])
            ui.label(f"Module « {it['module']} » en erreur : {type(e).__name__}").classes("rmeta")
