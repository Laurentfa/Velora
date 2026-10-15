"""« Nettoyer ma boîte » : expéditeurs les plus bavards, archivage groupé, désabonnement."""
import json
import threading

from nicegui import run, ui

from mailia import cleanup, sync

from mailia.ui_common import initials, log, short_date


class CleanupMixin:
    async def cleanup_dialog(self) -> None:
        """Expéditeurs qui remplissent la boîte de réception : tout archiver ou se désabonner, en un clic. Rien n'est supprimé."""
        opts = {"read": 40, "min": 3}
        with self.host, ui.dialog() as dlg, ui.card().classes("no-wrap").style("width:940px;max-width:96vw;height:min(88vh,820px)"):
            with ui.row().classes("w-full items-center no-wrap"):
                ui.label("Nettoyer ma boîte").classes("text-h6 col")
                ui.button(icon="close", on_click=dlg.close).props("flat round dense")
            ui.label("Les expéditeurs qui vous écrivent le plus. « Archiver » déplace tous leurs mails de la boîte de réception vers l'archive "
                     "(rien n'est supprimé, tout reste retrouvable).").classes("rmeta")
            with ui.row().classes("w-full items-center gap-3 q-mt-xs"):
                ui.select({100: "Tous les expéditeurs", 60: "Que je lis à moins de 60 %", 40: "Que je lis à moins de 40 %", 20: "Que je lis à moins de 20 %",
                           5: "Que je ne lis presque jamais"}, value=opts["read"], label="Afficher", on_change=lambda e: (opts.update(read=e.value), listing.refresh())
                          ).props("outlined dense").style("min-width:280px")
                ui.select({2: "2 mails ou plus", 3: "3 mails ou plus", 10: "10 mails ou plus", 30: "30 mails ou plus"}, value=opts["min"], label="Au moins",
                          on_change=lambda e: (opts.update(min=e.value), listing.refresh())).props("outlined dense").style("min-width:180px")
            box = ui.scroll_area().classes("col w-full").style("min-height:0")
            with box:
                @ui.refreshable
                def listing() -> None:
                    stats = cleanup.sender_stats(opts["min"], opts["read"])
                    if not stats:
                        ui.label("Rien à nettoyer avec ces critères.").classes("rmeta q-pa-md")
                    for s in stats:
                        with ui.row().classes("w-full items-center no-wrap gap-3 q-py-sm").style("border-bottom:1px solid var(--line)"):
                            ui.avatar(initials(s["name"]), color="blue-1", text_color="blue-9", size="36px")
                            with ui.column().classes("col gap-0").style("min-width:0"):
                                ui.label(s["name"]).style("font-weight:600").classes("ellipsis")
                                ui.label(s["sender"]).classes("rmeta ellipsis")
                                ui.label(f"{s['n']} mails · {s['n_read']} lus ({s['read_pct']} %) · dernier {short_date(s['last'])}").classes("rmeta")
                            if s["noise"]:
                                ui.badge("newsletter / notification", color="grey-4", text_color="grey-9").props("outline=false")
                            st = ui.label("").classes("rmeta")
                            ui.button("Archiver tout", icon="archive", on_click=lambda _, s=s: do_archive(s, st)).props("flat dense no-caps color=primary")
                            b2 = ui.button("Se désabonner", icon="unsubscribe", on_click=lambda _, s=s: do_unsub(s)).props("flat dense no-caps color=primary")
                            if not s["noise"] and not s["can_unsub"]:
                                b2.set_visibility(False)
                            ui.button(icon="visibility_off", on_click=lambda _, s=s: (cleanup.ignore(s["sender"]), listing.refresh())).props(
                                "flat dense round size=sm").tooltip("Ne plus me proposer cet expéditeur")

                async def do_archive(s: dict, st) -> None:
                    with self.host, ui.dialog() as cdlg, ui.card().style("width:480px;max-width:96vw"):
                        ui.label(f"Archiver les {s['n']} mails de {s['name']} ?").classes("text-h6")
                        ui.label("Ils quittent la boîte de réception et vont dans le dossier d'archive. Vous pourrez toujours les retrouver.").classes("text-body2")
                        with ui.row().classes("justify-end w-full gap-2"):
                            ui.button("Annuler", on_click=lambda: cdlg.submit(False)).props("flat no-caps")
                            ui.button("Archiver", on_click=lambda: cdlg.submit(True)).props("unelevated no-caps color=primary")
                    if not await cdlg:
                        return
                    prog, stop = {"i": 0, "n": s["n"]}, threading.Event()
                    st.set_text("Archivage…")
                    stop_btn = ui.button("Arrêter", icon="stop", on_click=stop.set).props("flat dense no-caps size=sm color=negative")
                    timer = ui.timer(0.4, lambda: st.set_text(f"{prog['i']} / {prog['n']}"))
                    try:
                        done, failed = await run.io_bound(cleanup.archive_sender, s["sender"], lambda i, n: prog.update(i=i, n=n), stop)
                    except Exception as e:
                        st.set_text("")
                        return ui.notify(f"Archivage impossible : {e}", type="negative", multi_line=True)
                    finally:
                        timer.cancel()
                        stop_btn.delete()
                    ui.notify(f"{done} mail(s) archivé(s)" + (f", {failed} en échec" if failed else ""), type="positive" if not failed else "warning")
                    await self.reload(force=True)
                    listing.refresh()

                async def do_unsub(s: dict) -> None:
                    rows = cleanup.inbox_rows(s["sender"])
                    msg = next((r for r in rows if r.get("unsub")), None)
                    if not msg and rows:
                        try:
                            await run.io_bound(sync.ensure_details, rows[0])
                        except Exception:
                            log.debug("détails du mail indisponibles pour le désabonnement", exc_info=True)
                        msg = next((r for r in cleanup.inbox_rows(s["sender"]) if r.get("unsub")), None)
                    if not msg:
                        return ui.notify("Cet expéditeur n'indique aucun moyen de se désabonner.", type="warning")
                    await self.unsubscribe(msg, json.loads(msg["unsub"]))

                listing()
        dlg.open()
