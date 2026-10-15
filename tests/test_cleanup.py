import threading

from conftest import add_message
from mailia import cleanup


def test_sender_stats_and_archive_simulated(monkeypatch):
    for i in range(4):
        add_message(f"testbox|INBOX|{i}", sender="news@bulk.example.com", is_read=0)
    add_message("testbox|INBOX|9", sender="ami@example.com", is_read=1)
    stats = cleanup.sender_stats(min_count=3, max_read_pct=100)
    assert [s["sender"] for s in stats] == ["news@bulk.example.com"] and stats[0]["read_pct"] == 0
    seen = []
    done, failed = cleanup.archive_sender("news@bulk.example.com", lambda i, n: seen.append((i, n)))   # MAILIA_DRY_RUN : rien ne bouge
    assert (done, failed) == (4, 0) and seen[-1] == (4, 4)
    stop = threading.Event(); stop.set()
    assert cleanup.archive_sender("news@bulk.example.com", None, stop) == (0, 0)   # arrêt demandé : rien de plus n'est fait
    cleanup.ignore("news@bulk.example.com")
    assert cleanup.sender_stats(3, 100) == []
