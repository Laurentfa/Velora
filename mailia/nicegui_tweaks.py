"""Réglages de NiceGUI.

1. Une même fenêtre (dialogue) ne s'ouvre jamais deux fois : un clic répété sur un bouton ou une icône ne l'empile pas. Une fenêtre est « la même » quand elle
est créée par la même ligne de code et que la précédente est encore ouverte ; la nouvelle est supprimée à l'ouverture (si on l'attendait avec « await », elle rend None, comme une annulation).

2. Accélération : le dessin d'une liste de 80 mails crée ~4000 éléments, et pour chacun NiceGUI inspecte plusieurs fois la signature
d'une fonction (~9000 appels à inspect.signature, un quart du temps de dessin). Le résultat ne dépend que du code de la fonction : on le garde."""
import sys
import weakref

from nicegui import helpers
from nicegui.elements.dialog import Dialog

_orig = helpers.expects_arguments
_cache: dict[tuple, bool] = {}


def expects_arguments(func) -> bool:
    bound = hasattr(func, "__self__") and hasattr(func, "__func__")
    fn = func.__func__ if bound else func
    code = getattr(fn, "__code__", None)
    if code is None or hasattr(fn, "__wrapped__") or hasattr(fn, "__signature__"):  # décorée, partial, objet appelable… : calcul d'origine
        return _orig(func)
    key = (code, bound)
    if key not in _cache:
        _cache[key] = _orig(func)
    return _cache[key]


_sites: dict[tuple, weakref.WeakSet] = {}  # ligne de code qui crée la fenêtre -> fenêtres créées par elle
_init, _open = Dialog.__init__, Dialog.open


def _dialog_init(self, *args, **kwargs) -> None:
    _init(self, *args, **kwargs)
    caller = sys._getframe(1)
    self._site = (caller.f_code.co_filename, caller.f_lineno)
    _sites.setdefault(self._site, weakref.WeakSet()).add(self)


def _already_open(dialog, site) -> bool:
    for other in list(_sites.get(site, ())):
        try:
            if other is not dialog and not other.is_deleted and other.value and other.client is dialog.client:
                return True
        except RuntimeError:  # fenêtre d'une page déjà fermée
            continue
    return False


def _dialog_open(self) -> None:
    site = getattr(self, "_site", None)
    if site and not self.value and _already_open(self, site):
        self.delete()  # la même fenêtre est déjà ouverte : celle-ci n'a pas lieu d'être
        return
    _open(self)


def install() -> None:
    helpers.expects_arguments = expects_arguments
    Dialog.__init__ = _dialog_init
    Dialog.open = _dialog_open
