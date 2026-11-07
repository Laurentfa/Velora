"""Exporter / importer la configuration de Velora d'une machine à l'autre (un fichier .zip).

Contenu : les réglages (liste fermée ci-dessous), les boîtes mail (sans mot de passe), les modules installés et leur configuration.
Jamais exporté : les clés d'API, les mots de passe, le trousseau, les mails, la base de données. Après l'import, il reste à saisir les mots de passe des boîtes
et la clé d'IA (Paramètres). L'import n'écrase pas ce qui existe déjà : un réglage déjà posé ici est conservé, une boîte déjà présente aussi, un module n'est
remplacé que par une version plus récente."""
import json
import re
import shutil
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

from . import config, db, modules
from .version import APP_VERSION

FORMAT = 1
SETTING_KEYS = {"ai_fallback", "ai_intents", "ai_budget_eur", "ai_budget_hard", "ai_exclude", "apple_writer", "brief_off", "cols", "default_account", "list_density",
                "folder_fold", "onemin_allowance", "onemin_daily_cap", "update_auto", "vip_senders", "safe_senders", "modules_on", "modules_enabled", "modules_repo"}
SETTING_PREFIXES = ("module:", "drafts_auto:", "folder_order:", "archive:")
SKIP_SUFFIX = (":src",)  # dossier d'origine d'un module sur l'autre machine : n'a aucun sens ici
SECRET = re.compile(r"(pass|pwd|secret|token|api_?key)", re.I)


def exportable(key: str) -> bool:
    if SECRET.search(key) or key.endswith(SKIP_SUFFIX):
        return False
    return key in SETTING_KEYS or key.startswith(SETTING_PREFIXES)


def _settings() -> dict[str, str]:
    with db.conn() as c:
        rows = c.execute("SELECT k, v FROM kv").fetchall()
    return {r["k"]: r["v"] for r in rows if exportable(r["k"]) and r["v"] is not None}


def export_bundle(dest: Path) -> dict:
    """Écrit l'archive ; rend un résumé {settings, accounts, modules}."""
    dest = Path(dest)
    settings = _settings()
    accounts = [{k: v for k, v in a.items() if not SECRET.search(k)} for a in config.read_accounts()]
    mods = []
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
        if modules.DIR.is_dir():
            for d in sorted(modules.DIR.iterdir()):
                if not (d.is_dir() and (d / "module.json").is_file()):
                    continue
                mods.append(d.name)
                for f in sorted(d.rglob("*")):
                    if f.is_file() and "__pycache__" not in f.parts and f.suffix != ".pyc":
                        z.write(f, f"modules/{d.name}/{f.relative_to(d).as_posix()}")
        z.writestr("velora-config.json", json.dumps({"format": FORMAT, "app": APP_VERSION, "from": config.machine_label(), "at": datetime.now().isoformat(timespec="seconds"),
                                                      "settings": settings, "accounts": accounts}, ensure_ascii=False, indent=1))
    return {"settings": len(settings), "accounts": len(accounts), "modules": len(mods)}


def import_bundle(src: Path) -> dict:
    """Applique l'archive en ne remplaçant rien d'existant. ValueError si ce n'est pas une configuration de Velora.
    Rend {settings, accounts, modules, updated} (nombres ajoutés ; `updated` : modules remplacés par une version plus récente)."""
    src = Path(src)
    tmp = Path(tempfile.mkdtemp(prefix="velora-config-"))
    try:
        try:
            modules._safe_extract(src, tmp)
            data = json.loads((tmp / "velora-config.json").read_text(encoding="utf-8"))
        except (zipfile.BadZipFile, FileNotFoundError, ValueError, OSError) as e:
            raise ValueError("Ce fichier n'est pas une configuration de Velora.") from e
        if not isinstance(data, dict) or data.get("format") != FORMAT:
            raise ValueError("Ce fichier n'est pas une configuration de Velora (ou vient d'une version trop récente).")
        n_set = 0
        for k, v in (data.get("settings") or {}).items():
            if isinstance(k, str) and isinstance(v, str) and exportable(k) and db.kv_get(k) in (None, ""):
                db.kv_set(k, v)
                n_set += 1
        have = {a["key"] for a in config.read_accounts()}
        added = [a for a in (data.get("accounts") or []) if isinstance(a, dict) and a.get("key") and a["key"] not in have]
        if added:
            config.write_accounts([*config.read_accounts(), *[{k: v for k, v in a.items() if not SECRET.search(k)} for a in added]])
        n_mod = n_upd = 0
        root = tmp / "modules"
        for d in sorted(root.iterdir()) if root.is_dir() else []:
            if not (d / "module.json").is_file():
                continue
            target = modules.DIR / re.sub(r"[^A-Za-z0-9_-]", "_", d.name)
            try:
                if not target.exists():
                    modules.import_module(d)
                    n_mod += 1
                elif _version(d) > _version(target):
                    modules.import_module(d, replace=True)
                    n_upd += 1
            except (ValueError, OSError) as e:
                raise ValueError(f"Module « {d.name} » : {e}") from e
        return {"settings": n_set, "accounts": len(added), "modules": n_mod, "updated": n_upd}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _version(folder: Path) -> tuple:
    try:
        return modules._vtuple(json.loads((folder / "module.json").read_text(encoding="utf-8")).get("version", "0"))
    except (OSError, ValueError):
        return (0,)
