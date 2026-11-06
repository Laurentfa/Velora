"""Une seule copie de Velora : la deuxième s'arrête et réveille la première."""
import socket
import threading
import time

import pytest

from mailia import instance


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    monkeypatch.setattr(instance, "PORT", port)
    monkeypatch.setattr(instance, "_state", {"sock": None, "closing": False})
    monkeypatch.setattr(instance, "_raise_other", lambda: None)
    yield
    sock = instance._state.get("sock")
    if sock:
        sock.close()


def test_the_first_copy_starts_and_a_second_one_is_refused_and_wakes_the_first(monkeypatch) -> None:
    shown = threading.Event()
    monkeypatch.setattr(instance, "on_show", shown.set)
    assert instance.claim() is True
    assert instance.claim() is False
    assert shown.wait(3), "la première copie n'a pas été réveillée"


def test_a_port_used_by_something_else_does_not_block_velora(monkeypatch) -> None:
    other = socket.socket()
    other.bind(("127.0.0.1", instance.PORT))
    other.listen(1)
    try:
        assert instance.claim() is True  # personne ne répond « VELORA » : pas de verrou, on démarre
    finally:
        other.close()


def test_a_copy_that_is_closing_is_waited_for() -> None:
    assert instance.claim() is True
    instance.closing()
    first = instance._state["sock"]

    def finish():
        time.sleep(1.2)
        first.close()  # la copie qui se ferme libère le port
    threading.Thread(target=finish, daemon=True).start()
    t0 = time.time()
    assert instance.claim() is True  # elle a attendu la fin de la première, puis démarre
    assert time.time() - t0 >= 1.0
    assert instance._state["sock"] is not first


def test_a_copy_that_never_finishes_closing_does_not_block_for_ever(monkeypatch) -> None:
    monkeypatch.setattr(instance, "WAIT_FOR_CLOSING", 1)
    assert instance.claim() is True
    instance.closing()
    t0 = time.time()
    assert instance.claim() is True  # la première n'en finit pas : on démarre quand même
    assert 0.8 <= time.time() - t0 < 5
