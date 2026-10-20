"""Panneau « agenda » de la colonne de droite, affiché dans le brief du jour : la semaine en grille horaire, avec les rendez-vous Exchange et Google Agenda.

Fond transparent (celui de l'application). Un clic sur un créneau libre ou sur « + » propose d'ajouter un rendez-vous ; le panneau disparaît dès qu'on ouvre un mail ou qu'on écrit un message."""
import html
import math
from datetime import date, datetime, timedelta

from nicegui import background_tasks, run, ui

from mailia import agenda, config, i18n, sync
from mailia.ui_common import JOURS, MOIS_COURTS

HOUR_PX = 40


def grid_html(first: date, data: dict, now: datetime) -> str:
    """Grille horaire de la semaine : colonnes de jours, rendez-vous placés à l'heure, journées entières en bandeau."""
    e, T = html.escape, i18n.tr
    events = data["events"]
    timed = [x for x in events if not x["all_day"]]
    weekend = any(x["start"].weekday() >= 5 or (x["end"] - timedelta(seconds=1)).weekday() >= 5 for x in events if first <= x["start"].date() < first + timedelta(days=7))
    days = [first + timedelta(days=i) for i in range(7 if weekend else 5)]
    h0 = min([8] + [x["start"].hour for x in timed if x["start"].date() in days])
    h1 = max([19] + [min(24, math.ceil((x["end"].hour * 60 + x["end"].minute) / 60) or 24) for x in timed if x["end"].date() in days])
    height = (h1 - h0) * HOUR_PX
    head = "".join(f'<div class="ag-dh{" ag-today" if d == now.date() else ""}"><span>{e(T(JOURS[d.weekday()]))}</span> {d.day}</div>' for d in days)
    allday = ""
    if any(x["all_day"] for x in events):
        cells = []
        for d in days:
            ds = datetime(d.year, d.month, d.day)
            chips = "".join(f'<div class="ag-ad ag-{_cls(x)}" title="{e(x["title"])}">{e(x["title"] or T("(sans titre)"))}</div>'
                            for x in events if x["all_day"] and x["start"] < ds + timedelta(days=1) and x["end"] > ds)
            cells.append(f'<div class="ag-adc">{chips}</div>')
        allday = '<div class="ag-allday"><div></div>' + "".join(cells) + "</div>"
    hours = "".join(f'<div class="ag-hr" style="height:{HOUR_PX}px"><span>{h}</span></div>' for h in range(h0, h1))
    cols = []
    for d in days:
        ds, de = datetime(d.year, d.month, d.day), datetime(d.year, d.month, d.day) + timedelta(days=1)
        day_events = []
        for x in timed:
            if x["start"] < de and x["end"] > ds:
                day_events.append({**x, "start": max(x["start"], ds), "end": min(x["end"], de)})
        blocks = []
        for x, col, n in agenda.lanes(day_events):
            top = ((x["start"] - ds).total_seconds() / 3600 - h0) * HOUR_PX
            hgt = max(18, (x["end"] - x["start"]).total_seconds() / 3600 * HOUR_PX - 2)
            tip = f'{x["start"]:%H:%M} – {x["end"]:%H:%M} · {x["title"]}' + (f' · {x["where"]}' if x["where"] else "")
            blocks.append(f'<div class="ag-ev ag-{_cls(x)}" style="top:{top:.0f}px;height:{hgt:.0f}px;left:calc({col} * 100% / {n} + 2px);width:calc(100% / {n} - 4px)" '
                          f'title="{e(tip)}"><b>{x["start"]:%H:%M}</b> {e(x["title"] or T("(sans titre)"))}</div>')
        line = ""
        if d == now.date() and h0 <= now.hour + now.minute / 60 <= h1:
            line = f'<div class="ag-now" style="top:{(now.hour + now.minute / 60 - h0) * HOUR_PX:.0f}px"></div>'
        cols.append(f'<div class="ag-col" data-day="{d.isoformat()}" data-h0="{h0}" data-rh="{HOUR_PX}" style="height:{height}px">{"".join(blocks)}{line}</div>')
    n = len(days)
    style = f'grid-template-columns:34px repeat({n},minmax(0,1fr))'
    return (f'<div class="ag"><div class="ag-days" style="{style}"><div></div>{head}</div>'
            + allday.replace('class="ag-allday"', f'class="ag-allday" style="{style}"')
            + f'<div class="ag-grid" style="{style}"><div class="ag-hrs">{hours}</div>{"".join(cols)}</div></div>')


def _cls(x: dict) -> str:
    s = x["src"]
    return "both" if len(s) > 1 else ("go" if "google" in s else "ex")


def range_label(first: date, n: int = 5) -> str:
    last = first + timedelta(days=n - 1)
    T = i18n.tr
    if first.month == last.month:
        return f"{first.day} – {last.day} {T(MOIS_COURTS[last.month - 1])}"
    return f"{first.day} {T(MOIS_COURTS[first.month - 1])} – {last.day} {T(MOIS_COURTS[last.month - 1])}"


class AgendaMixin:
    agenda_off = 0

    def agenda_panel(self) -> None:
        """Colonne de droite du brief : l'agenda de la semaine (lecture), rempli en arrière-plan."""
        src = agenda.sources()
        with ui.column().classes("agenda gap-0"):
            with ui.row().classes("ag-head items-center no-wrap gap-1"):
                ui.icon("event", size="20px").style("color:var(--accent)")
                label = ui.label("").classes("ag-title")
                ui.element("div").classes("col")
                ui.button(icon="chevron_left", on_click=lambda: self._agenda_move(-1)).props("flat dense round size=sm").tooltip("Semaine précédente").mark("agenda-prev")
                ui.button("Aujourd'hui", on_click=lambda: self._agenda_move(0)).props("flat dense no-caps size=sm").mark("agenda-today")
                ui.button(icon="chevron_right", on_click=lambda: self._agenda_move(1)).props("flat dense round size=sm").tooltip("Semaine suivante").mark("agenda-next")
                ui.button(icon="add", on_click=lambda: self.agenda_add(None)).props("flat dense round size=sm color=primary").tooltip("Ajouter un rendez-vous").mark("agenda-add")
            with ui.element("div").classes("ag-scroll"):
                body = ui.html("", sanitize=False).mark("agenda-grid")
            note = ui.label("").classes("rmeta ag-note")
        self._agenda_ui = (label, body, note)
        if not (src["exchange"] or src["google"]):
            body.set_content('<div class="ag-empty">' + html.escape(i18n.tr("Aucun agenda connecté : ajoutez Exchange ou Google Agenda dans les Paramètres.")) + "</div>")
            note.set_text("")
            return
        background_tasks.create(self._agenda_fill())
        ui.timer(300, lambda: background_tasks.create(self._agenda_fill()))  # la ligne de l'heure et les nouveaux rendez-vous

    def _agenda_move(self, step: int) -> None:
        self.agenda_off = self.agenda_off + step if step else 0
        background_tasks.create(self._agenda_fill())

    async def _agenda_fill(self, force: bool = False) -> None:
        ui_ = getattr(self, "_agenda_ui", None)
        if not ui_:
            return
        label, body, note = ui_
        first = agenda.week_start(date.today()) + timedelta(weeks=self.agenda_off)
        try:
            label.set_text(range_label(first))
            data = await run.io_bound(agenda.fetch, first, 7, force)
            body.set_content(grid_html(first, data, datetime.now()))
            note.set_text(" · ".join(data["errors"]))
            ui.run_javascript("mailiaAgendaScroll()")
        except Exception:  # noqa: BLE001 — le panneau a pu être fermé entre-temps
            pass

    async def agenda_slot(self, args: dict | None) -> None:
        """Clic sur un créneau libre de la grille."""
        a = args or {}
        try:
            d = date.fromisoformat(a["day"])
            start = datetime(d.year, d.month, d.day) + timedelta(minutes=int(a["min"]))
        except (KeyError, ValueError, TypeError):
            return
        await self.agenda_add(start)

    async def agenda_add(self, start: datetime | None) -> None:
        src = agenda.sources()
        names = {"exchange": "Exchange", "google": "Google Agenda"}
        choices = {k: v for k, v in names.items() if src[k]}
        if not choices:
            return ui.notify("Aucun agenda connecté (Paramètres › Agenda).", type="warning")
        if start is None:
            now = datetime.now()
            start = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        default = config.calendar_backend() if config.calendar_backend() in choices else next(iter(choices))
        with self.host, ui.dialog() as dlg, ui.card().style("width:min(480px,92vw)"):
            ui.label("Ajouter un rendez-vous").classes("text-h6")
            title = ui.input("Titre").props("outlined dense stack-label autofocus").classes("w-full").mark("agenda-title")
            with ui.row().classes("w-full no-wrap gap-2"):
                begin = ui.input("Début", value=f"{start:%Y-%m-%dT%H:%M}").props("outlined dense stack-label type=datetime-local").classes("col")
                end = ui.input("Fin", value=f"{start + timedelta(hours=1):%Y-%m-%dT%H:%M}").props("outlined dense stack-label type=datetime-local").classes("col")
            where = ui.input("Lieu (facultatif)").props("outlined dense stack-label").classes("w-full")
            target = ui.select(choices, value=default, label="Agenda").props("outlined dense stack-label").classes("w-full") if len(choices) > 1 else None
            ui.label("Le rendez-vous est créé sans invité : personne n'est prévenu.").classes("text-caption text-grey")
            with ui.row().classes("justify-end w-full gap-2"):
                ui.button("Annuler", on_click=lambda: dlg.submit(None)).props("flat no-caps")
                ui.button("Ajouter", on_click=lambda: dlg.submit((title.value, begin.value, end.value, where.value, target.value if target else default))).props(
                    "unelevated no-caps color=primary").mark("agenda-save")
        r = await dlg
        dlg.delete()
        if not r:
            return
        try:
            a, b = datetime.fromisoformat(r[1]), datetime.fromisoformat(r[2])
            if b <= a or not (r[0] or "").strip():
                return ui.notify("Indiquez un titre, et une fin postérieure au début.", type="warning")
            account = next((k for k, c in sync.ACCOUNTS.items() if c.kind == "ews" and c.password), "")
            await run.io_bound(sync.create_event, account, r[0].strip(), a, b, r[3] or "", "", r[4])
        except Exception as e:  # noqa: BLE001
            return ui.notify(f"Ajout impossible : {e}", type="negative", multi_line=True)
        agenda.invalidate()
        ui.notify("Rendez-vous ajouté à l'agenda", type="positive")
        await self._agenda_fill(force=True)
