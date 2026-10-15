from mailia import db, sync


def _row(oid, status):
    db.outbox_add({"id": oid, "account": "testbox", "to": "a@b.fr", "cc": "", "subject": "s", "html": "<p>x</p>", "body": "x", "send_at": "2000-01-01T00:00:00+00:00"})
    db.outbox_mark(oid, status)


def test_interrupted_send_is_flagged_never_replayed():
    _row("a", "sending"); _row("b", "sent"); _row("c", "pending")
    assert db.outbox_recover() == (1, 1)
    rows = {r["id"]: r["status"] for r in db.outbox_list()}
    assert rows == {"a": "uncertain", "c": "pending"}
    assert [r["id"] for r in db.outbox_due()] == ["c"]   # l'incertain n'est jamais renvoyé tout seul
    assert "Envoyés" in db.outbox_get("a")["error"]


def test_successful_send_is_marked_before_cleanup():
    _row("d", "pending")
    assert sync.send_due() == 1                 # envoi simulé (MAILIA_DRY_RUN)
    assert db.outbox_get("d") is None
