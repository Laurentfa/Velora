"""Pièces jointes : aperçu, résumé IA, téléchargement, ajout au message, images et signature dans le texte."""
import base64
import json
import os
import re
from pathlib import Path

from nicegui import app, run, ui

from mailia import ai, aipolicy, compose, config, htmlmail, preview, security, sync

from mailia.ui_common import SERVER_MODE, can_print, human_size, log, open_to_print, reveal


class FilesMixin:
    def insert_signature(self) -> None:
        acct = self.draft["account"] if self.new else (self.cur or {}).get("account", "")
        ui.run_javascript(f"mailiaInsert({json.dumps(self.editor.html_id)}, {json.dumps(compose.sig_block(acct) or '')})")

    async def insert_image(self) -> None:
        """Choisit une image, la réduit si besoin et l'insère dans le corps du message (elle partira en image incorporée)."""
        eid = self.editor.html_id
        with self.host, ui.dialog() as dlg, ui.card().style("width:560px;max-width:96vw"):
            ui.label("Insérer une image").classes("text-h6")

            async def on_img(e) -> None:
                data = await e.file.read()
                try:
                    from io import BytesIO
                    from PIL import Image
                    im = Image.open(BytesIO(data))
                    im.thumbnail((1100, 1100))
                    buf, fmt = BytesIO(), ("PNG" if im.mode in ("RGBA", "P", "LA") else "JPEG")
                    im.convert("RGBA" if fmt == "PNG" else "RGB").save(buf, fmt, **({} if fmt == "PNG" else {"quality": 84}))
                    data, mime = buf.getvalue(), f"image/{fmt.lower()}"
                except Exception:
                    mime = e.file.content_type or "image/png"
                if len(data) > 3_000_000:
                    return ui.notify("Image trop volumineuse (3 Mo maximum).", type="warning")
                uri = f"data:{mime};base64,{base64.b64encode(data).decode()}"
                ui.run_javascript(f"mailiaInsert({json.dumps(eid)}, {json.dumps(f'<img src={chr(34)}{uri}{chr(34)} style={chr(34)}max-width:100%{chr(34)}>')})")
                dlg.close()

            ui.upload(on_upload=on_img, auto_upload=True, label="Choisir une image (PNG, JPEG, GIF)").props(
                "accept=image/* flat bordered dense hide-upload-btn").classes("w-full")
            ui.label("L'image est réduite si besoin puis incorporée au message.").classes("text-caption text-grey")
            ui.button("Annuler", on_click=dlg.close).props("flat no-caps")
        dlg.open()

    @ui.refreshable_method
    def files_view(self) -> None:
        for f in self.draft["files"]:
            with ui.row().classes("items-center gap-1 no-wrap w-full").style("min-width:0"):
                ui.icon("attach_file", size="18px").classes("shrink-0")
                ui.label(f"{f[0]} · {human_size(len(f[1]))}").classes("rmeta ellipsis").style("flex:1 1 0;min-width:0").tooltip(f[0])
                if self.preview_kind(f[0]):
                    ui.button(icon="visibility", on_click=lambda _, f=f: self.preview_draft(f)).props("flat dense round size=xs").classes("shrink-0").tooltip(
                        "Aperçu (et résumé IA à la demande) avant l'envoi")
                ui.button(icon="close", on_click=lambda _, f=f: self.drop_file(f)).props("flat dense round size=xs").classes("shrink-0")

    async def on_upload(self, e) -> None:
        data = await e.file.read()
        self.draft["files"].append((e.file.name, data, e.file.content_type or "application/octet-stream"))
        e.sender.reset()
        self.files_view.refresh()

    def drop_file(self, f) -> None:
        self.draft["files"].remove(f)
        self.files_view.refresh()

    async def download(self, att: dict, msg: dict | None = None) -> None:
        why = security.risky_attachment(att.get("name", ""))
        if why:  # fichier qui s'exécute, double extension, macros, page web : confirmation avant de l'enregistrer
            with self.host, ui.dialog() as dlg, ui.card().style("width:480px;max-width:96vw"):
                ui.label("Ce fichier est dangereux").classes("text-h6")
                ui.label(why).classes("text-body2")
                ui.label("L'enregistrer ne l'exécute pas, mais ne l'ouvrez pas sans être certain de son origine.").classes("text-caption text-grey")
                with ui.row().classes("justify-end w-full gap-2"):
                    ui.button("Annuler", on_click=lambda: dlg.submit(False)).props("flat no-caps")
                    ui.button("Enregistrer quand même", on_click=lambda: dlg.submit(True)).props("unelevated no-caps color=negative")
            if not await dlg:
                return
        m = msg or self.cur
        try:
            data, name = await run.io_bound(sync.fetch_attachment, m, att)
        except Exception as e:
            return ui.notify(f"Téléchargement impossible : {e}", type="negative", multi_line=True)
        self._save_download(name, data)

    def _save_download(self, name: str, data: bytes) -> None:
        name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", os.path.basename(name)).strip(". ") or "piece-jointe"
        if SERVER_MODE:  # navigateur / téléphone : téléchargement classique
            ui.download.content(data, name)
            return
        folder = Path.home() / "Downloads"
        folder.mkdir(exist_ok=True)
        path, n = folder / name, 1
        while path.exists():
            path = folder / f"{Path(name).stem} ({n}){Path(name).suffix}"
            n += 1
        path.write_bytes(data)
        ui.notify(f"Enregistré dans Téléchargements : {path.name}", type="positive")
        reveal(path)

    PREVIEW = {"image": {"png", "jpg", "jpeg", "gif", "webp", "bmp"}, "pdf": {"pdf"},
               "rich": {"docx", "docm", "xlsx", "xlsm", "pptx", "pptm", "heic", "heif", "svg", "zip"},
               "text": {"txt", "csv", "log", "md", "json", "xml", "ics", "eml", "tsv"}}

    @classmethod
    def preview_kind(cls, name: str) -> str | None:
        ext = (name or "").rsplit(".", 1)[-1].lower() if "." in (name or "") else ""
        return next((k for k, exts in cls.PREVIEW.items() if ext in exts), None)

    async def preview(self, att: dict, msg: dict | None = None) -> None:
        """Aperçu d'une pièce jointe reçue (lue sur le serveur) sans l'enregistrer sur le disque de l'utilisateur."""
        m = msg or self.cur
        try:
            data, name = await run.io_bound(sync.fetch_attachment, m, att)
        except Exception as e:
            return ui.notify(f"Aperçu impossible : {e}", type="negative", multi_line=True)
        await self.show_preview(name, data, f"{m['id']}:{att['id']}", lambda: self.download(att, m), excluded=aipolicy.is_excluded(m),
                                send=lambda: self.attach_to_new(name, data, att.get("type") or "application/octet-stream"))

    async def preview_draft(self, f: tuple) -> None:
        """Aperçu d'une pièce jointe du message en cours de rédaction (un scan, par exemple) : aperçu et résumé IA avant l'envoi."""
        import hashlib
        name, data = f[0], f[1]
        await self.show_preview(name, data, "draft:" + hashlib.sha1(data).hexdigest()[:20], lambda: self._save_download(name, data))

    async def attach_to_new(self, name: str, data: bytes, mime: str) -> None:
        """Nouveau message avec cette pièce jointe déjà jointe (depuis l'aperçu). Le brouillon en cours est d'abord enregistré."""
        if self.compose or self.new:
            await self._save_draft_now()
        self.new_message()
        if not self.new:
            return
        self.draft["files"].append((name, data, mime))
        self.files_view.refresh()
        self.reader.refresh()

    async def show_preview(self, name: str, data: bytes, key: str, download, excluded: bool = False, send=None) -> None:
        import shutil
        import tempfile
        kind = self.preview_kind(name)
        rich, rich_error = None, ""
        if kind == "rich":  # Word, Excel, PowerPoint, HEIC, SVG, ZIP : converti en local en image ou en page HTML
            try:
                rich = await run.io_bound(preview.render, name, data)
            except preview.PreviewError as e:
                rich_error = str(e)
            except Exception:
                log.exception("aperçu de %s", name)
                rich_error = "L'aperçu de ce fichier a échoué. Vous pouvez le télécharger pour l'ouvrir."
            if rich and rich["type"] == "image":
                name_view, data_view = f"apercu.{rich['ext']}", rich["data"]
            else:
                name_view, data_view = name, data
        else:
            name_view, data_view = name, data
        tmp = Path(tempfile.mkdtemp(prefix="mailia-apercu-"))
        shown = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", name_view).strip(". ") or "apercu"
        (tmp / shown).write_bytes(data_view)
        url = app.add_static_file(local_file=tmp / shown, max_cache_age=0) if (kind in ("image", "pdf") or (rich and rich["type"] == "image")) else ""
        with self.host, ui.dialog() as dlg, ui.card().classes("no-wrap q-pa-none").style(
                "width:min(1100px,96vw);max-width:96vw;height:min(90vh,900px)"):
            with ui.row().classes("w-full items-center no-wrap q-px-md q-py-sm gap-2").style("border-bottom:1px solid var(--line)"):
                ui.icon("attach_file", size="20px").style("color:var(--accent)")
                ui.label(f"{name} · {human_size(len(data))}").style("font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap").classes("col")
                z_out = ui.button(icon="zoom_out").props("flat dense round size=sm").tooltip("Zoom arrière")
                zoom_lbl = ui.label("100 %").classes("cursor-pointer").style("min-width:44px;text-align:center;font-size:13px;color:var(--muted)").tooltip(
                    "Cliquez pour revenir à 100 %")
                z_in = ui.button(icon="zoom_in").props("flat dense round size=sm").tooltip("Zoom avant")
                sum_btn = None
                is_photo = kind == "image" or preview.kind_of(name) == "heic"
                if config.ai_enabled() and not excluded and (kind in ("pdf", "text") or is_photo or (preview.kind_of(name) in ("word", "excel", "powerpoint"))):
                    sum_btn = ui.button("Résumer", icon="auto_awesome").props("unelevated no-caps color=primary size=sm").tooltip(
                        "Résumé par l'IA. Le texte du fichier est envoyé à votre fournisseur d'IA (une image, pour une photo ou un document numérisé : "
                        "6 pages au plus). Quelques centimes au plus, une seule fois par fichier.")
                if not SERVER_MODE and can_print(name):
                    async def print_it() -> None:
                        try:
                            await run.io_bound(open_to_print, name, data)
                        except Exception as e:
                            return ui.notify(f"Impression impossible : {e}", type="negative", multi_line=True)
                        ui.notify("Fichier ouvert : imprimez avec ⌘P (Ctrl+P sous Windows).", type="positive")
                    ui.button("Imprimer", icon="print", on_click=print_it).props("flat dense no-caps color=primary").tooltip(
                        "Ouvre le fichier dans l'application du système (Aperçu, Word…) pour l'imprimer avec vos réglages d'imprimante.")
                if send:
                    async def send_it() -> None:
                        dlg.close()
                        await send()
                    ui.button("Envoyer", icon="forward_to_inbox", on_click=send_it).props("flat dense no-caps color=primary").tooltip(
                        "Écrire un nouveau mail avec cette pièce jointe déjà jointe")
                ui.button("Télécharger", icon="download", on_click=download).props("flat dense no-caps color=primary")
                ui.button(icon="close", on_click=dlg.close).props("flat round dense")
            sum_box = ui.element("div").style(
                "display:none;width:100%;padding:12px 18px;background:var(--ai-soft);border-bottom:1px solid var(--line);max-height:34%;overflow:auto;user-select:text;-webkit-user-select:text")

            async def summarize() -> None:
                sum_btn.props(add="loading disable")
                try:
                    text = "" if is_photo else await run.io_bound(preview.extract_text, name, data)
                    if is_photo or (kind == "pdf" and len(text.strip()) < 40):  # photo ou PDF numérisé : sans texte, l'IA lit les images
                        images, cut = await run.io_bound(preview.scan_images, name, data)
                        out = await run.io_bound(ai.summarize_scan, key, name, images, cut)
                    else:
                        out = await run.io_bound(ai.summarize_attachment, key, name, text)
                except (preview.PreviewError, ai.AIError) as e:
                    sum_btn.props(remove="loading disable")
                    return ui.notify(str(e), type="warning", multi_line=True)
                except Exception as e:
                    log.exception("résumé de %s", name)
                    sum_btn.props(remove="loading disable")
                    return ui.notify(f"Résumé impossible : {e}", type="negative", multi_line=True)
                sum_btn.set_visibility(False)
                sum_box.clear()
                sum_box.style("display:block")
                with sum_box:
                    with ui.row().classes("w-full items-center no-wrap"):
                        ui.label("✦ RÉSUMÉ IA").classes("rmeta col").style("font-weight:700;color:var(--ai);letter-spacing:.06em")
                        plain_out = re.sub(r"(?m)^[ \t]*[-•·*][ \t]+", "• ", out).replace("**", "")

                        def copy_summary() -> None:
                            ui.clipboard.write(plain_out)
                            ui.notify("Résumé copié", type="positive", timeout=1500)
                        ui.button("Copier", icon="content_copy", on_click=copy_summary).props("flat dense no-caps size=sm color=primary")
                    ui.markdown(re.sub(r"(?m)^([^\n-][^\n]*)\n(- )", r"\1\n\n\2", re.sub(r"(?m)^[ \t]*[•·][ \t]*", "- ", out))).style("font-size:14px")

            if sum_btn:
                sum_btn.on_click(summarize)
            target, target_kind = None, ""
            with ui.element("div").classes("col").style("min-height:0;width:100%;overflow:auto;background:var(--surface2)"):
                if rich_error:
                    ui.label(rich_error).style("padding:24px;color:var(--muted)")
                elif rich and rich["type"] == "html":
                    clean = htmlmail.sanitize(rich["html"], preview.MAX_HTML)
                    frame = target = ui.element("iframe").classes("pvframe")  # suit le thème de l'appli (voir le CSS)
                    target_kind = "frame"
                    frame._props.update({"sandbox": "", "srcdoc": htmlmail.build_doc(clean, False)})  # sandbox vide : aucun script
                elif kind == "image" or (rich and rich["type"] == "image"):
                    target = ui.element("img").props(f'src="{url}"').style("display:block;max-width:100%;max-height:100%;margin:0 auto;object-fit:contain")
                    target_kind = "img"
                elif kind == "pdf":
                    target = ui.element("iframe").props(f'src="{url}"').style("width:100%;height:100%;border:0;min-height:600px")
                    target_kind = "frame"
                elif name.lower().endswith((".csv", ".tsv")) and (tbl := preview.csv_table(data)):
                    html_tbl, n_total, n_shown = tbl
                    target, target_kind = ui.html(html_tbl, sanitize=False), "text"
                    if n_total > n_shown:
                        ui.label(f"{n_shown} lignes affichées sur {n_total}. Téléchargez le fichier pour tout voir.").style(
                            "padding:10px 16px;color:var(--muted);font-size:13px")
                else:
                    text = data[:300_000].decode("utf-8", errors="replace")
                    target_kind = "text"
                    target = ui.label(text + ("\n\n… (aperçu tronqué)" if len(data) > 300_000 else "")).style(
                        "white-space:pre-wrap;font-family:var(--font-mono,monospace);font-size:13px;padding:16px")
        zoom = {"v": 1.0}

        def set_zoom(v: float) -> None:
            zoom["v"] = max(0.25, min(5.0, round(v, 2)))
            z = zoom["v"]
            zoom_lbl.set_text(f"{round(z * 100)} %")
            if target is None:
                return
            if target_kind == "img":
                target.style("max-width:100%;max-height:100%;width:auto;height:auto;object-fit:contain" if z == 1 else
                             f"max-width:none;max-height:none;width:{z * 100}%;height:auto")
            elif target_kind == "frame":  # la page est agrandie dans son cadre : la zone de défilement reste celle de la fenêtre
                target.style(f"transform:scale({z});transform-origin:0 0;width:{100 / z}%;height:{100 / z}%;min-height:{600 / z}px")
            else:
                target.style(f"zoom:{z}")
        z_in.on_click(lambda: set_zoom(zoom["v"] + (0.25 if zoom["v"] < 2 else 0.5)))
        z_out.on_click(lambda: set_zoom(zoom["v"] - (0.25 if zoom["v"] <= 2 else 0.5)))
        zoom_lbl.on("click", lambda: set_zoom(1.0))
        dlg.on("hide", lambda: shutil.rmtree(tmp, ignore_errors=True))
        dlg.open()
