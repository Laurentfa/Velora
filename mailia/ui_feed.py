"""Flux RSS sous l'agenda du brief (voir feed.py) et son réglage dans les Paramètres."""
import html
import webbrowser
from datetime import datetime

from nicegui import background_tasks, run, ui

from mailia import db, feed
from mailia.ui_common import SERVER_MODE


def stamp(when: datetime | None, now: datetime) -> str:
    if not when:
        return ""
    return f"{when:%H:%M}" if when.date() == now.date() else f"{when:%d/%m}"


class FeedMixin:
    def feed_panel(self) -> None:
        """Sous l'agenda : les titres du flux, remplis en arrière-plan ; absent si désactivé ou si le flux ne répond pas."""
        if not feed.enabled():
            return
        slot = ui.column().classes("feed gap-0")
        background_tasks.create(self._feed_fill(slot))
        ui.timer(900, lambda: background_tasks.create(self._feed_fill(slot)))

    async def _feed_fill(self, slot) -> None:
        try:
            data = await run.io_bound(feed.fetch)
        except (OSError, ValueError):
            try:
                slot.delete()
            except Exception:  # noqa: BLE001
                pass
            return
        try:
            slot.clear()
            now = datetime.now()
            with slot:
                ui.label(data["title"] or "Actualités").classes("fd-head").mark("feed-title")
                for it in data["items"]:
                    with ui.element("div").classes("fd-item").on("click", lambda _, u=it["link"]: self.feed_open(u)).mark("feed-item"):
                        with ui.column().classes("gap-0 col"):
                            ui.label(it["title"]).classes("fd-t")
                            if it["summary"]:
                                ui.label(it["summary"]).classes("fd-s")
                            ui.label(stamp(it["when"], now)).classes("rmeta")
                        if it["image"]:
                            ui.html(f'<img src="{html.escape(it["image"])}" alt="" loading="lazy" referrerpolicy="no-referrer">', sanitize=False).classes("fd-img")
        except Exception:  # noqa: BLE001 — le panneau a pu être fermé entre-temps
            pass

    def feed_open(self, url: str) -> None:
        if SERVER_MODE:
            ui.navigate.to(url, new_tab=True)
        else:
            webbrowser.open(url)


def feed_settings() -> None:
    """Bloc « Flux d'actualités dans le brief » des Paramètres : interrupteur et adresse du flux."""
    ui.label("Flux d'actualités dans le brief du jour").classes("text-subtitle2 q-mt-md")
    ui.switch("Afficher un flux RSS sous l'agenda", value=feed.enabled(),
              on_change=lambda e: db.kv_set("feed_on", "1" if e.value else "0")).props("dense").mark("feed-switch")

    def save(e) -> None:
        v = (e.value or "").strip()
        if v and not feed.valid(v):
            ui.notify("L'adresse du flux doit commencer par https://", type="warning")
            return
        db.kv_set("feed_url", v)
        feed._cache.clear()

    ui.input("Adresse du flux", value=db.kv_get("feed_url") or "", placeholder=feed.DEFAULT_URL, on_change=save).props("outlined dense stack-label clearable").classes("w-full").mark("feed-url")
    ui.label("Laissez vide pour « À la une » du Monde. Le flux est relu toutes les 15 minutes ; les articles s'ouvrent dans votre navigateur.").classes("text-caption text-grey")
