from mailia import db, queries, sync
from tests.conftest import add_message


class FakeProvider:
    def __init__(self):
        self.calls = []

    def mark_verb(self, folder, uid, verb):
        self.calls.append((folder, uid, verb))


def test_mark_answered_reply_and_forward(monkeypatch):
    mid = add_message()
    fake = FakeProvider()
    monkeypatch.setattr(sync, "provider", lambda account: fake)
    monkeypatch.setattr(sync, "_dry", lambda: False)
    sync.mark_answered(mid)
    assert queries.get_message(mid)["verb"] == 1 and fake.calls == [("INBOX", "1", 1)]
    sync.mark_answered("fwd:" + mid)
    assert queries.get_message(mid)["verb"] == 2 and fake.calls[-1] == ("INBOX", "1", 2)


def test_mark_answered_never_raises_and_skips_dry_run(monkeypatch):
    mid = add_message(mid="testbox|INBOX|2")

    class Broken:
        def mark_verb(self, *a):
            raise RuntimeError("serveur injoignable")

    monkeypatch.setattr(sync, "provider", lambda account: Broken())
    monkeypatch.setattr(sync, "_dry", lambda: False)
    sync.mark_answered(mid)  # l'envoi ne doit pas échouer pour autant
    assert queries.get_message(mid)["verb"] == 1
    monkeypatch.setattr(sync, "_dry", lambda: True)
    sync.mark_answered("fwd:" + mid)
    assert queries.get_message(mid)["verb"] == 1  # essai sans envoi : rien ne change
    sync.mark_answered(None)


def test_upsert_keeps_verb_from_server():
    from mailia.providers.base import Msg
    m = Msg(uid="9", subject="s", sender="a@b.fr", sender_name="A", to="c@d.fr", date="2026-10-01T10:00:00+00:00", body="x", is_read=True, has_att=False, verb=2)
    assert db.upsert("testbox", "INBOX", m) is True
    mid = db.msg_id("testbox", "INBOX", "9")
    assert queries.get_message(mid)["verb"] == 2
    m.verb = 0
    db.upsert("testbox", "INBOX", m)
    assert queries.get_message(mid)["verb"] == 2  # une marque posée n'est jamais effacée par une synchro


def test_forward_quote_keeps_embedded_images_only():
    from mailia import compose, htmlmail
    html = ('<p>Voici</p><img src="data:image/png;base64,AAAA" alt="photo">'
            '<img src="https://suivi.example.com/pixel.gif"><img src="cid:abc">')
    kept = htmlmail.sanitize_quote(html, images=True)
    assert 'data:image/png;base64,AAAA' in kept
    assert "suivi.example.com" not in kept and "cid:abc" not in kept
    assert "<img" not in htmlmail.sanitize_quote(html)  # une réponse ne cite toujours aucune image
    m = {"date": "2026-10-01T10:00:00+00:00", "sender": "a@b.fr", "sender_name": "A", "to_addrs": "c@d.fr", "cc_addrs": "", "subject": "s", "body_html": html, "body": ""}
    assert "data:image/png" in compose.quote_html(m, images=True) and "data:image/png" not in compose.quote_html(m)
    out, inline = htmlmail.extract_inline(compose.quote_html(m, images=True))
    assert len(inline) == 1 and "data:image" not in out  # à l'envoi, l'image devient une image incorporée


def test_roboto_gets_fallback_fonts_when_sending():
    from mailia import htmlmail
    out = htmlmail.sanitize_outgoing('<div style="font-family: Roboto; color:red">a</div><font face="Roboto">b</font><p style="font-family: Arial">c</p>')
    assert "font-family: Roboto, Arial, sans-serif; color:red" in out and 'face="Roboto, Arial, sans-serif"' in out
    assert 'font-family: Arial"' in out


def test_subject_is_sent_exactly_so_exchange_keeps_the_conversation(monkeypatch):
    class Capture:
        sent = []

        def send(self, to, subject, *a, **kw):
            self.sent.append(subject)

    cap = Capture()
    monkeypatch.setattr(sync, "provider", lambda account: cap)
    monkeypatch.setenv("MAILIA_DRY_RUN", "0")
    sync.send_mail("testbox", "a@b.fr", "TR: Document mutuelle ", "texte")
    sync.send_mail("testbox", "a@b.fr", "   ", "texte")
    assert cap.sent == ["TR: Document mutuelle ", "(sans objet)"]  # l'espace final de l'original est conservé


def test_thread_ref_points_to_the_original_message():
    mid = add_message(mid="testbox|INBOX|77")
    assert sync.thread_ref("testbox", mid) == ("reply", "INBOX", "77")
    assert sync.thread_ref("testbox", "fwd:" + mid) == ("forward", "INBOX", "77")
    assert sync.thread_ref("autre", mid) is None  # autre boîte : pas de rattachement possible
    assert sync.thread_ref("testbox", None) is None and sync.thread_ref("testbox", "fwd:inconnu") is None


def test_imap_reply_and_forward_carry_standard_threading_headers():
    from email.message import EmailMessage
    from mailia.providers.imap import thread_headers
    msg = EmailMessage()
    thread_headers(msg, " <b@x.fr> ", "<a@x.fr>")
    assert msg["In-Reply-To"] == "<b@x.fr>" and msg["References"] == "<a@x.fr> <b@x.fr>"
    thread_headers(msg, "<b@x.fr>", "<a@x.fr> <b@x.fr>")  # pas de doublon, pas de second en-tête
    assert msg["References"] == "<a@x.fr> <b@x.fr>" and len(msg.get_all("References")) == 1
    long = EmailMessage()
    thread_headers(long, "<n@x.fr>", " ".join(f"<{i}@x.fr>" for i in range(40)))
    assert len(long["References"].split()) == 20 and long["References"].endswith("<n@x.fr>")
    empty = EmailMessage()
    thread_headers(empty, "  ")
    assert "In-Reply-To" not in empty


def test_send_alert_is_raised_for_fallback_and_uncertain_only():
    import json
    from mailia import settings_ui
    from mailia.providers import ews
    assert settings_ui.send_alert() is None
    ews._stat("threaded", "ok")
    assert settings_ui.send_alert() is None  # un envoi réussi ne déclenche rien
    ews._stat("fallback", "Document mutuelle")
    a = settings_ui.send_alert()
    assert a["kind"] == "fallback" and "Document mutuelle" in settings_ui.alert_text(a) and "sans être rattaché" in settings_ui.alert_text(a)
    ews._stat("uncertain", "Contrat")
    a = settings_ui.send_alert()
    assert a["kind"] == "uncertain" and "Envoyés" in settings_ui.alert_text(a)
    assert json.loads(db.kv_get("send_stats")) == {"threaded": 1, "fallback": 1, "uncertain": 1}
    db.kv_set("send_alert", "")
    assert settings_ui.send_alert() is None


def test_imap_message_id_uses_the_senders_domain():
    from mailia.config import AccountCfg
    from mailia.providers.imap import ImapProvider
    cfg = AccountCfg(key="perso", label="Perso", kind="imap", email="marie@exemple.fr", login="marie@exemple.fr", password="x", host="imap.ionos.fr",
                     port=993, smtp_host="smtp.ionos.fr", smtp_port=587, ai_enabled=False)
    msg = ImapProvider(cfg).build("a@b.fr", "Objet", "texte")
    assert msg["Message-ID"].endswith("@exemple.fr>")


def test_threaded_send_rereads_the_draft_after_removing_copied_attachments():
    """Régression : détacher les images recopiées par le serveur change la clé de version du brouillon ; sans relecture, la mise à jour était
    refusée (ErrorIrresolvableConflict) et le message partait sans rattachement."""
    from mailia.config import AccountCfg
    from mailia.providers.ews import EwsProvider

    class Server:
        changekey = 1

    class Draft:
        id = "d1"

        def __init__(self, key):
            self.key = key
            self.attachments = [type("A", (), {"name": "image001.png"})(), type("A", (), {"name": "image002.png"})()]
            self.sent = False
            self.attached = []

        def detach(self, items):
            Server.changekey += 1  # le serveur change la clé ; CET objet garde l'ancienne
            self.attachments = []

        def attach(self, a):
            self.attached.append(a)

        def save(self, update_fields=None):
            if self.key != Server.changekey:
                raise RuntimeError("ErrorIrresolvableConflict")

        def send(self):
            self.sent = True

    drafts_seen = []

    class Drafts:
        def get(self, id):
            d = Draft(Server.changekey)
            drafts_seen.append(d)
            return d

    class Original:
        def create_reply(self, **kw):
            return type("R", (), {"save": lambda s, folder: type("S", (), {"id": "d1"})()})()

    class Account:
        drafts = Drafts()

    cfg = AccountCfg(key="exchange", label="Pro", kind="ews", email="a@b.fr", login="a@b.fr", password="x", ai_enabled=True, host="h")
    p = EwsProvider(cfg)
    p._account = Account()
    p._folder = lambda key: type("F", (), {"get": lambda s, id: Original()})()
    assert p._send_threaded(("reply", "INBOX", "1"), "RE: x", "", [], [], [], None, "<p>ok</p>", None) is True
    assert drafts_seen[-1].sent and len(drafts_seen) == 2  # relu une seconde fois après la suppression des pièces jointes


def test_incremental_sync_marks_bodyless_mail_as_needing_details():
    """Régression : la synchro incrémentale d'Exchange livre un mail sans corps ni destinataires ; il ne doit pas être pris pour un mail vide
    (affichage « message vide » et résumé de l'IA faux)."""
    from datetime import datetime, timezone
    from exchangelib import EWSDateTime, Message
    from mailia.config import AccountCfg
    from mailia.providers.ews import EwsProvider

    item = Message(subject="RE: sujet", datetime_received=EWSDateTime.from_datetime(datetime(2026, 10, 13, 8, 53, tzinfo=timezone.utc)), is_read=False)

    class Folder:
        item_sync_state = "s2"

        def sync_items(self, **kw):
            return [("create", item)]

    cfg = AccountCfg(key="exchange", label="Pro", kind="ews", email="a@b.fr", login="a@b.fr", password="x", ai_enabled=True, host="h")
    d = EwsProvider(cfg)._changes(Folder(), "s1", {})
    m = d.upserts[0]
    assert m.attachments is None and m.html is None  # -> att_json / body_html NULL en base -> sync.needs_details() est vrai


def test_autolink_turns_web_addresses_into_links_without_touching_the_rest():
    from mailia.htmlmail import autolink
    out = autolink("<p>Voir https://exemple.fr/page?a=1&amp;b=2, puis www.exemple.fr. Merci</p>")
    assert '<a href="https://exemple.fr/page?a=1&amp;b=2">https://exemple.fr/page?a=1&amp;b=2</a>,' in out  # la virgule reste hors du lien
    assert '<a href="https://www.exemple.fr">www.exemple.fr</a>. Merci' in out  # www. : lien en https, point final exclu
    assert autolink('<a href="https://deja.fr">https://deja.fr</a>') == '<a href="https://deja.fr">https://deja.fr</a>'  # déjà un lien
    assert autolink('<img src="https://x.fr/i.png"><a href="http://a.fr">http://a.fr</a>') == '<img src="https://x.fr/i.png"><a href="http://a.fr">http://a.fr</a>'
    assert autolink("<p>http://x.fr&nbsp;suite</p>") == '<p><a href="http://x.fr">http://x.fr</a>&nbsp;suite</p>'  # l'espace insécable n'entre pas dans l'adresse
    assert '<a href="https://fr.wikipedia.org/wiki/Paris_(ville)">' in autolink("https://fr.wikipedia.org/wiki/Paris_(ville)")  # parenthèse de l'adresse conservée
    assert autolink("(voir https://exemple.fr)").endswith('</a>)')  # parenthèse de la phrase exclue
    assert autolink("<p>écrire à marie@exemple.fr, pas de lien pour une adresse e-mail</p>") == "<p>écrire à marie@exemple.fr, pas de lien pour une adresse e-mail</p>"
    assert autolink("sans adresse") == "sans adresse" and autolink("") == ""


def test_brief_summary_failure_is_retried_instead_of_cached_all_day(monkeypatch):
    """Régression : une coupure réseau au premier affichage du matin laissait « Synthèse indisponible » toute la journée (la synthèse était marquée à jour)."""
    import json
    import time
    from mailia import ai, queries
    monkeypatch.setenv("AI_ENABLED", "1")
    add_message(mid="testbox|INBOX|50", subject="À traiter", is_read=0, ai_at="2026-10-14T08:00:00+00:00", category="action", priority=3, needs_reply=1)

    def boom(items):
        raise ai.AIError("Erreur API Claude : Connection error.")

    monkeypatch.setattr(ai, "brief_structured", boom)
    started = []
    monkeypatch.setattr(queries, "_refresh_headline", lambda sig, items: started.append(sig))
    queries.brief()                                   # premier affichage : tentative de synthèse
    assert len(started) == 1
    queries._headline_lock.acquire()
    queries._headline_work("sigX", [])                # elle échoue
    assert "Synthèse indisponible" in json.loads(db.kv_get("brief:headline"))["intro"]
    assert db.kv_get("brief:sig") != "sigX"           # PAS marquée à jour
    queries.brief()                                   # juste après : on ne harcèle pas l'API
    assert len(started) == 1
    db.kv_set("brief:fail_at", str(time.time() - queries.RETRY_AFTER - 1))
    queries.brief()                                   # plus tard : nouvel essai automatique
    assert len(started) == 2
    monkeypatch.setattr(ai, "brief_structured", lambda items: {"intro": "Tout va bien.", "urgent": [], "waiting": [], "info": ""})
    queries._headline_lock.acquire()
    queries._headline_work("sigY", [])
    assert json.loads(db.kv_get("brief:headline"))["intro"] == "Tout va bien." and db.kv_get("brief:sig") == "sigY" and db.kv_get("brief:fail_at") == ""


# ---------- IMAP : compteurs et alertes d'envoi, comme pour Exchange ----------
def _imap_for_send(monkeypatch, sent_folder="Sent", box_error=None, full_error=None):
    from types import SimpleNamespace
    from mailia.config import AccountCfg
    from mailia.providers import imap

    cfg = AccountCfg(key="perso", label="Perso", kind="imap", email="moi@exemple.fr", login="moi@exemple.fr", password="x", host="imap.exemple.fr",
                     port=993, tls="ssl", smtp_host="smtp.exemple.fr", smtp_port=587, ai_enabled=False)
    p = imap.ImapProvider(cfg)
    sent = []

    class Smtp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def send_message(self, msg):
            sent.append(msg)

    class Box:
        def __enter__(self):
            if box_error:
                raise box_error
            return self

        def __exit__(self, *a):
            return False

        folder = SimpleNamespace(list=lambda: [SimpleNamespace(name=sent_folder, flags=("\\Sent",))] if sent_folder else [])

        def append(self, *a, **k):
            pass

    monkeypatch.setattr(p, "smtp", lambda: Smtp())
    monkeypatch.setattr(p, "_box", lambda folder="INBOX": Box())

    def full(folder, uid):
        if full_error:
            raise full_error
        return SimpleNamespace(headers={"message-id": ("<orig@x>",), "references": ()})
    monkeypatch.setattr(p, "_full", full)
    return p, sent


def _stats():
    import json
    from mailia import db
    return json.loads(db.kv_get("send_stats") or "{}"), db.kv_get("send_alert") or ""


def test_imap_reply_counts_as_threaded_without_alert(monkeypatch):
    p, sent = _imap_for_send(monkeypatch)
    p.send("a@b.fr", "Re: Devis", "texte", ref=("x", "INBOX", "1"))
    stats, alert = _stats()
    assert sent and stats == {"threaded": 1} and alert == ""


def test_imap_reply_without_original_headers_raises_an_alert(monkeypatch):
    p, sent = _imap_for_send(monkeypatch, full_error=OSError("introuvable"))
    p.send("a@b.fr", "Re: Devis", "texte", ref=("x", "INBOX", "1"))
    stats, alert = _stats()
    assert sent and stats.get("fallback") == 1 and "fallback" in alert  # le message part quand même


def test_imap_missing_sent_folder_or_copy_failure_raises_an_alert(monkeypatch):
    p, sent = _imap_for_send(monkeypatch, sent_folder=None)
    p.send("a@b.fr", "Hello", "texte")
    assert _stats()[0].get("nocopy") == 1 and sent
    from mailia import db
    db.kv_set("send_alert", "")
    p2, sent2 = _imap_for_send(monkeypatch, box_error=OSError("réseau"))
    p2.send("a@b.fr", "Hello 2", "texte")
    stats, alert = _stats()
    assert stats.get("nocopy") == 2 and "nocopy" in alert and sent2
