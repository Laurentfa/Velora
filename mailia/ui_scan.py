"""Numérisation d'un document : scanner réseau ou téléphone, assemblage en PDF, ajout au message."""
import re
from datetime import datetime

from nicegui import run, ui

from mailia import scan

from mailia.ui_common import human_size, log


class ScanMixin:
    async def scan_dialog(self) -> None:
        """Numérise (scanner réseau ou téléphone), assemble les pages en un PDF et le joint au message."""
        import base64
        pages: list[dict] = []  # {"raw": JPEG, "thumb": data URI, "enh": bool}
        st = {"server": None, "seen": 0, "base": ""}
        o = {"mode": "color", "dpi": 200, "name": f"Scan {datetime.now():%Y-%m-%d %H.%M}", "source": "auto", "addr": "", "found": ""}

        async def add_pages(raws: list[bytes], enhance: bool) -> None:
            for raw in raws:
                try:
                    th = await run.io_bound(scan.thumbnail, raw)
                except Exception:
                    ui.notify("Une page reçue n'est pas lisible.", type="warning")
                    continue
                pages.append({"raw": raw, "thumb": "data:image/jpeg;base64," + base64.b64encode(th).decode(), "enh": enhance})
            pages_view.refresh()

        def stop_server() -> None:
            if st["server"]:
                st["server"].stop()
                st["server"] = None

        with self.host, ui.dialog() as dlg, ui.card().classes("no-wrap").style(
                "width:880px;max-width:96vw;max-height:94vh;overflow:auto"):
            ui.label("Scanner un document").classes("text-h6")
            with ui.tabs().props("dense align=left no-caps") as tabs:
                t_net = ui.tab("Scanner réseau", icon="print")
                t_ph = ui.tab("Téléphone", icon="smartphone")
            with ui.tab_panels(tabs, value=t_net).classes("w-full").props("animated=false"):
                with ui.tab_panel(t_net).classes("q-pa-sm"):
                    ui.label("Imprimantes multifonctions et scanners récents du réseau (AirScan / Mopria), sans pilote.").classes("rmeta")
                    with ui.row().classes("w-full items-center no-wrap gap-2 q-mt-sm"):
                        saved = scan.known()
                        o["found"] = saved[0]["base"] if saved else ""
                        found = ui.select({s_["base"]: s_["name"] for s_ in saved}, value=o["found"] or None, label="Scanner",
                                          on_change=lambda e: o.update(found=e.value or "")).props("outlined dense").classes("col")
                        forget_btn = ui.button(icon="delete_outline").props("flat dense round color=grey-7").tooltip("Oublier ce scanner")
                        find_btn = ui.button("Chercher", icon="search").props("flat no-caps")
                    ui.input("Ou adresse du scanner (ex. 192.168.1.20)").bind_value(o, "addr").props("outlined dense").classes("w-full q-mt-sm")
                    with ui.row().classes("w-full items-center no-wrap gap-2 q-mt-sm"):
                        ui.select({"auto": "Automatique (chargeur s'il contient des feuilles, sinon vitre)", "platen": "Vitre (une page à la fois)",
                                   "feeder": "Chargeur automatique", "duplex": "Chargeur recto-verso"},
                                  label="Source").bind_value(o, "source").props("outlined dense").classes("col")
                        go_btn = ui.button("Numériser", icon="document_scanner").props("unelevated no-caps color=primary")
                    net_msg = ui.label("").classes("rmeta q-mt-sm")

                    async def find() -> None:
                        find_btn.props(add="loading")
                        net_msg.set_text("Recherche des scanners du réseau…")
                        try:
                            res = await run.io_bound(scan.discover, 3.0)
                        finally:
                            find_btn.props(remove="loading")
                        for r in res:
                            scan.remember(r["name"], r["base"])  # mémorisé : il sera là à la prochaine ouverture, sans nouvelle recherche
                        options = {s_["base"]: s_["name"] for s_ in scan.known()}
                        keep = o["found"] if o["found"] in options else (res[0]["base"] if res else (next(iter(options), None)))
                        found.set_options(options, value=keep)
                        net_msg.set_text(f"{len(res)} scanner(s) trouvé(s)." if res else
                                         "Aucun scanner détecté pour l'instant : les scanners déjà utilisés restent dans la liste." if options else
                                         "Aucun scanner détecté. Saisissez son adresse (affichée sur son écran ou sa page de configuration), "
                                         "ou utilisez le téléphone. Sur Mac, autorisez l'accès au réseau local si le système le demande.")

                    def forget() -> None:
                        if o["found"]:
                            scan.forget(o["found"])
                            options = {s_["base"]: s_["name"] for s_ in scan.known()}
                            found.set_options(options, value=next(iter(options), None))

                    forget_btn.on_click(forget)

                    async def go() -> None:
                        try:
                            base = scan.normalize_base(o["addr"]) if o["addr"].strip() else (o["found"] or "")
                            if not base:
                                return ui.notify("Cherchez un scanner ou saisissez son adresse.", type="warning")
                            go_btn.props(add="loading disable")
                            net_msg.set_text("Numérisation en cours…")
                            raws = await run.io_bound(scan.scan, base, o["source"], o["mode"], o["dpi"])
                        except scan.ScanError as e:
                            net_msg.set_text("")
                            return ui.notify(str(e), type="negative", multi_line=True)
                        finally:
                            go_btn.props(remove="loading disable")
                        scan.remember(next((s_["name"] for s_ in scan.known() if s_["base"] == base), ""), base, used=True)
                        if base not in found.options:
                            found.set_options({s_["base"]: s_["name"] for s_ in scan.known()}, value=base)
                        await add_pages(raws, False)
                        net_msg.set_text(f"{len(raws)} page(s) ajoutée(s). Posez la suivante et recommencez si besoin.")
                        blanks = sum(1 for r in raws if await run.io_bound(scan.is_blank, r))
                        if blanks:
                            ui.notify(f"{blanks} page(s) semblent vides. Le document est peut-être dans le chargeur : choisissez la source "
                                      "« Chargeur automatique », ou posez-le sur la vitre.", type="warning", multi_line=True, timeout=8000)

                    find_btn.on_click(find)
                    go_btn.on_click(go)
                    ui.timer(0.3, find, once=True)  # recherche automatique à l'ouverture : les scanners déjà connus sont déjà dans la liste
                with ui.tab_panel(t_ph).classes("q-pa-sm"):
                    ui.label("Photographiez vos pages avec le téléphone : elles arrivent ici, recadrées. Aucun service en ligne : "
                             "le téléphone parle directement à cet ordinateur sur le réseau local (même Wi-Fi).").classes("rmeta")
                    ph_box = ui.column().classes("w-full items-center gap-1 q-mt-sm")
                    ph_btn = ui.button("Afficher le QR code", icon="qr_code_2").props("unelevated no-caps color=primary")

                    def start_phone() -> None:
                        if st["server"]:
                            return
                        try:
                            srv = scan.PhoneServer()
                            url = srv.url()
                            svg = scan.qr_svg(url)
                        except Exception as e:
                            log.exception("serveur téléphone")
                            return ui.notify(f"Impossible d'ouvrir l'accès réseau : {e}", type="negative", multi_line=True)
                        st["server"] = srv
                        ph_btn.set_visibility(False)
                        with ph_box:
                            ui.html(svg.replace("<svg ", '<svg width="220" height="220" ', 1), sanitize=False).style("width:220px;height:220px;flex:none;margin-bottom:10px")
                            ui.label("Scannez ce code avec l'appareil photo du téléphone, puis ouvrez le lien.").classes("rmeta")
                            ui.label(url).classes("rmeta").style("word-break:break-all;user-select:text")
                            ui.label("Windows peut demander d'autoriser l'accès réseau : acceptez pour les réseaux privés.").classes("rmeta")
                        poll.activate()

                    async def poll_phone() -> None:
                        srv = st["server"]
                        if not srv:
                            return
                        with srv.session.lock:
                            new = srv.session.pages[st["seen"]:]
                            st["seen"] += len(new)
                        if new:
                            await add_pages(new, True)
                        if srv.session.done and not st.get("said"):
                            st["said"] = True
                            ui.notify("Le téléphone a terminé l'envoi.", type="positive", timeout=2000)

                    ph_btn.on_click(start_phone)
                    poll = ui.timer(1.0, poll_phone, active=False)
            ui.separator()

            @ui.refreshable
            def pages_view() -> None:
                if not pages:
                    ui.label("Aucune page pour l'instant.").classes("rmeta")
                    return
                ui.label(f"{len(pages)} page(s)").classes("rmeta")
                with ui.row().classes("w-full gap-3").style("flex-wrap:wrap"):
                    for i, p in enumerate(pages):
                        with ui.column().classes("items-center gap-0"):
                            ui.image(p["thumb"]).style("width:96px;border:1px solid var(--line);border-radius:6px;background:#fff")
                            with ui.row().classes("items-center gap-0 no-wrap"):
                                ui.button(icon="chevron_left", on_click=lambda _, i=i: move(i, -1)).props("flat dense round size=xs").set_enabled(i > 0)
                                ui.label(str(i + 1)).classes("rmeta")
                                ui.button(icon="chevron_right", on_click=lambda _, i=i: move(i, 1)).props("flat dense round size=xs").set_enabled(i < len(pages) - 1)
                                ui.button(icon="close", on_click=lambda _, i=i: drop(i)).props("flat dense round size=xs color=negative")

            def move(i: int, d: int) -> None:
                pages[i], pages[i + d] = pages[i + d], pages[i]
                pages_view.refresh()

            def drop(i: int) -> None:
                pages.pop(i)
                pages_view.refresh()

            pages_view()
            with ui.row().classes("w-full items-center no-wrap gap-2 q-mt-sm"):
                ui.select({"color": "Couleur", "gray": "Niveaux de gris", "bw": "Noir et blanc"}, label="Rendu").bind_value(o, "mode").props(
                    "outlined dense").style("min-width:170px")
                ui.select({150: "Légère (150 dpi)", 200: "Standard (200 dpi)", 300: "Haute (300 dpi)"}, label="Qualité").bind_value(o, "dpi").props(
                    "outlined dense").style("min-width:170px")
                ui.input("Nom du fichier").bind_value(o, "name").props("outlined dense").classes("col")

            async def attach() -> None:
                if not pages:
                    return ui.notify("Ajoutez d'abord au moins une page.", type="warning")
                add_btn.props(add="loading disable")
                try:
                    pdf = await run.io_bound(scan.build_pdf, [p["raw"] for p in pages], o["mode"], o["dpi"], [p["enh"] for p in pages])
                except scan.ScanError as e:
                    add_btn.props(remove="loading disable")
                    return ui.notify(str(e), type="negative", multi_line=True)
                name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", (o["name"] or "").strip()) or "Scan"
                if not name.lower().endswith(".pdf"):
                    name += ".pdf"
                self.draft["files"].append((name, pdf, "application/pdf"))
                self.files_view.refresh()
                dlg.close()
                ui.notify(f"« {name} » ajouté au message ({human_size(len(pdf))})", type="positive")

            with ui.row().classes("w-full justify-end q-mt-sm"):
                ui.button("Annuler", on_click=dlg.close).props("flat no-caps")
                add_btn = ui.button("Ajouter au message", icon="attach_file", on_click=attach).props("unelevated no-caps color=primary")
        dlg.on("hide", stop_server)
        dlg.open()
