"""Survol d'un message de la liste : les modules inscrits en sont informés une seule fois, pour le dernier message survolé, avec le message complet."""
import time
from pathlib import Path

from mailia import db, modules


def _api(mid: str = "essai") -> modules.ModuleAPI:
    return modules.ModuleAPI({"id": mid, "dir": Path("."), "name": mid})


def test_only_the_last_hovered_message_is_reported_with_its_full_content(clean_db, monkeypatch) -> None:
    monkeypatch.setattr(modules, "HOVER_DELAY", 0.05)
    with db.conn() as c:
        for i in (1, 2, 3):
            c.execute("INSERT INTO messages (id, account, uid, subject, body, folder) VALUES (?,?,?,?,?,?)", (f"a:{i}", "a", str(i), f"objet {i}", f"corps {i}", "INBOX"))
    got = []
    modules.slots["hover"].clear()
    _api().on_message_hover(lambda m: got.append((m["id"], m["subject"], m["body"])))
    for mid in ("a:1", "a:2", "a:3"):  # la souris passe sur trois messages d'affilée
        modules.hovered(mid)
    time.sleep(0.4)
    assert got == [("a:3", "objet 3", "corps 3")]
    modules.hovered("a:inconnu")  # un message disparu : rien, pas d'erreur
    time.sleep(0.3)
    assert len(got) == 1
    modules.slots["hover"].clear()


def test_a_failing_module_does_not_stop_the_others_and_no_listener_means_no_work(clean_db, monkeypatch) -> None:
    monkeypatch.setattr(modules, "HOVER_DELAY", 0.05)
    with db.conn() as c:
        c.execute("INSERT INTO messages (id, account, uid, subject, body, folder) VALUES ('a:1','a','1','o','c','INBOX')")
    ok = []
    modules.slots["hover"].clear()
    _api("a").on_message_hover(lambda m: 1 / 0)
    _api("b").on_message_hover(lambda m: ok.append(m["id"]))
    modules.hovered("a:1")
    time.sleep(0.3)
    assert ok == ["a:1"]
    modules.slots["hover"].clear()
    _hover_before = modules._hover["timer"]
    modules.hovered("a:1")  # personne n'écoute : aucun minuteur
    assert modules._hover["timer"] is _hover_before
