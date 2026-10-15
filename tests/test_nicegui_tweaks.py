"""Accélération de NiceGUI : le résultat mis en mémoire doit toujours être celui du calcul d'origine ; redessins espacés sur machine lente."""
import functools
import time
import types

import pytest
from nicegui import helpers

import main
from mailia import nicegui_tweaks as tw


class Obj:
    def method(self, e): ...
    def noargs(self): ...
    def __call__(self, e): ...


def deco(fn):
    @functools.wraps(fn)
    def wrapper(*a, **k):
        return fn(*a, **k)
    return wrapper


@deco
def decorated(e): ...


async def coro(e): ...


CASES = [lambda: None, lambda e: None, lambda e=1: None, lambda *a, **k: None, lambda a, *b: None, Obj().method, Obj().noargs, Obj(), decorated,
         functools.partial(lambda a, b: None, 1), coro, print, Obj.method]


@pytest.mark.parametrize("fn", CASES, ids=range(len(CASES)))
def test_cached_answer_equals_original(fn):
    for _ in range(2):  # 2e appel : réponse issue de la mémoire
        assert tw.expects_arguments(fn) == tw._orig(fn)


def test_two_closures_of_the_same_code_share_one_entry():
    fs = [(lambda i=i: (lambda e: i)) for i in range(5)]
    before = len(tw._cache)
    assert all(tw.expects_arguments(f()) for f in fs)
    assert len(tw._cache) - before <= 1


def test_install_replaces_nicegui_helper():
    tw.install()
    assert helpers.expects_arguments is tw.expects_arguments


async def test_background_redraws_are_spaced_by_build_cost():
    reloads = []

    async def reload():
        reloads.append(1)
    box = types.SimpleNamespace(ver=("a",), _last_bg=0.0, _build_cost=0.0, reload=reload)
    import mailia.queries as q
    real = q.version
    q.version = lambda: ("b",)
    try:
        await main.Mailbox.tick(box)  # première fois : redessin immédiat
        assert len(reloads) == 1 and box.ver == ("b",)
        q.version = lambda: ("c",)
        box._build_cost = 100.0  # ordinateur lent : le redessin suivant attend
        await main.Mailbox.tick(box)
        assert len(reloads) == 1 and box.ver == ("b",)
        box._build_cost = 0.0  # ordinateur rapide : redessin au prochain tick
        await main.Mailbox.tick(box)
        assert len(reloads) == 2 and box.ver == ("c",)
    finally:
        q.version = real
    assert time.monotonic() >= box._last_bg
