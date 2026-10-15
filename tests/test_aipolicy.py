import pytest
from conftest import add_message
from mailia import ai, aipolicy, ask, db, queries


def test_sender_patterns():
    assert aipolicy.matches_sender("laposte.net", "a@news.laposte.net")
    assert not aipolicy.matches_sender("laposte.net", "a@lapostex.net")
    assert aipolicy.matches_sender("bob@x.fr", "BOB@x.fr") and not aipolicy.matches_sender("bob@x.fr", "rob@x.fr")


def test_exclusion_by_account_and_sender():
    assert aipolicy.is_excluded({"account": "noai", "sender": "a@b.fr"})          # boîte sans IA
    assert not aipolicy.is_excluded({"account": "testbox", "sender": "a@b.fr"})
    aipolicy.set_excluded_patterns(["cpam.fr"])
    assert aipolicy.is_excluded({"account": "testbox", "sender": "x@cpam.fr"})
    with pytest.raises(aipolicy.AIError):
        aipolicy.guard({"account": "testbox", "sender": "x@cpam.fr"})


def test_excluded_mails_never_reach_ai_paths():
    aipolicy.set_excluded_patterns(["secret.fr"])
    ok = add_message("testbox|INBOX|1", sender="a@public.fr", subject="projet", body="projet alpha budget")
    bad = add_message("testbox|INBOX|2", sender="b@secret.fr", subject="projet", body="projet alpha confidentiel")
    inbox_noai = add_message("noai|INBOX|3", account="noai", sender="c@public.fr", body="projet alpha prive")
    found = {r["id"] for r in ask.retrieve("projet alpha")}
    assert found == {ok}, found                       # « Demander à ma boîte »
    sql, args = queries.ai_pending_clause()
    with db.conn() as c:
        pending = {r["id"] for r in c.execute(f"SELECT id FROM messages WHERE {sql}", args)}
    assert bad not in pending and inbox_noai not in pending   # file d'analyse automatique
    ctx = ai._thread_context({"ckey": "t", "id": "x", "date": "2999"})
    assert ctx == ""


def test_thread_context_skips_excluded():
    aipolicy.set_excluded_patterns(["secret.fr"])
    add_message("testbox|INBOX|10", sender="b@secret.fr", body="message confidentiel", ckey="fil1", date="2026-10-01T10:00:00+00:00")
    add_message("testbox|INBOX|11", sender="a@public.fr", body="message public", ckey="fil1", date="2026-10-02T10:00:00+00:00")
    cur = {"id": "testbox|INBOX|12", "ckey": "fil1", "date": "2026-10-03T10:00:00+00:00"}
    ctx = ai._thread_context(cur)
    assert "public" in ctx and "confidentiel" not in ctx


def test_vip_and_learning():
    aipolicy.set_vip_list(["@exemple.net", "maman@free.fr"])
    assert aipolicy.is_vip("aldo@exemple.net") and aipolicy.is_vip("maman@free.fr") and not aipolicy.is_vip("x@y.fr")
    base = {"subject": "s", "account": "testbox", "category": "info"}
    assert aipolicy.record_correction({**base, "sender": "a@ex.com"}, "info", "newsletter") is None
    assert aipolicy.record_correction({**base, "sender": "b@ex.com"}, "info", "newsletter") == "ex.com"   # deux expéditeurs du domaine
    assert aipolicy.forced_category("c@sub.ex.com") == "newsletter"
    assert aipolicy.record_correction({**base, "sender": "a@gmail.com"}, "info", "newsletter") is None
    assert aipolicy.record_correction({**base, "sender": "b@gmail.com"}, "info", "newsletter") is None  # domaines génériques : jamais généralisés
    assert "ex.com" not in aipolicy.few_shot_block() or "newsletter" in aipolicy.few_shot_block()


def test_budget_hard_limit():
    db.record_usage("claude-haiku-4-5", 5_000_000, 500_000, "analyze")  # ≈ 7 $
    db.kv_set("ai_budget_eur", "1"); db.kv_set("ai_budget_hard", "1")
    with pytest.raises(aipolicy.AIError, match="Budget"):
        aipolicy.check_budget()
    db.kv_set("ai_budget_hard", "0")
    aipolicy.check_budget()  # sans blocage dur : simple alerte


def test_language_note_follows_interface_language():
    db.kv_set("ui_lang", "en")
    assert "English" in aipolicy.language_note("analyze") and aipolicy.language_note("draft_reply") == ""
    db.kv_set("ui_lang", "fr")
    assert aipolicy.language_note("analyze") == ""
