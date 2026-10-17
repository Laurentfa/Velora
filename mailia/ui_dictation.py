"""Dictée vocale dans la rédaction : un bouton micro pour le corps du message et un pour la consigne donnée à l'IA.

Le texte s'affiche en provisoire pendant qu'on parle, puis s'écrit à l'endroit du curseur à chaque phrase terminée. Rien ne part sans votre clic sur « Envoyer »."""
import json
import queue
import threading

from nicegui import run, ui

from mailia import dictation, whisper_engine
from mailia.ui_common import log


async def whisper_download_dialog(host, key: str) -> bool:
    """Demande l'accord pour télécharger le modèle Whisper, montre la progression, permet d'annuler. Vrai si le modèle est en place à la fin."""
    m = whisper_engine.MODELS[key]
    state = {"done": 0}
    cancel = threading.Event()
    with host, ui.dialog().props("persistent") as dlg, ui.card().style("width:min(520px,92vw)"):
        ui.label("Télécharger le modèle de dictée").classes("text-h6")
        ui.label(f"Modèle « {m.title} » ({whisper_engine.human_mb(m.size)}), depuis le dépôt public de whisper.cpp sur huggingface.co. "
                 "Il reste sur cet ordinateur : ensuite, la dictée fonctionne sans connexion et sans l'IA. Le fichier est vérifié avant d'être utilisé.").classes("text-body2")
        bar = ui.linear_progress(value=0, show_value=False).classes("w-full").mark("dictate-progress")
        bar.set_visibility(False)
        info = ui.label("").classes("rmeta")
        with ui.row().classes("justify-end w-full gap-2"):
            ui.button("Annuler", on_click=lambda: (cancel.set(), dlg.submit(False))).props("flat no-caps").mark("dictate-cancel")
            go_btn = ui.button("Télécharger").props("unelevated no-caps color=primary").mark("dictate-download")

        def refresh() -> None:
            bar.set_value(min(1.0, state["done"] / m.size))
            info.set_text(f"{whisper_engine.human_mb(state['done'])} / {whisper_engine.human_mb(m.size)}" if state["done"] else info.text)

        timer = ui.timer(0.3, refresh)

        async def go() -> None:
            go_btn.disable()
            bar.set_visibility(True)
            info.set_text("Téléchargement…")
            try:
                await run.io_bound(whisper_engine.download, key, lambda d, t: state.update(done=d), cancel)
            except whisper_engine.DownloadError as e:
                info.set_text(str(e))
                go_btn.set_text("Réessayer")
                go_btn.enable()
                return
            dlg.submit(True)

        go_btn.on_click(go)
    ok = bool(await dlg)
    timer.cancel()
    dlg.delete()
    return ok


class DictationMixin:
    dict_state: dict | None = None

    def dictation_widget(self, target: str) -> None:
        """Le bouton micro (« body » : le message, « ins » : la consigne de l'IA) et sa ligne d'état. Absent quand la dictée n'est pas possible sur cette machine."""
        if not dictation.usable():
            return
        btn = ui.button(icon="mic", on_click=lambda t=target: self.toggle_dictation(t)).props("flat dense round size=sm color=primary").tooltip(
            "Dicter : un clic pour parler, un clic pour arrêter").mark(f"dictate-{target}")
        status = ui.label("").classes("rmeta").style("max-width:340px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-style:italic").mark(f"dictate-status-{target}")
        self.dict_widgets = getattr(self, "dict_widgets", {})
        self.dict_widgets[target] = (btn, status)

    def _dict_paint(self, target: str, recording: bool, text: str = "") -> None:
        btn, status = getattr(self, "dict_widgets", {}).get(target, (None, None))
        try:
            if btn is not None:
                btn.props(f"icon={'stop' if recording else 'mic'} color={'negative' if recording else 'primary'}")
                btn.update()
            if status is not None:
                status.set_text(text)
        except Exception:  # le champ a pu être redessiné entre-temps
            pass

    async def toggle_dictation(self, target: str) -> None:
        state = self.dict_state
        if state:
            if state["target"] == target and not state.get("stopping"):
                state["stopping"] = True
                state["session"].stop()
                self._dict_paint(target, True, "Fin de la dictée…")
            return
        info = await run.io_bound(dictation.check)
        if not info.get("available"):
            return ui.notify(dictation.message("unavailable"), type="warning")
        if not info.get("installed") and not await self._dictation_install():
            return
        session = dictation.new_session()
        session.start()
        self.dict_state = {"session": session, "target": target, "timer": ui.timer(0.2, self._dictation_pump)}
        self._dict_paint(target, True, "Démarrage…")

    async def _dictation_install(self) -> bool:
        if dictation.engine() == "whisper":
            return await whisper_download_dialog(self.host, whisper_engine.chosen())
        with self.host, ui.dialog() as dlg, ui.card().style("width:min(460px,92vw)"):
            ui.label("Installer la langue de dictée").classes("text-h6")
            ui.label("macOS va télécharger le modèle de reconnaissance du français. Il reste sur ce Mac, et la dictée fonctionne ensuite sans connexion.").classes("text-body2")
            with ui.row().classes("justify-end w-full gap-2"):
                ui.button("Annuler", on_click=lambda: dlg.submit(False)).props("flat no-caps")
                ui.button("Installer", on_click=lambda: dlg.submit(True)).props("unelevated no-caps color=primary").mark("dictate-install")
        if not await dlg:
            return False
        ui.notify("Installation de la langue de dictée…")
        err = await run.io_bound(dictation.install_language)
        if err:
            ui.notify(err, type="negative")
            return False
        return True

    def _dictation_pump(self) -> None:
        state = self.dict_state
        if not state:
            return
        target, session = state["target"], state["session"]
        if not (self.new or self.compose):  # on a quitté la rédaction
            return self._dictation_end(kill=True)
        while True:
            try:
                ev = session.events.get_nowait()
            except queue.Empty:
                break
            kind = ev.get("t")
            if kind == "loading":
                self._dict_paint(target, True, "Chargement du modèle…")
            elif kind == "ready":
                self._dict_paint(target, True, "J'écoute… (clic sur le micro pour arrêter)")
            elif kind == "partial":
                self._dictation_live(target, ev.get("text") or "", False)
                self._dict_paint(target, True, "J'écoute…")
            elif kind == "final":
                self._dictation_live(target, ev.get("text") or "", True)
                self._dict_paint(target, True, "" if state.get("stopping") else "J'écoute…")
            elif kind == "error":
                log.warning("dictée : %s %s", ev.get("code"), ev.get("msg"))
                ui.notify(dictation.message(ev.get("code", ""), ev.get("msg", "")), type="negative", multi_line=True)
                return self._dictation_end()
            elif kind == "done":
                return self._dictation_end()

    def _dictation_end(self, kill: bool = False) -> None:
        state, self.dict_state = self.dict_state, None
        if not state:
            return
        state["timer"].cancel()
        if state["target"] == "body":
            self._dictation_live("body", "", True)  # un texte provisoire resté en gris devient du texte normal
        if kill:
            state["session"].kill()
        self._dict_paint(state["target"], False, "")

    def _dictation_live(self, target: str, text: str, final: bool) -> None:
        """Texte reconnu : provisoire (gris, remplacé à chaque mise à jour) tant qu'il n'est pas `final`, puis écrit pour de bon."""
        text = text.strip()
        if target == "ins":
            state = self.dict_state or {}
            base = state.setdefault("base", self.draft.get("ins", ""))
            self.draft["ins"] = (base + " " + text).strip()
            if final:
                state["base"] = self.draft["ins"]
        else:
            ui.run_javascript(f"mailiaLive({json.dumps(self.editor.html_id)}, {json.dumps(text)}, {'true' if final else 'false'})")
