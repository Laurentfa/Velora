from mailia.ui_bulk import BulkMixin, range_ids


def test_range_between_two_rows_in_list_order():
    ids = ["a", "b", "c", "d", "e"]
    assert range_ids(ids, "b", "d") == ["b", "c", "d"]
    assert range_ids(ids, "d", "b") == ["b", "c", "d"]  # dans l'autre sens aussi
    assert range_ids(ids, "c", "c") == ["c"]
    assert range_ids(ids, None, "c") == ["c"]  # pas de repère : la ligne seule
    assert range_ids(ids, "zz", "c") == ["c"]  # repère disparu de la liste (rafraîchie, changée de vue)


def test_bulk_perform_continues_after_an_error(monkeypatch):
    from mailia import sync
    calls = []

    def fake(msgs, kind, dest):
        calls.append((msgs[0]["id"], kind, dest))
        if msgs[0]["id"] == "bad":
            raise RuntimeError("serveur injoignable")

    monkeypatch.setattr(sync, "perform_many", fake)
    groups = [[{"id": "a"}], [{"id": "bad"}], [{"id": "c"}]]
    assert BulkMixin._bulk_perform(groups, "move", "K") == (2, "serveur injoignable")
    assert [c[0] for c in calls] == ["a", "bad", "c"]  # une erreur n'arrête pas les suivantes


def test_read_filter_keeps_only_fully_read_conversations():
    from mailia import queries
    from tests.conftest import add_message
    add_message(mid="testbox|INBOX|1", subject="lu", ckey="testbox|c1", is_read=1)
    add_message(mid="testbox|INBOX|2", subject="fil mixte (1)", ckey="testbox|c2", is_read=1, date="2026-10-01T11:00:00+00:00")
    add_message(mid="testbox|INBOX|3", subject="fil mixte (2)", ckey="testbox|c2", is_read=0, date="2026-10-01T12:00:00+00:00")
    add_message(mid="testbox|INBOX|4", subject="non lu", ckey="testbox|c3", is_read=0)
    subjects = lambda **kw: sorted(r["subject"] for r in queries.list_messages(**kw))
    assert subjects(read=True) == ["lu"]  # un fil qui contient un non lu n'est pas « lu »
    assert subjects(unread=True) == ["fil mixte (2)", "non lu"]
    assert len(subjects()) == 3  # sans filtre : les trois fils


def test_archive_conversation_skips_already_archived(monkeypatch):
    from mailia import sync
    done = []
    monkeypatch.setattr(sync, "archive_folder", lambda k: "ARCH")
    monkeypatch.setattr(sync, "perform", lambda m, kind, dest=None: done.append(m["uid"]))
    sync.perform_many([{"account": "a", "folder": "ARCH", "uid": 1}, {"account": "a", "folder": "INBOX", "uid": 2},
                       {"account": "a", "folder": "X", "uid": 3}], "archive")
    assert done == [2, 3]


def test_oldest_first_sorts_and_keeps_latest_message_of_each_thread():
    from mailia import queries
    from tests.conftest import add_message
    add_message(mid="testbox|INBOX|1", subject="A (début)", ckey="testbox|cA", date="2026-10-01T08:00:00+00:00")
    add_message(mid="testbox|INBOX|2", subject="B", ckey="testbox|cB", date="2026-10-02T08:00:00+00:00")
    add_message(mid="testbox|INBOX|3", subject="A (suite)", ckey="testbox|cA", date="2026-10-03T08:00:00+00:00")
    recent = [r["subject"] for r in queries.list_messages()]
    oldest = [r["subject"] for r in queries.list_messages(oldest_first=True)]
    assert recent == ["A (suite)", "B"]
    assert oldest == ["B", "A (suite)"]  # le fil A reste représenté par son message le plus récent
