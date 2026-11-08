"""Partage des analyses IA entre ordinateurs, par la boîte Exchange : reprise sans appeler l'IA, publication, garde-fous."""
import json

import pytest
from conftest import add_message

from mailia import ai, aishare, config, db, sync
from mailia.providers.base import Delta, Msg

RESULT = {"summary": "Demande de rappel.", "category": "action", "priority": 2, "needs_reply": 1, "suggested": "Répondre"}


@pytest.fixture
def ews(monkeypatch, tmp_path):
    """Une boîte Exchange avec l'IA, et un faux serveur qui garde les analyses publiées."""
    monkeypatch.setattr(config, "ACCOUNTS_PATH", tmp_path / "accounts.json")
    monkeypatch.setattr(config, "_acc_cache", (None, []))
    config.write_accounts([{"key": "testbox", "label": "Test", "kind": "ews", "email": "a@b.fr", "login": "a", "host": "x", "ai": True}])
    monkeypatch.setattr(config, "ai_enabled", lambda: True)
    monkeypatch.setattr(sync, "ensure_details", lambda row: None)
    monkeypatch.setattr(sync, "needs_details", lambda row: False)
    sync._ai_pause.update(until=0.0, fp="")
    aishare._write_refused.clear()

    class Server:
        store: dict[str, str] = {}
        writes: list = []
        fail_write = False

        def read_analysis(self, uid):
            return self.store.get(uid)

        def write_analysis(self, uid, raw):
            if self.fail_write:
                raise PermissionError("lecture seule")
            self.store[uid] = raw
            self.writes.append(uid)

    srv = Server()
    srv.store, srv.writes = {}, []
    monkeypatch.setattr(sync, "provider", lambda key: srv)
    return srv


def get(uid="1"):
    with db.conn() as c:
        return dict(c.execute("SELECT * FROM messages WHERE id=?", (f"testbox|INBOX|{uid}",)).fetchone())


def test_round_trip_and_the_fingerprint_guards_against_other_instructions(clean_db, ews) -> None:
    raw = aishare.encode(RESULT)
    assert aishare.decode(raw) == RESULT
    db.kv_set("prompt:analyze", "Autres critères de tri")  # consignes différentes : l'analyse faite avec les anciennes n'est pas reprise
    assert aishare.decode(raw) is None
    assert aishare.decode("pas du json") is None and aishare.decode(None) is None and aishare.decode(json.dumps({"v": 99})) is None


def test_a_published_analysis_is_taken_without_calling_the_ai_and_keeps_local_rules(clean_db, ews, monkeypatch) -> None:
    monkeypatch.setattr(ai, "analyze", lambda m, system=None: (_ for _ in ()).throw(AssertionError("l'IA ne doit pas être appelée")))
    add_message("testbox|INBOX|1", is_read=0, sender="vip@exemple.fr")
    db.kv_set("vip_senders", '["vip@exemple.fr"]')  # VOS expéditeurs prioritaires s'appliquent par-dessus le résultat partagé
    ews.store["1"] = aishare.encode(RESULT)
    assert sync.analyze_pending() == 1
    m = get()
    assert m["summary"] == "Demande de rappel." and m["category"] == "action" and m["needs_reply"] == 1 and m["ai_at"]
    assert m["priority"] == 3  # prioritaire chez vous, normale pour l'autre ordinateur
    assert aishare.stats()["reused"] == 1 and ews.writes == []  # reprise : rien n'est republié


def test_without_a_published_analysis_the_ai_runs_and_the_raw_result_is_published(clean_db, ews, monkeypatch) -> None:
    monkeypatch.setattr(ai, "analyze", lambda m, system=None: dict(RESULT))
    add_message("testbox|INBOX|1", is_read=0, sender="vip@exemple.fr")
    db.kv_set("vip_senders", '["vip@exemple.fr"]')
    assert sync.analyze_pending() == 1
    assert get()["priority"] == 3  # chez vous : prioritaire
    assert json.loads(ews.store["1"])["p"] == 2  # sur le serveur : le résultat brut de l'IA, pas vos règles locales
    assert aishare.stats()["published"] == 1


def test_a_server_that_refuses_writing_never_breaks_the_analysis(clean_db, ews, monkeypatch) -> None:
    monkeypatch.setattr(ai, "analyze", lambda m, system=None: dict(RESULT))
    ews.fail_write = True
    add_message("testbox|INBOX|1", is_read=0)
    add_message("testbox|INBOX|2", is_read=0)
    assert sync.analyze_pending() == 2 and get("1")["ai_at"] and get("2")["ai_at"]
    assert "testbox" in aishare._write_refused  # on n'insiste pas : une seule tentative d'écriture refusée


def test_the_switch_per_mailbox_turns_sharing_off(clean_db, ews, monkeypatch) -> None:
    monkeypatch.setattr(ai, "analyze", lambda m, system=None: dict(RESULT))
    aishare.set_enabled("testbox", False)
    ews.store["1"] = aishare.encode({**RESULT, "summary": "Autre ordinateur"})
    add_message("testbox|INBOX|1", is_read=0)
    sync.analyze_pending()
    assert get()["summary"] == "Demande de rappel." and ews.writes == []  # ni reprise ni publication
    assert aishare.enabled("testbox") is False
    aishare.set_enabled("testbox", True)
    assert aishare.enabled("testbox") is True


def test_excluded_senders_and_accounts_without_ai_are_never_shared(clean_db, ews) -> None:
    db.kv_set("ai_exclude", '["exclu.fr"]')
    add_message("testbox|INBOX|1", is_read=0, sender="x@exclu.fr")
    assert aishare.take(get(), aishare.encode(RESULT)) is False and not get()["ai_at"]
    # une boîte IMAP n'est pas concernée
    config.write_accounts([{"key": "testbox", "label": "Test", "kind": "imap", "email": "a@b.fr", "ai": True}])
    assert aishare.supported("testbox") is False and aishare.enabled("testbox") is False


def test_already_analysed_mails_are_left_alone(clean_db, ews) -> None:
    add_message("testbox|INBOX|1", is_read=0, ai_at="2026-11-07T08:00:00+00:00", summary="Local", category="info", priority=1, needs_reply=0)
    assert aishare.take(get(), aishare.encode(RESULT)) is False and get()["summary"] == "Local"


def test_the_sync_takes_the_analysis_published_with_a_new_mail(clean_db, ews, monkeypatch) -> None:
    monkeypatch.setattr(ai, "analyze", lambda m, system=None: (_ for _ in ()).throw(AssertionError("l'IA ne doit pas être appelée")))
    m = Msg(uid="9", subject="Sujet", sender="alice@example.com", sender_name="Alice", to="a@b.fr", date="2026-11-07T08:00:00+00:00", body="corps", is_read=False,
            has_att=False, shared=aishare.encode(RESULT))
    sync._apply("testbox", "INBOX", Delta(upserts=[m], new_state="s1"))
    g = get("9")
    assert g["summary"] == "Demande de rappel." and g["ai_at"]
    assert sync.analyze_pending() == 0  # plus rien à analyser : aucun appel à l'IA


def test_ews_reads_the_shared_property() -> None:
    from exchangelib import Message

    from mailia.providers import ews
    assert "ai_share" in ews.FIELDS and any(f.name == "ai_share" for f in Message.FIELDS)
    assert ews._AIShare.property_name == "VeloraAnalysis"


def test_the_settings_explain_the_feature_and_offer_one_switch_per_mailbox(clean_db, ews) -> None:
    from mailia import ui_aishare
    assert [t for t, _ in ui_aishare.EXPLAIN] == ["Ce que ça fait", "Où c'est stocké", "Qui peut le lire", "Quand une analyse est reprise", "Le désactiver"]
    assert db.kv_get("ai_share_notice") is None


async def test_the_settings_block_shows_the_switch_and_the_explanation(clean_db, ews, user) -> None:
    from nicegui import ui

    from mailia import ui_aishare

    @ui.page("/aishare")
    def page() -> None:
        ui_aishare.settings()

    await user.open("/aishare")
    await user.should_see(marker="ai-share-title")
    await user.should_see(marker="ai-share-testbox")
    await user.should_see("Activé par défaut")
    aishare.set_enabled("testbox", False)
    assert not aishare.enabled("testbox")
