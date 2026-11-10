"""Statistiques de courrier : reçus, lus, envoyés, par boîte et par période."""
from datetime import date

from mailia import db, stats


def _msg(i, account, folder, d, read):
    with db.conn() as c:
        c.execute("INSERT INTO messages(id,account,uid,folder,date,is_read) VALUES(?,?,?,?,?,?)", (f"{account}|{folder}|{i}", account, str(i), folder, d, read))


def _folder(account, key, role):
    with db.conn() as c:
        c.execute("INSERT OR REPLACE INTO folders(account,key,name,path,role,sync) VALUES(?,?,?,?,?,1)", (account, key, key, key, role))


def test_received_read_sent_by_box_and_period(clean_db):
    today = date(2026, 11, 10)
    for a in ("ex", "perso"):
        for key, role in (("INBOX", "inbox"), ("Sent", "sent"), ("Brouillons", "drafts"), ("Corbeille", "trash"), ("Spam", "junk"), ("Divers", "")):
            _folder(a, key, role)
    _msg(1, "ex", "INBOX", "2026-11-09T10:00:00+00:00", 1)
    _msg(2, "ex", "INBOX", "2026-11-09T11:00:00+00:00", 0)
    _msg(3, "ex", "Divers", "2026-10-01T11:00:00+00:00", 1)       # dossier perso : compte comme reçu
    _msg(4, "ex", "Sent", "2026-11-08T09:00:00+00:00", 1)
    _msg(5, "ex", "Brouillons", "2026-11-08T09:00:00+00:00", 0)    # exclus
    _msg(6, "ex", "Corbeille", "2026-11-08T09:00:00+00:00", 0)     # exclus
    _msg(7, "ex", "Spam", "2026-11-08T09:00:00+00:00", 0)          # exclus
    _msg(8, "perso", "INBOX", "2026-03-02T09:00:00+00:00", 1)
    _msg(9, "perso", "Sent", "2026-03-03T09:00:00+00:00", 1)
    _msg(10, "perso", "INBOX", "1900-01-01T00:00:00+00:00", 1)     # sans vraie date : ignoré
    s = stats.summary("", "12m", today)
    assert (s["received"], s["read"], s["sent"]) == (4, 3, 2), s
    assert len(s["buckets"]) == 12 and s["buckets"][-1][0] == "11/26" and s["buckets"][-1][1:] == (2, 1)
    assert {a[0]: a[1:] for a in s["accounts"]} == {"ex": (3, 2, 1), "perso": (1, 1, 1)}
    one = stats.summary("ex", "30j", today)
    assert (one["received"], one["sent"]) == (2, 1) and len(one["buckets"]) == 30 and one["buckets"][-1][0] == "10/11"
    assert one["buckets"][-2][1:] == (2, 0)  # le 9 novembre : deux reçus
    allp = stats.summary("", "tout", today)
    assert allp["received"] == 4 and allp["sent"] == 2
    assert stats.summary("", "n'importe quoi", today)["received"] == 4  # période inconnue : 12 mois


def test_empty_database_gives_zeros(clean_db):
    s = stats.summary("", "tout", date(2026, 11, 10))
    assert s["received"] == s["sent"] == s["read"] == 0 and s["accounts"] == []


def test_more_than_three_years_groups_by_year(clean_db):
    _folder("ex", "INBOX", "inbox")
    _msg(1, "ex", "INBOX", "2019-05-01T10:00:00+00:00", 1)
    _msg(2, "ex", "INBOX", "2021-05-01T10:00:00+00:00", 0)
    _msg(3, "ex", "INBOX", "2026-05-01T10:00:00+00:00", 1)
    s = stats.summary("", "tout", date(2026, 11, 10))
    assert [b[0] for b in s["buckets"]] == ["2019", "2020", "2021", "2022", "2023", "2024", "2025", "2026"], s["buckets"]
    assert [b[1] for b in s["buckets"]] == [1, 0, 1, 0, 0, 0, 0, 1]
