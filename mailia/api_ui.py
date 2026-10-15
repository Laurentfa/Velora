"""Onglet « API et assistants » des paramètres : deux interrupteurs séparés (API REST, assistants IA/MCP), accès réseau, clés, journal."""
import json
import sys
from datetime import datetime
from pathlib import Path

from nicegui import run, ui

from . import apikeys, apiserver, config, db

EXPIRY = {0: "Jamais", 30: "30 jours", 90: "90 jours", 365: "1 an"}


def bridge_command() -> tuple[str, list[str]]:
    """(programme, arguments) pour lancer le pont MCP depuis un assistant : l'application elle-même."""
    if getattr(sys, "frozen", False):
        return sys.executable, ["--mcp-stdio"]
    return sys.executable, [str(Path(__file__).resolve().parent.parent / "main.py"), "--mcp-stdio"]


def api_panel(host, open_link) -> None:
    ui.label("API et assistants").classes("text-h6").style("font-weight:700")
    ui.label("Deux accès, activables séparément. Ils agissent comme vous le feriez dans l'application : mêmes boîtes, même file d'envoi, mêmes limites. "
             "Rien n'est ouvert tant que vous ne l'activez pas, et seul cet ordinateur peut s'y connecter (sauf si vous autorisez le réseau local).").classes("text-caption text-grey")

    status_box = ui.column().classes("w-full gap-1")

    @ui.refreshable
    def status_view() -> None:
        s = apiserver.status()
        if not (s["rest"] or s["mcp"]):
            ui.label("Désactivé : aucun serveur n'est ouvert.").classes("text-body2 text-grey")
            return
        if s["running"]:
            with ui.row().classes("items-center gap-2"):
                ui.icon("check_circle", size="18px").style("color:#16a34a")
                ui.label(f"Serveur actif sur {s['base']}").classes("text-body2")
            if s["rest"]:
                with ui.row().classes("items-center gap-2"):
                    ui.label(f"API : {s['base']}/v1").classes("rmeta").style("user-select:text")
                    ui.button("Documentation", icon="menu_book", on_click=lambda: open_link(s["base"] + "/docs")).props("flat dense no-caps size=sm")
            if s["mcp"]:
                ui.label(f"MCP : {s['base']}/mcp").classes("rmeta").style("user-select:text")
        else:
            ui.label("Le serveur n'a pas pu démarrer : " + (s["error"] or "port indisponible") + " Changez le port ci-dessous.").classes("text-body2 text-negative")

    async def apply(_e=None) -> None:
        await run.io_bound(apiserver.ensure)
        status_view.refresh()
        snippets.refresh()

    async def set_flag(key: str, on: bool) -> None:
        db.kv_set(key, "1" if on else "0")
        await apply()

    async def set_port() -> None:
        db.kv_set("api_port", str(int(port.value or apiserver.DEFAULT_PORT)))
        await apply()

    with ui.element("div").classes("w-full q-pa-md q-mt-sm").style("border:1px solid var(--line);border-radius:12px"):
        ui.switch("API REST (programmes, scripts, outils de gestion)", value=apiserver.rest_on(),
                  on_change=lambda e: set_flag("api_rest_on", e.value)).style("font-weight:600")
        ui.label("Lire, rédiger, envoyer immédiatement ou à l'heure choisie, annuler un envoi. Documentation interactive incluse.").classes("text-caption text-grey")
    with ui.element("div").classes("w-full q-pa-md q-mt-sm").style("border:1px solid var(--line);border-radius:12px"):
        ui.switch("Assistants IA (serveur MCP : Claude et autres)", value=apiserver.mcp_on(),
                  on_change=lambda e: set_flag("api_mcp_on", e.value)).style("font-weight:600")
        ui.label("Un assistant peut chercher, lire, rédiger et envoyer pour vous, avec les droits de la clé que vous lui donnez.").classes("text-caption text-grey")

    with ui.row().classes("items-center gap-3 q-mt-sm"):
        port = ui.number("Port", value=apiserver.port(), min=1024, max=65535, precision=0).props("outlined dense").style("width:120px")
        ui.button("Appliquer", on_click=set_port).props("outline no-caps dense")
        ui.switch("Autoriser le réseau local", value=apiserver.lan_on(),
                  on_change=lambda e: set_flag("api_lan", e.value)).tooltip(
            "Les autres appareils du réseau pourront se connecter. La connexion n'est pas chiffrée : à réserver à un réseau de confiance.")
    with status_box:
        status_view()

    # ---------- connexion d'un assistant
    @ui.refreshable
    def snippets() -> None:
        if not apiserver.mcp_on():
            return
        base = apiserver.status()["base"]
        prog, args = bridge_command()
        desktop = json.dumps({"mcpServers": {"velora": {"command": prog, "args": args, "env": {"MAILIA_API_KEY": "mia_VOTRE_CLE"}}}}, indent=2, ensure_ascii=False)
        code = f'claude mcp add --transport http velora {base}/mcp --header "Authorization: Bearer mia_VOTRE_CLE"'
        ui.label("Connecter un assistant").classes("text-subtitle2 q-mt-md")
        ui.label("Remplacez mia_VOTRE_CLE par une clé créée ci-dessous.").classes("text-caption text-grey")
        for title, text in (("Claude Code (terminal)", code), ("Claude Desktop (fichier de configuration)", desktop)):
            with ui.expansion(title).classes("w-full").props("dense"):
                ui.label(text).style("font-family:monospace;font-size:12px;white-space:pre-wrap;word-break:break-all;user-select:text;background:var(--surface2);padding:10px;border-radius:8px")
                ui.button("Copier", icon="content_copy", on_click=lambda _, t=text: (ui.clipboard.write(t), ui.notify("Copié", type="positive", timeout=1200))).props("flat dense no-caps size=sm")
    snippets()

    # ---------- clés
    ui.label("Clés d'accès").classes("text-subtitle2 q-mt-lg")
    ui.label("Chaque programme ou assistant reçoit sa propre clé, avec ses droits. La clé n'est montrée qu'une fois ; vous pouvez la révoquer à tout moment.").classes("text-caption text-grey")

    @ui.refreshable
    def keys_view() -> None:
        rows = db.api_keys_list()
        if not rows:
            ui.label("Aucune clé.").classes("text-caption text-grey")
        for r in rows:
            scopes = json.loads(r["scopes"] or "[]")
            chans = json.loads(r["channels"] or "[]")
            with ui.row().classes("w-full items-center no-wrap gap-2").style("border:1px solid var(--line);border-radius:12px;padding:8px 10px" + (";opacity:.5" if r["revoked"] else "")):
                ui.icon("key", size="22px").style("color:var(--accent)")
                with ui.column().classes("col gap-0").style("min-width:0"):
                    ui.label(r["name"]).style("font-weight:600").classes("ellipsis")
                    ui.label(f"{r['prefix']}… · " + ", ".join(scopes) + " · " + " + ".join(apikeys.CHANNELS.get(c, c) for c in chans)).classes("rmeta")
                    ui.label(("Envoi direct" if r["send_mode"] == "direct" else "Envois à valider dans l'application") +
                             (f" (max {r['daily_send_cap']}/jour)" if r["send_mode"] == "direct" and "send" in scopes else "") +
                             (f" · expire le {r['expires'][:10]}" if r["expires"] else "") +
                             (f" · dernière utilisation {r['last_used'][:16].replace('T', ' ')}" if r["last_used"] else " · jamais utilisée")).classes("rmeta")
                ui.button(icon="delete_outline", on_click=lambda _, i=r["id"], n=r["name"]: delete_key(i, n)).props("flat dense round").tooltip("Révoquer et supprimer cette clé")

    async def delete_key(kid: str, name: str) -> None:
        with host, ui.dialog() as dlg, ui.card().style("width:460px;max-width:96vw"):
            ui.label(f"Supprimer la clé « {name} » ?").classes("text-h6")
            ui.label("Tout programme qui l'utilise sera immédiatement refusé.").classes("text-body2")
            with ui.row().classes("justify-end w-full gap-2"):
                ui.button("Annuler", on_click=lambda: dlg.submit(False)).props("flat no-caps")
                ui.button("Supprimer", on_click=lambda: dlg.submit(True)).props("unelevated no-caps color=negative")
        if await dlg:
            db.api_key_delete(kid)
            keys_view.refresh()

    keys_view()

    async def new_key() -> None:
        accts = {k: f"{c.label} ({c.email})" for k, c in config.load_accounts().items()}
        o = {"name": "", "preset": "Rédaction (brouillons à valider)", "scopes": list(apikeys.PRESETS["Rédaction (brouillons à valider)"]), "rest": True, "mcp": False,
             "accounts": [], "folders": "", "mode": "approval", "cap": 20, "ai_cap": 30, "domains": "", "expires": 90}
        with host, ui.dialog() as dlg, ui.card().classes("no-wrap").style("width:680px;max-width:96vw;max-height:92vh;overflow:auto"):
            ui.label("Nouvelle clé").classes("text-h6")
            ui.input("Nom (à qui ou à quoi sert-elle ?)", placeholder="ex : Odoo, Claude, script de relances").bind_value(o, "name").props("outlined dense autofocus stack-label").classes("w-full")
            ui.label("Canaux autorisés").classes("text-caption q-mt-sm")
            with ui.row().classes("gap-4"):
                ui.checkbox("API REST").bind_value(o, "rest")
                ui.checkbox("Assistants IA (MCP)").bind_value(o, "mcp")
            ui.label("Droits").classes("text-caption q-mt-sm")
            refs: dict = {}  # le champ « droits détaillés » n'existe pas encore quand le profil est créé : on le retrouve ici

            def pick_preset(e) -> None:
                if e.value in apikeys.PRESETS:
                    o.update(scopes=list(apikeys.PRESETS[e.value]))
                    if "box" in refs:
                        refs["box"].set_value(list(apikeys.PRESETS[e.value]))
            ui.select(list(apikeys.PRESETS), label="Profil", on_change=pick_preset).bind_value(o, "preset").props("outlined dense").classes("w-full")
            refs["box"] = ui.select({k: f"{k} — {v}" for k, v in apikeys.SCOPES.items()}, multiple=True, label="Droits détaillés").bind_value(o, "scopes").props(
                "outlined dense use-chips").classes("w-full")
            ui.select(accts, multiple=True, label="Boîtes autorisées (vide = toutes)").bind_value(o, "accounts").props("outlined dense use-chips").classes("w-full")
            ui.input("Dossiers autorisés (vide = tous)", placeholder="ex : INBOX, Archive").bind_value(o, "folders").props("outlined dense stack-label").classes("w-full")
            ui.label("Envoi").classes("text-caption q-mt-sm")
            ui.select({"approval": "Je valide chaque envoi dans l'application (recommandé)", "direct": "Envoi direct, avec les limites ci-dessous"}, label="Mode d'envoi").bind_value(
                o, "mode").props("outlined dense").classes("w-full")
            with ui.column().classes("w-full gap-2").bind_visibility_from(o, "mode", backward=lambda m: m == "direct"):
                ui.number("Envois maximum par jour", min=1, max=500, precision=0).bind_value(o, "cap").props("outlined dense")
                ui.input("Domaines ou adresses autorisés (vide = tous)", placeholder="ex : exemple.fr, partenaire.org").bind_value(o, "domains").props("outlined dense stack-label").classes("w-full")
            ui.number("Appels IA maximum par jour", min=0, max=1000, precision=0).bind_value(o, "ai_cap").props("outlined dense")
            ui.select(EXPIRY, label="Expiration").bind_value(o, "expires").props("outlined dense").classes("w-full")
            msg = ui.label("").classes("text-negative text-caption")

            def create() -> None:
                if not o["name"].strip():
                    return msg.set_text("Donnez un nom à cette clé.")
                if not (o["rest"] or o["mcp"]):
                    return msg.set_text("Choisissez au moins un canal.")
                if not o["scopes"]:
                    return msg.set_text("Choisissez au moins un droit.")
                dlg.submit(True)

            with ui.row().classes("justify-end w-full gap-2 q-mt-sm"):
                ui.button("Annuler", on_click=lambda: dlg.submit(False)).props("flat no-caps")
                ui.button("Créer la clé", on_click=create).props("unelevated no-caps color=primary")
        if not await dlg:
            return
        kid, raw = apikeys.create(
            o["name"], o["scopes"], [c for c, on in (("rest", o["rest"]), ("mcp", o["mcp"])) if on], o["accounts"],
            [x.strip() for x in o["folders"].split(",") if x.strip()], o["mode"], int(o["cap"] or 20), int(o["ai_cap"] or 0),
            [x.strip() for x in o["domains"].replace("\n", ",").split(",") if x.strip()], int(o["expires"] or 0))
        keys_view.refresh()
        with host, ui.dialog().props("persistent") as shown, ui.card().style("width:600px;max-width:96vw"):
            ui.label("Votre clé").classes("text-h6")
            ui.label("Copiez-la maintenant : elle ne sera plus jamais affichée. Seule son empreinte est conservée.").classes("text-body2")
            ui.label(raw).style("font-family:monospace;word-break:break-all;user-select:text;background:var(--surface2);padding:10px;border-radius:8px;font-size:13px")
            ok = ui.checkbox("J'ai copié la clé", value=False)
            with ui.row().classes("justify-end w-full gap-2"):
                ui.button("Copier", icon="content_copy", on_click=lambda: (ui.clipboard.write(raw), ui.notify("Clé copiée", type="positive", timeout=1500))).props("flat no-caps")
                ui.button("Fermer", on_click=lambda: shown.submit(True)).props("unelevated no-caps color=primary").bind_enabled_from(ok, "value")
        await shown

    ui.button("Nouvelle clé", icon="add", on_click=new_key).props("unelevated no-caps color=primary dense").classes("q-mt-sm")

    # ---------- notifications et journal
    ui.switch("Me prévenir quand une clé crée un brouillon ou une demande d'envoi à valider", value=db.kv_get("api_notify") != "0",
              on_change=lambda e: db.kv_set("api_notify", "1" if e.value else "0")).classes("q-mt-md")

    ui.label("Journal des appels").classes("text-subtitle2 q-mt-md")

    @ui.refreshable
    def log_view() -> None:
        rows = db.api_log_list(60)
        if not rows:
            ui.label("Aucun appel pour l'instant.").classes("text-caption text-grey")
        for r in rows:
            when = datetime.fromisoformat(r["ts"]).astimezone().strftime("%d/%m %H:%M:%S")
            color = {"ok": "var(--muted)", "denied": "#c77800", "error": "var(--hi)"}.get(r["status"], "var(--muted)")
            ui.label(f"{when} · {r['key_name']} · {r['channel']} · {r['action']}" + (f" · {r['target']}" if r["target"] else "") +
                     (f" · {r['status']}" if r["status"] != "ok" else "") + (f" — {r['detail']}" if r["detail"] and r["status"] != "ok" else "")).classes("rmeta").style(f"color:{color}")

    log_view()
    ui.button("Actualiser le journal", icon="refresh", on_click=log_view.refresh).props("flat no-caps dense size=sm")
