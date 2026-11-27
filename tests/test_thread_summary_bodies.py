"""Résumé d'une conversation : les messages enregistrés sans texte sont relus sur le serveur avant d'être envoyés à l'IA (sinon l'IA ne voit que des en-têtes)."""
import pytest

from mailia import ai, db, sync
from tests.conftest import add_message


def _thread(bodies):
    for i, b in enumerate(bodies):
        add_message(mid=f"testbox|INBOX|{i + 1}", ckey="c1", subject="Dépannage O2", body=b, date=f"2026-11-2{i}T10:00:00+00:00")


def _fake_ask(seen):
    def ask(system, user, **kw):
        seen.append(user)
        return "Résumé factice."
    return ask


def test_bodyless_messages_are_loaded_before_the_summary(monkeypatch):
    _thread(["", "texte déjà là"])
    loaded = []

    def ensure(m):
        loaded.append(m["id"])
        db.set_body(m["id"], "texte relu sur le serveur")
        return {**m, "body": "texte relu sur le serveur"}
    monkeypatch.setattr(sync, "ensure_details", ensure)
    seen = []
    monkeypatch.setattr(ai, "_ask", _fake_ask(seen))
    assert ai.summarize_thread("c1") == "Résumé factice."
    assert loaded == ["testbox|INBOX|1"] and "texte relu sur le serveur" in seen[0] and "texte déjà là" in seen[0]


def test_nothing_readable_means_no_summary_and_no_cache(monkeypatch):
    _thread(["", ""])
    monkeypatch.setattr(sync, "ensure_details", lambda m: (_ for _ in ()).throw(RuntimeError("serveur muet")))
    monkeypatch.setattr(ai, "_ask", lambda *a, **k: pytest.fail("l'IA ne doit pas être appelée sans texte"))
    with pytest.raises(ai.AIError, match="n'a pas pu être chargé"):
        ai.summarize_thread("c1")
    assert not db.kv_get("thread_sum:c1")


def test_a_cached_summary_made_without_the_text_is_not_served_again(monkeypatch):
    _thread(["un", "deux"])
    db.kv_set("thread_sum:c1", "2\nJe ne vois pas le contenu des e-mails dans votre message, uniquement les en-têtes.")
    seen = []
    monkeypatch.setattr(ai, "_ask", _fake_ask(seen))
    assert ai.summarize_thread("c1") == "Résumé factice." and len(seen) == 1  # refait avec le vrai texte
    assert ai.summarize_thread("c1") == "Résumé factice." and len(seen) == 1  # puis servi depuis le cache
