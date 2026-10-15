from conftest import add_message
from mailia import queries, search


def ids(q, **kw):
    return {r["id"] for r in queries.list_messages("", q, False, "", 100, 0, "", group=False, pins_top=False, **kw)}


def test_parse_operators():
    p = search.parse('de:martin objet:"devis urgent" pj:oui non-lu depuis:7j texte libre')
    assert p["text"] == "texte libre" and p["advanced"] and len(p["where"]) == 5
    assert search.parse("rien de special")["advanced"] is False
    assert search.parse("de:a:b mot")["text"] == "mot"  # filtre invalide ignoré proprement


def test_filters_on_messages():
    a = add_message("testbox|INBOX|1", sender="martin@example.com", subject="Devis PPC", body="le devis", has_att=1)
    b = add_message("testbox|INBOX|2", sender="durand@example.com", subject="Autre", body="rien", is_read=1)
    assert ids("de:martin") == {a}
    assert ids("pj:oui") == {a} and ids("pj:non") == {b}
    assert ids("non-lu") == {a} and ids("lu") == {b}
    assert ids("-de:martin") == {b}
    assert ids("devis") == {a}
    assert ids('objet:"Autre"') == {b}


def test_dates_and_category():
    old = add_message("testbox|INBOX|3", date="2020-01-01T10:00:00+00:00", category="newsletter")
    new = add_message("testbox|INBOX|4", date="2999-01-01T10:00:00+00:00")
    assert ids("avant:2021-01-01") == {old}
    assert ids("depuis:2025-01-01") == {new}
    assert ids("cat:newsletter") == {old}


def test_like_wildcards_are_escaped():
    add_message("testbox|INBOX|5", sender="a_b@example.com")
    add_message("testbox|INBOX|6", sender="axb@example.com")
    assert len(ids("de:a_b")) == 1  # « _ » est littéral, pas « n'importe quel caractère »


def test_european_dates_in_search():
    from mailia import search
    assert search._when("01/09/2026") == search._when("2026-09-01")  # 1er septembre, pas le 9 janvier
    assert search._when("1.9.2026") == search._when("2026-09-01") and search._when("01-09-2026") == search._when("2026-09-01")
    assert search._when("13/01/2026") == search._when("2026-01-13")
    assert search._when("01/13/2026") is None  # mois 13 : format américain refusé
    assert search._when("31/02/2026") is None
    p = search.parse("depuis:01/09/2026 avant:01/10/2026")
    assert p["where"] == ["date>=?", "date<?"] and p["args"][0].startswith("2026-09-01") and p["args"][1].startswith("2026-10-01")
