"""Compteur de crédits 1min.AI dans la fenêtre principale : tous vos ordinateurs, aujourd'hui et ce mois-ci (voir fleet)."""
from nicegui import ui

from . import aipolicy, fleet


def summary_text(snap: list[dict]) -> str:
    """« IA : 12 400 crédits aujourd'hui · 310 000 ce mois-ci » (vide tant que rien n'a été dépensé)."""
    day, month = sum(m["cr_day"] for m in snap), sum(m["cr_month"] for m in snap)
    if not month and not day:
        return ""
    cap = aipolicy.daily_credit_cap()
    over = " · seuil dépassé" if aipolicy.credits_alert(day, month) else ""
    return f"IA : {fleet.compact(day)}" + (f" sur {fleet.compact(cap)}" if cap and len(snap) == 1 else "") + f" crédits aujourd'hui · {fleet.compact(month)} ce mois-ci{over}"


def tooltip_text(snap: list[dict]) -> str:
    if len(snap) < 2:
        return "Crédits 1min.AI dépensés par cette installation. Cliquez pour le détail."
    return "\n".join(f"{m['name']}{' (cet ordinateur)' if m['me'] else ''} : {fleet.compact(m['cr_day'])} aujourd'hui · {fleet.compact(m['cr_month'])} ce mois-ci" for m in snap)


def dialog(open_settings) -> None:
    """Le détail par ordinateur : version, dernière apparition, crédits du jour et du mois."""
    snap = fleet.snapshot()
    top = fleet.top_version()
    with ui.dialog() as dlg, ui.card().style("width:min(720px,94vw);max-width:94vw"):
        ui.label("Crédits 1min.AI par ordinateur").classes("text-h6")
        ui.label("Chaque ordinateur compte ses propres appels et dépose le total dans votre boîte Exchange (un élément caché, sans aucun contenu de mail) : "
                 "on voit ici la somme de tous vos ordinateurs.").classes("text-caption text-grey")
        with ui.row().classes("w-full items-center no-wrap gap-2 q-mt-sm").style("font-weight:600"):
            ui.label("Ordinateur").classes("col")
            ui.label("Aujourd'hui").style("width:100px;text-align:right")
            ui.label("Ce mois-ci").style("width:100px;text-align:right")
        for m in snap:
            old = bool(top and m["version"] and not m["me"] and fleet.updater.parse(m["version"]) < fleet.updater.parse(top))
            with ui.row().classes("w-full items-center no-wrap gap-2").mark("fleet-row"):
                with ui.column().classes("col gap-0"):
                    ui.label(m["name"] + (" (cet ordinateur)" if m["me"] else ""))
                    current = bool(top and m["version"] and fleet.updater.parse(m["version"]) >= fleet.updater.parse(top))
                    ui.label(f"Velora {m['version'] or '?'} · vu {fleet.ago(m['seen'])}" + (f" · en retard sur la {top}" if old else (" · à jour" if current else ""))).classes(
                        "text-caption " + ("text-negative" if old else "text-grey"))
                ui.label(fleet.compact(m["cr_day"])).style("width:100px;text-align:right")
                ui.label(fleet.compact(m["cr_month"])).style("width:100px;text-align:right")
        ui.separator()
        with ui.row().classes("w-full items-center no-wrap gap-2").style("font-weight:700"):
            ui.label("Total").classes("col")
            ui.label(fleet.compact(sum(m["cr_day"] for m in snap))).style("width:100px;text-align:right")
            ui.label(fleet.compact(sum(m["cr_month"] for m in snap))).style("width:100px;text-align:right")
        if any(fleet.updater.parse(m["version"] or "0") < fleet.updater.parse(top or "0") for m in snap):
            ui.label("Un ordinateur en retard se met à jour tout seul : Velora cherche une nouvelle version toutes les 6 heures et l'installe après 20 minutes sans l'utiliser "
                     "(Paramètres › Utilisation et données). Il faut simplement que Velora soit ouverte dessus.").classes("text-caption text-grey")
        if len(snap) == 1:
            ui.label("Les autres ordinateurs n'apparaissent qu'une fois Velora ouverte dessus (dans cette version ou une plus récente), avec la synchronisation des réglages allumée.").classes("text-caption text-grey")
        with ui.row().classes("w-full justify-end gap-2"):
            ui.button("Plafonds et détails", icon="tune", on_click=lambda: (dlg.close(), open_settings())).props("flat no-caps")
            ui.button("Fermer", on_click=dlg.close).props("unelevated no-caps color=primary")
    dlg.open()
