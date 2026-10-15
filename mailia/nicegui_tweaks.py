"""Accélération de NiceGUI : le dessin d'une liste de 80 mails crée ~4000 éléments, et pour chacun NiceGUI inspecte plusieurs fois la signature
d'une fonction (~9000 appels à inspect.signature, un quart du temps de dessin). Le résultat ne dépend que du code de la fonction : on le garde."""
from nicegui import helpers

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


def install() -> None:
    helpers.expects_arguments = expects_arguments
