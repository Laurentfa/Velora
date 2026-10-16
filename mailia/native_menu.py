"""Fenêtre native : réglages propres à la fenêtre (menu clic droit, barre de menus macOS), exécutés dans SON processus.
Ce code est dans un module à part pour que ce processus n'ait pas à recharger tout le programme (plus de 2 s au démarrage sous Windows)."""
import logging
import sys

from . import i18n, perf


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
            edit = main_menu.itemAtIndex_(1).submenu() if main_menu.numberOfItems() > 1 else None

            def clean(*_args) -> None:  # le système ajoute lui-même des entrées au menu Édition (remplissage automatique…) : on ne garde que les quatre utiles
                for item in list(edit.itemArray()):
                    if str(item.title()) not in keep and not item.isSeparatorItem():
                        edit.removeItem_(item)

            if edit is not None:
                from AppKit import NSMenuDidAddItemNotification, NSNotificationCenter
                clean()
                NSNotificationCenter.defaultCenter().addObserverForName_object_queue_usingBlock_(NSMenuDidAddItemNotification, edit, None, clean)

        AppHelper.callAfter(rebuild)
    except Exception:
        logging.getLogger("mailia").warning("barre de menus non simplifiée", exc_info=True)


def setup() -> None:
    """Fenêtre native (macOS) : la bibliothèque de fenêtre supprime tout menu clic droit hors mode débogage.
    On rétablit le menu standard (couper, copier, coller, orthographe…) sans les entrées qui feraient quitter l'appli."""
    perf.mark("moteur d'affichage démarré")
    perf.save_window_marks()
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
