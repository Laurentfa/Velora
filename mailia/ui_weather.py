"""Carte météo du brief du jour (voir weather.py) et son réglage dans les Paramètres."""
import html

from nicegui import background_tasks, run, ui

from mailia import db, i18n, weather

ICON = "/weather-icons/{}.svg"


def card_html(s: dict, place_name: str) -> str:
    e = html.escape
    T = i18n.tr  # la carte suit la langue de l'interface (libellés du temps, « ressenti », « vent »…)
    img = lambda name, cls="": f'<img class="{cls}" src="{ICON.format(name)}" alt="">'
    hours = "".join(f'<div class="wx-h"><span>{e(h["hour"])}</span>{img(h["icon"])}<b>{h["temp"]}°</b>'
                    f'<i>{img("raindrop")}{h["rain"]} %</i></div>' for h in s["next"])
    return (f'<div class="wx"><div class="wx-main">{img(s["icon"], "wx-icon")}<div class="wx-temp">{s["temp"]}°</div>'
            f'<div class="wx-info"><div class="wx-label">{e(T(s["label"]))}</div><div class="wx-place">{e(place_name)}</div>'
            f'<div class="wx-sub">{T("Ressenti")} {s["feels"]}° · {T("min")} {s["tmin"]}° · {T("max")} {s["tmax"]}°</div>'
            f'<div class="wx-sub">{img("raindrop")}{s["rain"]} % · {T("vent")} {s["wind"]} km/h · {img("sunrise")}{e(s["sunrise"])} · {img("sunset")}{e(s["sunset"])}</div></div></div>'
            f'<div class="wx-hours">{hours}</div>'
            f'<div class="wx-credit">{e(weather.ATTRIBUTION)} · {T("icônes Meteocons")}</div></div>')


class WeatherMixin:
    def weather_card(self) -> None:
        """Dans le brief : la carte météo, remplie en arrière-plan (le brief ne l'attend pas) ; absente si désactivée, sans ville ou hors connexion."""
        if not weather.enabled():
            return
        slot = ui.element("div").classes("wx-slot")
        background_tasks.create(self._weather_fill(slot))

    async def _weather_fill(self, slot) -> None:
        s = await run.io_bound(weather.report)
        p = weather.place()
        try:
            if not s or not p:
                slot.delete()
                return
            with slot:
                ui.html(card_html(s, weather.place_label(p)), sanitize=False).mark("weather-card")
        except Exception:  # noqa: BLE001 — le brief a pu être redessiné entre-temps
            pass


def weather_settings() -> None:
    """Bloc « Météo dans le brief du jour » des Paramètres : interrupteur, recherche de la ville."""
    ui.label("Météo dans le brief du jour").classes("text-subtitle2 q-mt-md")
    ui.switch("Afficher la météo de ma ville", value=(db.kv_get("weather_on") or "1") == "1",
              on_change=lambda e: db.kv_set("weather_on", "1" if e.value else "0")).props("dense").mark("weather-switch")
    state = {"results": []}

    @ui.refreshable
    def current() -> None:
        p = weather.place()
        if p:
            with ui.row().classes("items-center gap-2"):
                ui.icon("place", size="18px").style("color:var(--accent)")
                ui.label(weather.place_label(p)).classes("text-body2").mark("weather-place")
                ui.button("Retirer", icon="close", on_click=lambda: (weather.set_place(None), current.refresh(), results.refresh())).props("flat dense no-caps size=sm")
        else:
            ui.label("Aucune ville choisie : la météo n'apparaît pas.").classes("text-caption text-grey")

    @ui.refreshable
    def results() -> None:
        for r in state["results"]:
            label = ", ".join(x for x in (r["name"], r["region"], r["country"]) if x)

            def pick(r=r) -> None:
                weather.set_place({"name": r["name"], "region": r["region"], "lat": r["lat"], "lon": r["lon"]})
                state["results"] = []
                current.refresh()
                results.refresh()
                ui.notify(f"Météo de {r['name']} activée pour le brief.", type="positive")
            ui.button(label, icon="place", on_click=pick).props("flat dense no-caps align=left").classes("w-full").mark("weather-result")

    async def look() -> None:
        try:
            state["results"] = await run.io_bound(weather.search, query.value)
        except Exception:  # noqa: BLE001
            state["results"] = []
            ui.notify("Recherche impossible : vérifiez la connexion.", type="warning")
        results.refresh()
        if not state["results"] and (query.value or "").strip():
            ui.notify("Aucune ville trouvée.", type="warning")

    current()
    with ui.row().classes("w-full items-center no-wrap gap-2"):
        query = ui.input("Chercher une ville", placeholder="ex : Rouen").props("outlined dense stack-label").classes("col").on("keydown.enter", look).mark("weather-query")
        ui.button("Rechercher", icon="search", on_click=look).props("outline no-caps dense color=primary").mark("weather-search")
    results()
    ui.label("Seules les coordonnées de la ville sont envoyées à Open-Meteo.com, un service gratuit. Aucune IA n'est utilisée.").classes("text-caption text-grey")
