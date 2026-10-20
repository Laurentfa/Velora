"""Chat de compagnie : une silhouette noire qui se promène en bas de la fenêtre (pet/pet.js). Désactivé par défaut ; réglages dans Paramètres › Affichage."""
import json
from pathlib import Path

from nicegui import ui

from mailia import db

DIR = Path(__file__).parent / "pet"
MOODS = {"calm": "Calme (il dort et se repose beaucoup)", "normal": "Normal", "lively": "Vif (il se promène davantage)"}


def version() -> int:
    """Empreinte du script (date de modification) : change à chaque mise à jour, pour que la fenêtre ne garde pas l'ancienne version en cache."""
    try:
        return int((DIR / "pet.js").stat().st_mtime)
    except OSError:
        return 0


def config() -> dict:
    """Réglages du chat tels que le script de la page les attend."""
    try:
        size = max(30, min(90, int(db.kv_get("pet_size") or 50)))
    except ValueError:
        size = 50
    mood = db.kv_get("pet_mood") or "normal"
    return {"on": (db.kv_get("pet_on") or "0") == "1", "size": size / 100, "follow": (db.kv_get("pet_follow") or "0") == "1",
            "mood": mood if mood in MOODS else "normal"}


def apply() -> None:
    """Envoie les réglages à la page ouverte (le chat apparaît, disparaît ou change tout de suite)."""
    ui.run_javascript("window.veloraPet && window.veloraPet.set(%s)" % json.dumps(config()))


def pet_settings() -> None:
    """Bloc « Chat de compagnie » des Paramètres."""
    ui.label("Chat de compagnie").classes("text-subtitle2 q-mt-md")

    def save(key: str, value) -> None:
        db.kv_set(key, str(value))
        apply()

    c = config()
    ui.switch("Afficher un chat qui se promène dans la fenêtre", value=c["on"], on_change=lambda e: save("pet_on", "1" if e.value else "0")).props("dense").mark("pet-switch")
    ui.slider(min=30, max=90, step=5, value=int(c["size"] * 100), on_change=lambda e: save("pet_size", int(e.value))).props("label dense").classes("w-full").mark("pet-size")
    ui.label("Taille du chat (30 à 90 %).").classes("text-caption text-grey")
    ui.select(MOODS, value=c["mood"], label="Humeur", on_change=lambda e: save("pet_mood", e.value)).props("outlined dense stack-label").classes("w-full").mark("pet-mood")
    ui.switch("Le chat suit le curseur", value=c["follow"], on_change=lambda e: save("pet_follow", "1" if e.value else "0")).props("dense").mark("pet-follow")
    ui.label("Purement décoratif : il ne gêne pas les clics (sauf sur lui-même), s'arrête quand la fenêtre est cachée et reste immobile si votre système demande de réduire les animations. "
             "Cliquez dessus pour le caresser.").classes("text-caption text-grey")
