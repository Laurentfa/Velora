"""Paramètres › À propos : état de la surveillance des avis de sécurité des composants (voir vulnwatch), et vérification automatique en arrière-plan."""
from datetime import datetime

from nicegui import run, ui

from . import db, vulnwatch

_COLORS = {"CRITICAL": "#d92d3a", "HIGH": "#d92d3a", "MODERATE": "#c27c0e", "MEDIUM": "#c27c0e", "LOW": "#66768a", "": "#66768a"}


def _when(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso).astimezone().strftime("%d/%m/%Y à %H:%M")
    except (ValueError, TypeError):
        return "?"


def panel() -> None:
    ui.label("Sécurité des composants").classes("text-subtitle1 q-mt-lg").style("font-weight:700")
    ui.label("Velora embarque des bibliothèques. Une fois par semaine, elle demande à OSV.dev (base publique des avis de sécurité) si les versions qu'elle contient ont une faille connue. "
             "Ce qui part sur Internet : seulement des noms de bibliothèques et leurs numéros de version, jamais un mail, un nom ou un réglage. "
             "Une faille ne se corrige que par une nouvelle version de Velora : cet écran vous dit quand elle est nécessaire.").classes("text-caption text-grey")

    @ui.refreshable
    def status() -> None:
        last, issues = vulnwatch.last_result(), vulnwatch.open_issues()
        if not last:
            ui.label("Pas encore vérifié.").classes("text-body2")
        elif last.get("error") and not last.get("issues"):
            ui.label(f"Dernière vérification impossible ({last['error']}) : nouvel essai bientôt.").classes("text-body2").style("color:#c27c0e")
        elif not issues:
            ui.label(f"✓ Aucun avis de sécurité connu sur les {last.get('n', 0)} composants (vérifié le {_when(last.get('at', ''))}).").classes("text-body2").style("color:#18803a")
        else:
            ui.label(f"{len(issues)} avis de sécurité sur des composants (vérifié le {_when(last.get('at', ''))}). Une nouvelle version de Velora est à prévoir.").classes(
                "text-body2").style("font-weight:600;color:#d92d3a")
            for i in issues[:15]:
                with ui.row().classes("items-start no-wrap gap-2 w-full").style("padding:4px 0;border-top:1px solid var(--line)"):
                    ui.label(vulnwatch.SEVERITY_FR.get(i["severity"], "gravité ?")).style(f"min-width:64px;font-size:12px;font-weight:700;color:{_COLORS.get(i['severity'], '#66768a')}")
                    with ui.column().classes("gap-0 col"):
                        ui.label(f"{i['name']} {i['version']}" + (f" (module {i['from']})" if i.get("from") else "") + (f" → corrigé dans {i['fixed']}" if i.get("fixed") else "")).style(
                            "font-size:13px;font-weight:600")
                        ui.link(i["id"], f"https://osv.dev/vulnerability/{i['id']}", new_tab=True).style("font-size:12px")
                        if i.get("summary"):
                            ui.label(i["summary"]).classes("rmeta")
                    ui.button("Écarter", on_click=lambda _, a=i["id"]: (vulnwatch.dismiss(a), status.refresh())).props("flat dense no-caps size=sm").tooltip(
                        "Cet avis ne vous concerne pas : il disparaît de la liste")
            if len(issues) > 15:
                ui.label(f"… et {len(issues) - 15} autre(s) : les plus graves sont affichés d'abord.").classes("rmeta")
        if vulnwatch.dismissed():
            ui.button(f"Réafficher les {len(vulnwatch.dismissed())} avis écartés", on_click=lambda: (db.kv_set(vulnwatch.KV_OFF, "[]"), status.refresh())).props("flat dense no-caps size=sm")

    status()

    async def now() -> None:
        btn.props(add="loading disable")
        try:
            await run.io_bound(vulnwatch.check, True)
        finally:
            btn.props(remove="loading disable")
        status.refresh()

    with ui.row().classes("items-center gap-3"):
        btn = ui.button("Vérifier maintenant", icon="shield", on_click=now).props("flat no-caps dense").mark("vuln-check")
        ui.switch("Vérifier chaque semaine", value=vulnwatch.enabled(), on_change=lambda e: db.kv_set(vulnwatch.KV_ON, "1" if e.value else "0")).mark("vuln-switch")


async def auto_check() -> None:
    """Au démarrage puis toutes les 6 heures (la vérification elle-même n'a lieu qu'une fois par semaine) : une alerte discrète quand un avis GRAVE apparaît."""
    try:
        res = await run.io_bound(vulnwatch.check)
    except Exception:  # noqa: BLE001 — ne gêne jamais
        return
    if not res:
        return
    grave = [i for i in vulnwatch.open_issues() if i["severity"] in ("CRITICAL", "HIGH")]
    seen = set((db.kv_get("vuln:notified") or "").split(","))
    new = [i for i in grave if i["id"] not in seen]
    if new:
        db.kv_set("vuln:notified", ",".join(sorted({i["id"] for i in grave})))
        ui.notification(f"{len(new)} avis de sécurité grave(s) sur des composants de Velora. Détails dans Paramètres › À propos : une nouvelle version est à prévoir.",
                        type="warning", timeout=0, close_button="OK")
