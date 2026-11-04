"""Fenêtre native : réglages propres à la fenêtre (menu clic droit, barre de menus macOS), exécutés dans SON processus.
Ce code est dans un module à part pour que ce processus n'ait pas à recharger tout le programme (plus de 2 s au démarrage sous Windows)."""
import json
import logging
import sys
import threading
import time
from pathlib import Path

from . import db, i18n, perf

log = logging.getLogger("mailia")


def _tidy_mac_menus(cocoa) -> None:
    """Barre de menus macOS réduite à l'essentiel : le menu de l'appli et « Édition » (nécessaire : c'est lui qui fait fonctionner ⌘C, ⌘V, ⌘X et ⌘A dans
    les champs de saisie). Le menu « Présentation » est retiré, ainsi que les entrées ajoutées par le système (remplissage automatique, dictée, émojis).
    Les libellés suivent la langue de l'appli."""
    try:
        from AppKit import NSUserDefaults
        from PyObjCTools import AppHelper
        ud = NSUserDefaults.standardUserDefaults()
        ud.setBool_forKey_(True, "NSDisabledDictationMenuItem")
        ud.setBool_forKey_(True, "NSDisabledCharacterPaletteMenuItem")
        ud.setBool_forKey_(False, "NSAutoFillHeuristicControllerEnabled")
        cocoa.BrowserView._add_view_menu = lambda self, main_menu: None
        fr = {"cocoa.menu.about": "À propos de", "cocoa.menu.services": "Services", "cocoa.menu.edit": "Édition", "cocoa.menu.hide": "Masquer",
              "cocoa.menu.hideOthers": "Masquer les autres", "cocoa.menu.showAll": "Tout afficher", "cocoa.menu.quit": "Quitter",
              "cocoa.menu.cut": "Couper", "cocoa.menu.copy": "Copier", "cocoa.menu.paste": "Coller", "cocoa.menu.selectAll": "Tout sélectionner"}

        def rebuild() -> None:
            inst = next(iter(cocoa.BrowserView.instances.values()))
            if i18n.language() == "fr":
                inst.localization.update(fr)
            main_menu = inst._recreate_menus(inst.menu)
            cocoa.BrowserView.app.setMainMenu_(main_menu)
            keep = {fr.get(k, v) for k, v in (("cocoa.menu.cut", "Cut"), ("cocoa.menu.copy", "Copy"), ("cocoa.menu.paste", "Paste"),
                                              ("cocoa.menu.selectAll", "Select All"))}
            keep |= {"Cut", "Copy", "Paste", "Select All"}
            _keep_title = lambda t: any(k in t for k in ("iPhone", "iPad"))  # « Importer depuis l'iPhone ou l'iPad » (Continuité) ajouté par le système
            edit = main_menu.itemAtIndex_(1).submenu() if main_menu.numberOfItems() > 1 else None

            def clean(*_args) -> None:  # le système ajoute lui-même des entrées au menu Édition (remplissage automatique…) : on ne garde que les quatre utiles
                for item in list(edit.itemArray()):
                    if str(item.title()) not in keep and not item.isSeparatorItem() and not _keep_title(str(item.title())):
                        edit.removeItem_(item)

            try:  # menu « Fichier » réduit à « Importer depuis l'iPhone ou l'iPad » : macOS y met lui-même les appareils proches (Continuité)
                from AppKit import NSMenu, NSMenuItem, NSMenuItemImportFromDeviceIdentifier
                imp = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("Importer depuis l'iPhone ou l'iPad", "importFromDevice:", "")  # action d'AppKit (NSApplication) : c'est elle qui fait remplir le sous-menu
                imp.setIdentifier_(NSMenuItemImportFromDeviceIdentifier)
                imp.setSubmenu_(NSMenu.alloc().initWithTitle_("Importer depuis l'iPhone ou l'iPad"))
                file_menu = NSMenu.alloc().initWithTitle_("Fichier")
                file_menu.addItem_(imp)
                file_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("Fichier", None, "")
                file_item.setSubmenu_(file_menu)
                main_menu.insertItem_atIndex_(file_item, 1)
                cocoa.BrowserView.app.setMainMenu_(main_menu)
                try:  # AppKit repère l'entrée d'identifiant standard dans le menu principal et la confie à Sidecar, qui y met les appareils proches
                    cocoa.BrowserView.app._customizeImportFromDeviceMenuItem()
                except Exception:  # noqa: BLE001
                    logging.getLogger("mailia").warning("Continuité : entrée non confiée à AppKit", exc_info=True)

                _import_item[:] = [imp]

                def refresh(*_args) -> None:  # dans cette fenêtre, macOS ne remplit pas seul le sous-menu à l'ouverture : on le lui demande (appareils proches)
                    sub = imp.submenu()
                    if sub is not None:
                        sub.update()

                from AppKit import NSMenuDidBeginTrackingNotification, NSNotificationCenter as _NC
                _NC.defaultCenter().addObserverForName_object_queue_usingBlock_(NSMenuDidBeginTrackingNotification, None, None, refresh)
                refresh()
            except Exception:  # noqa: BLE001
                logging.getLogger("mailia").warning("menu Fichier non ajouté", exc_info=True)
            edit = main_menu.itemAtIndex_(2).submenu() if main_menu.numberOfItems() > 2 else edit
            if edit is not None:
                from AppKit import NSMenuDidAddItemNotification, NSNotificationCenter
                clean()
                NSNotificationCenter.defaultCenter().addObserverForName_object_queue_usingBlock_(NSMenuDidAddItemNotification, edit, None, clean)

        AppHelper.callAfter(rebuild)
    except Exception:
        logging.getLogger("mailia").warning("barre de menus non simplifiée", exc_info=True)


def pages_from_pasteboard(pboard) -> list[bytes]:
    """Images reçues de l'iPhone par Continuité (PDF numérisé, PNG ou TIFF d'une photo) : une image PNG par page."""
    from AppKit import NSBitmapImageRep, NSImage, NSPasteboardTypePDF
    from Foundation import NSMakeSize
    from Quartz import PDFDocument, kPDFDisplayBoxMediaBox

    def png(img) -> bytes | None:
        rep = NSBitmapImageRep.imageRepWithData_(img.TIFFRepresentation())
        data = rep.representationUsingType_properties_(4, None) if rep else None  # 4 : NSBitmapImageFileTypePNG
        return bytes(data) if data else None

    out: list[bytes] = []
    pdf = pboard.dataForType_(NSPasteboardTypePDF)
    if pdf is not None:
        doc = PDFDocument.alloc().initWithData_(pdf)
        for i in range(doc.pageCount() if doc else 0):
            page = doc.pageAtIndex_(i)
            box = page.boundsForBox_(kPDFDisplayBoxMediaBox)
            k = 2000 / max(box.size.width, box.size.height, 1)  # ~2000 px sur le grand côté : lisible, sans excès
            img = page.thumbnailOfSize_forBox_(NSMakeSize(box.size.width * k, box.size.height * k), kPDFDisplayBoxMediaBox)
            data = png(img) if img else None
            if data:
                out.append(data)
        return out
    img = NSImage.alloc().initWithPasteboard_(pboard)  # photo (JPEG, HEIC, PNG, TIFF…) : le système sait la lire
    data = png(img) if img else None
    return [data] if data else out


def inbox_push(files: list[str]) -> None:
    """Fenêtre native -> programme principal : fichiers reçus de l'iPhone, à ajouter aux pages du scan (kv « iphone_inbox »)."""
    with db.conn() as c:
        row = c.execute("SELECT v FROM kv WHERE k='iphone_inbox'").fetchone()
        try:
            cur = json.loads(row["v"]) if row else []
        except ValueError:
            cur = []
    db.kv_set("iphone_inbox", json.dumps(cur + files))


def inbox_take() -> list[bytes]:
    """Programme principal : récupère (et vide) les images reçues de l'iPhone."""
    with db.conn() as c:
        row = c.execute("SELECT v FROM kv WHERE k='iphone_inbox'").fetchone()
    try:
        names = json.loads(row["v"]) if row and row["v"] else []
    except ValueError:
        names = []
    if not names:
        return []
    db.kv_set("iphone_inbox", "[]")
    out = []
    for n in names:
        p = Path(n)
        if p.is_file() and p.parent == _inbox_dir():
            out.append(p.read_bytes())
            p.unlink(missing_ok=True)
    return out


def _inbox_dir() -> Path:
    from . import config
    d = config.DATA_DIR / "iphone"
    d.mkdir(parents=True, exist_ok=True)
    return d


def enable_continuity(cocoa) -> None:
    """macOS : la vue de la fenêtre se déclare capable de recevoir une image (PDF, PNG, TIFF) : le système propose alors « Importer depuis l'iPhone
    ou l'iPad » (menus et clic droit). Ce qui arrive est déposé pour le programme principal (voir inbox_take)."""
    import objc
    from AppKit import NSImage
    host = cocoa.BrowserView.WebKitHost
    # PDF, PNG, JPEG, TIFF, HEIC… ce que l'iPhone peut renvoyer ; le système interroge aussi avec les anciens noms (« Apple PDF pasteboard type »…)
    wanted = {str(t) for t in NSImage.imageTypes()} | {"Apple PDF pasteboard type", "Apple PNG pasteboard type", "NeXT TIFF v4.0 pasteboard type", "com.adobe.pdf", "public.png", "public.jpeg", "public.tiff"}
    original = host.validRequestorForSendType_returnType_
    try:  # sans cette déclaration, macOS n'interroge jamais la vue pour des images (il ne demande que du texte) et le sous-menu reste vide
        from AppKit import NSApplication
        NSApplication.sharedApplication().registerServicesMenuSendTypes_returnTypes_([], sorted(wanted))
    except Exception:  # noqa: BLE001
        logging.getLogger("mailia").warning("Continuité : types non déclarés", exc_info=True)

    def valid(self, send_type, return_type):
        if send_type is None and return_type is not None and str(return_type) in wanted:
            return self
        return original(self, send_type, return_type)

    def read(self, pboard):
        try:
            import time
            files = []
            for n, png in enumerate(pages_from_pasteboard(pboard)):
                f = _inbox_dir() / f"{int(time.time() * 1000)}-{n}.png"
                f.write_bytes(png)
                files.append(str(f))
            if files:
                inbox_push(files)
            return bool(files)
        except Exception:  # noqa: BLE001
            logging.getLogger("mailia").exception("image reçue de l'iPhone")
            return False

    host.validRequestorForSendType_returnType_ = objc.selector(valid, selector=b"validRequestorForSendType:returnType:", signature=b"@@:@@")
    host.readSelectionFromPasteboard_ = objc.selector(read, selector=b"readSelectionFromPasteboard:", signature=b"Z@:@")
    try:  # le système n'accepte comme destinataire qu'une classe qui se déclare conforme à NSServicesMenuRequestor
        import ctypes
        lib = ctypes.CDLL("/usr/lib/libobjc.A.dylib")
        lib.class_addProtocol.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        lib.class_addProtocol.restype = ctypes.c_bool
        lib.objc_getClass.argtypes = [ctypes.c_char_p]
        lib.objc_getClass.restype = ctypes.c_void_p
        lib.objc_getProtocol.argtypes = [ctypes.c_char_p]
        lib.objc_getProtocol.restype = ctypes.c_void_p
        proto = lib.objc_getProtocol(b"NSServicesMenuRequestor")
        if not proto or not lib.class_addProtocol(lib.objc_getClass(host.__name__.encode()), proto):
            logging.getLogger("mailia").warning("Continuité : protocole non déclaré")
    except Exception:  # noqa: BLE001
        logging.getLogger("mailia").warning("Continuité : protocole non déclaré", exc_info=True)


_import_item: list = []  # entrée « Importer depuis l'iPhone » du menu Fichier (celle que macOS relie à la fenêtre et remplit)
_choosers: list = []  # cibles des entrées du menu du bouton (à garder en vie)


def _chooser_class():
    """Cible des entrées du menu du bouton. La classe n'est créée qu'une fois : macOS refuse qu'une classe du même nom soit déclarée deux fois."""
    import objc
    from Foundation import NSObject
    try:
        return objc.lookUpClass("VeloraIPhoneChooser")
    except objc.nosuchclass_error:
        pass

    class VeloraIPhoneChooser(NSObject):
        def choose_(self, sender):
            try:
                from webview.platforms import cocoa
                view = next(iter(cocoa.BrowserView.instances.values())).webview
                view.window().makeFirstResponder_(view)
                real = _import_item[0].submenu()
                real.update()
                real.performActionForItemAtIndex_(sender.tag())
            except Exception:  # noqa: BLE001
                log.exception("choix de l'iPhone")

    return VeloraIPhoneChooser


def show_continuity_menu() -> None:
    """Fil principal de la fenêtre : propose les choix de l'iPhone proche (« Prendre une photo », « Scanner des documents »…). Chaque entrée déclenche
    l'entrée équivalente du menu Fichier, la seule que macOS relie à la fenêtre ; l'image arrive par readSelectionFromPasteboard (voir enable_continuity)."""
    from AppKit import NSEvent, NSMenu, NSMenuItem
    from Foundation import NSDate, NSMakePoint, NSRunLoop
    from webview.platforms import cocoa
    if not _import_item:
        return
    real = _import_item[0].submenu()
    view = next(iter(cocoa.BrowserView.instances.values())).webview
    window = view.window()
    window.makeKeyAndOrderFront_(None)
    window.makeFirstResponder_(view)
    for _ in range(3):  # les appareils proches arrivent de façon asynchrone
        real.update()
        NSRunLoop.currentRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(0.3))
    real.update()

    chooser = _chooser_class().alloc().init()
    _choosers[:] = [chooser]
    menu = NSMenu.alloc().init()
    menu.setAutoenablesItems_(False)
    for i, it in enumerate(real.itemArray()):
        if it.isSeparatorItem() or not str(it.title()):
            continue
        mine = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(it.title(), "choose:" if str(it.action()) == "importFromDevice:" else None, "")
        mine.setTarget_(chooser)
        mine.setTag_(i)
        mine.setImage_(it.image())
        mine.setEnabled_(str(it.action()) == "importFromDevice:")
        menu.addItem_(mine)
    box = view.bounds().size
    event = NSEvent.mouseEventWithType_location_modifierFlags_timestamp_windowNumber_context_eventNumber_clickCount_pressure_(
        1, NSMakePoint(box.width / 2, box.height / 2), 0, 0, window.windowNumber(), None, 0, 1, 1.0)
    NSMenu.popUpContextMenu_withEvent_forView_(menu, event, view)


def watch_iphone(stop: threading.Event | None = None, every: float = 0.3, call_on_main=None) -> None:
    """Le programme principal demande le menu de l'iPhone en écrivant « iphone_request » ; on l'ouvre ici (fenêtre native)."""
    def current() -> str:
        with db.conn() as c:
            row = c.execute("SELECT v FROM kv WHERE k='iphone_request'").fetchone()
        return row["v"] if row else ""

    if call_on_main is None:
        from PyObjCTools import AppHelper
        call_on_main = AppHelper.callAfter
    done = current()  # une demande restée d'un lancement précédent ne doit pas être rejouée
    while not (stop and stop.is_set()):
        try:
            req = current()
            if req and req != done:
                done = req
                call_on_main(show_continuity_menu)
        except Exception as e:  # noqa: BLE001
            log.warning("menu de l'iPhone : %s", e)
        time.sleep(every)


def apply_zoom(factor: float) -> None:
    """Zoom de la fenêtre elle-même (comme celui d'un navigateur), sur le fil de l'interface de chaque système."""
    if sys.platform == "darwin":
        from PyObjCTools import AppHelper
        from webview.platforms import cocoa
        view = next(iter(cocoa.BrowserView.instances.values())).webview
        AppHelper.callAfter(lambda: view.setPageZoom_(factor))
    elif sys.platform.startswith("win"):
        import webview
        from System import Action
        form = webview.windows[0].native
        form.Invoke(Action(lambda: setattr(form.webview, "ZoomFactor", float(factor))))
    else:
        raise RuntimeError("zoom non pris en charge sur ce système")


def _wanted_zoom() -> float:
    with db.conn() as c:  # lecture directe : le réglage est écrit par l'autre processus (le cache de kv_get ne le verrait pas tout de suite)
        row = c.execute("SELECT v FROM kv WHERE k='ui_zoom'").fetchone()
    try:
        return float(row["v"]) if row else 1.0
    except ValueError:
        return 1.0


def watch_zoom(stop: threading.Event | None = None, every: float = 0.4) -> None:
    """Applique à la fenêtre le zoom choisi dans l'appli (réglage ou raccourci), dès qu'il change. Un échec est noté, puis réessayé : la fenêtre
    peut ne pas être prête (premières secondes), et rien ne doit jamais empêcher l'appli de fonctionner."""
    done = None
    failures = 0
    while not (stop and stop.is_set()):
        try:
            z = _wanted_zoom()
            if z != done and not (done is None and z == 1.0):  # rien à faire tant que le zoom reste à 100 % depuis le lancement
                apply_zoom(z)
                done = z
                db.kv_set("ui_zoom_status", f"ok : {round(z * 100)} %")
            elif done is None:
                done = 1.0
            failures = 0
        except Exception as e:
            failures += 1
            if failures in (1, 10):
                log.warning("zoom de la fenêtre : %s", e)
                try:
                    db.kv_set("ui_zoom_status", f"échec : {type(e).__name__} {str(e)[:120]}")
                except Exception:
                    pass
        time.sleep(every if not failures else min(3.0, every * (1 + failures)))


def print_native(path: str) -> None:
    """macOS : ouvre la boîte d'impression du système sur un PDF ou une image, dans la fenêtre de Velora (sans passer par Aperçu).
    À exécuter sur le fil principal de la fenêtre."""
    from AppKit import NSApp, NSImage, NSImageView, NSPrintInfo, NSPrintOperation
    from Foundation import NSMakeRect, NSURL
    from Quartz import PDFDocument, kPDFPrintPageScaleDownToFit
    info = NSPrintInfo.sharedPrintInfo()
    if Path(path).suffix.lower() == ".pdf":
        doc = PDFDocument.alloc().initWithURL_(NSURL.fileURLWithPath_(path))
        if doc is None:
            raise RuntimeError("PDF illisible")
        op = doc.printOperationForPrintInfo_scalingMode_autoRotate_(info, kPDFPrintPageScaleDownToFit, True)
    else:
        img = NSImage.alloc().initWithContentsOfFile_(path)
        if img is None:
            raise RuntimeError("image illisible")
        info = info.copy()  # réglages propres à cette image : la mise en page des autres impressions n'est pas touchée
        info.setHorizontalPagination_(1)  # NSFitPagination : tout sur UNE page (sinon l'image, un peu plus grande que la zone imprimable, en prend deux)
        info.setVerticalPagination_(1)
        info.setHorizontallyCentered_(True)
        info.setVerticallyCentered_(True)
        paper, left, right, top, bottom = info.paperSize(), info.leftMargin(), info.rightMargin(), info.topMargin(), info.bottomMargin()
        view = NSImageView.alloc().initWithFrame_(NSMakeRect(0, 0, paper.width - left - right - 2, paper.height - top - bottom - 2))
        view.setImage_(img)
        view.setImageScaling_(3)  # NSImageScaleProportionallyUpOrDown : l'image tient dans la page
        op = NSPrintOperation.printOperationWithView_printInfo_(view, info)
    op.setJobTitle_(Path(path).name)
    op.setShowsPrintPanel_(True)
    window = NSApp.keyWindow() or NSApp.mainWindow()
    if window is not None:
        op.runOperationModalForWindow_delegate_didRunSelector_contextInfo_(window, None, None, None)  # la boîte s'ouvre en feuille sur la fenêtre
    else:
        op.runOperation()


def html_to_pdf(html_path: str, pdf_path: str, timeout: float = 30.0) -> None:
    """macOS : PDF paginé d'une page HTML locale, produit par WebKit (le moteur de la fenêtre) dans une fenêtre cachée. À exécuter sur le fil principal.
    L'impression de WebKit se fait en « feuille » sur une fenêtre : en mode bloquant elle ne finit jamais (WebKit calcule les pages en laissant tourner la boucle d'événements)."""
    from AppKit import NSPrintInfo, NSPrintJobSavingURL, NSPrintSaveJob, NSWindow
    from Foundation import NSDate, NSDefaultRunLoopMode, NSMakeRect, NSRunLoop, NSURL
    from WebKit import WKWebView, WKWebViewConfiguration

    def spin(seconds: float) -> None:
        NSRunLoop.currentRunLoop().runMode_beforeDate_(NSDefaultRunLoopMode, NSDate.dateWithTimeIntervalSinceNow_(seconds))

    html, out = Path(html_path), Path(pdf_path)
    out.unlink(missing_ok=True)
    info = NSPrintInfo.sharedPrintInfo().copy()
    paper, left, right, top, bottom = info.paperSize(), info.leftMargin(), info.rightMargin(), info.topMargin(), info.bottomMargin()
    size = NSMakeRect(0, 0, paper.width - left - right, paper.height - top - bottom)
    web = WKWebView.alloc().initWithFrame_configuration_(size, WKWebViewConfiguration.alloc().init())
    win = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(NSMakeRect(-4000, -4000, size.size.width, size.size.height), 0, 2, False)
    win.setContentView_(web)
    win.orderBack_(None)
    try:
        web.loadFileURL_allowingReadAccessToURL_(NSURL.fileURLWithPath_(str(html)), NSURL.fileURLWithPath_(str(html.parent)))
        end = time.time() + timeout
        while (web.isLoading() or web.estimatedProgress() < 1.0) and time.time() < end:
            spin(0.05)
        if web.isLoading():
            raise RuntimeError("page trop longue à charger")
        for _ in range(10):  # laisse la mise en page se terminer
            spin(0.05)
        info.setJobDisposition_(NSPrintSaveJob)
        info.dictionary()[NSPrintJobSavingURL] = NSURL.fileURLWithPath_(str(out))
        op = web.printOperationWithPrintInfo_(info)
        op.setShowsPrintPanel_(False)
        op.setShowsProgressPanel_(False)
        op.runOperationModalForWindow_delegate_didRunSelector_contextInfo_(win, None, None, None)
        last, stable = -1, 0
        while time.time() < end:
            spin(0.1)
            size_now = out.stat().st_size if out.exists() else 0
            if size_now > 50_000_000:  # garde-fou : un PDF de page ne pèse jamais autant
                out.unlink(missing_ok=True)
                raise RuntimeError("PDF anormalement volumineux")
            stable = stable + 1 if (size_now and size_now == last) else 0
            last = size_now
            if stable >= 4:
                return
        raise RuntimeError("PDF non produit à temps")
    finally:
        win.close()


def watch_print(stop: threading.Event | None = None, every: float = 0.3, call_on_main=None) -> None:
    """Le programme principal demande une impression en écrivant « print_request » ; on l'exécute ici (fenêtre native) et on répond dans
    « print_status » : « <id>:ok » ou « <id>:échec … » (alors le programme ouvre le fichier dans Aperçu comme avant)."""
    def current() -> dict:
        with db.conn() as c:
            row = c.execute("SELECT v FROM kv WHERE k='print_request'").fetchone()
        try:
            return json.loads(row["v"]) if row else {}
        except ValueError:
            return {}

    if call_on_main is None:  # exécution sur le fil principal de la fenêtre (macOS) ; injectable pour les tests
        from PyObjCTools import AppHelper
        call_on_main = AppHelper.callAfter
    done = current().get("id")  # une demande restée d'un lancement précédent ne doit pas être rejouée
    while not (stop and stop.is_set()):
        try:
            req = current()
            if req.get("id") and req["id"] != done:
                done = req["id"]

                def run(req=req) -> None:
                    try:
                        if req.get("html"):  # conversion d'une page HTML en PDF : la réponse est écrite une fois le fichier prêt
                            html_to_pdf(req["html"], req["path"])
                            db.kv_set("print_status", f"{req['id']}:ok")
                            return
                        db.kv_set("print_status", f"{req['id']}:ok")  # écrit avant d'ouvrir : la boîte garde la main tant qu'elle est affichée
                        print_native(req["path"])
                    except Exception as e:
                        log.warning("impression : %s", e)
                        db.kv_set("print_status", f"{req['id']}:échec {type(e).__name__} {str(e)[:100]}")
                call_on_main(run)
        except Exception as e:
            log.warning("surveillance de l'impression : %s", e)
        time.sleep(every)


def setup() -> None:
    """Fenêtre native (macOS) : la bibliothèque de fenêtre supprime tout menu clic droit hors mode débogage.
    On rétablit le menu standard (couper, copier, coller, orthographe…) sans les entrées qui feraient quitter l'appli."""
    perf.mark("moteur d'affichage démarré")
    perf.save_window_marks()
    threading.Thread(target=watch_zoom, daemon=True, name="zoom").start()
    if sys.platform == "darwin":
        threading.Thread(target=watch_print, daemon=True, name="impression").start()
        threading.Thread(target=watch_iphone, daemon=True, name="iphone").start()
    if sys.platform != "darwin":
        return
    try:
        from webview.platforms import cocoa

        def will_open_menu(self, menu, event):
            for item in list(menu.itemArray()):
                ident = str(item.identifier() or "")
                if any(k in ident for k in ("Reload", "Inspect", "GoBack", "GoForward", "OpenLink", "OpenImage", "OpenFrame",
                                            "DownloadLinkedFile", "DownloadImage", "OpenMedia", "Download",
                                            # entrées du système qui n'ont rien à faire dans un champ de saisie de l'appli
                                            "LookUp", "Translate", "SearchWeb", "Share", "AutoFill", "Substitutions", "Transformations",
                                            "Speech", "WritingDirection", "Font", "TextDirection")):
                    menu.removeItem_(item)

        cocoa.BrowserView.WebKitHost.willOpenMenu_withEvent_ = will_open_menu
        # le clic qui active une fenêtre inactive doit aussi agir sur ce qu'il touche (sinon il faut cliquer deux fois)
        cocoa.BrowserView.WebKitHost.acceptsFirstMouse_ = lambda self, event: True
        _tidy_mac_menus(cocoa)
    except Exception:
        logging.getLogger("mailia").exception("menu clic droit")
    try:
        from webview.platforms import cocoa as _cocoa
        enable_continuity(_cocoa)
    except Exception:  # noqa: BLE001
        logging.getLogger("mailia").warning("Continuité (importer depuis l'iPhone) non activée", exc_info=True)
