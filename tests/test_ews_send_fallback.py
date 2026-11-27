"""Envoi d'une réponse Exchange : nouvel essai rattaché après une erreur du serveur, puis envoi simple avec les en-têtes de réponse (aucun vrai envoi : tout est factice)."""
from types import SimpleNamespace

from mailia.providers import ews


class FakeMsg:
    sent = 0
    in_reply_to = None
    references = None

    def send(self):
        type(self).sent += 1


class Prov(ews.EwsProvider):
    def __init__(self, fail_times: int, headers=("<orig@x>", "<a@x> <b@x>")):
        self.fail_times, self.calls, self.forgot, self.stats = fail_times, 0, 0, []
        self._account = SimpleNamespace(root=SimpleNamespace(clear_cache=lambda: None), fetch=lambda ids, only_fields: [SimpleNamespace(message_id=headers[0], references=headers[1])] if headers else [Exception("introuvable")])
        self._fmap = {}
        self.built = None

    def _send_threaded(self, *a, **k):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise RuntimeError("ErrorItemNotFound")
        return True

    def _forget_folders(self):
        self.forgot += 1

    def build(self, *a, **k):
        self.built = FakeMsg()
        return self.built


def run(monkeypatch, prov, ref=("reply", "INBOX", "id1")):
    stats = []
    monkeypatch.setattr(ews, "_stat", lambda kind, subject="": stats.append(kind))
    FakeMsg.sent = 0
    prov.send("a@b.fr", "RE: x", "texte", ref=ref)
    return stats


def test_retry_then_threaded(monkeypatch):
    p = Prov(fail_times=1)
    assert run(monkeypatch, p) == ["threaded"] and p.calls == 2 and p.forgot == 1 and p.built is None  # rattaché au second essai : aucun envoi simple


def test_fallback_keeps_reply_headers(monkeypatch):
    p = Prov(fail_times=2)
    assert run(monkeypatch, p) == ["fallback"] and p.calls == 2 and FakeMsg.sent == 1
    assert p.built.in_reply_to == "<orig@x>" and p.built.references == "<a@x> <b@x> <orig@x>"


def test_fallback_without_original(monkeypatch):
    p = Prov(fail_times=2, headers=None)
    assert run(monkeypatch, p) == ["fallback"] and FakeMsg.sent == 1 and p.built.in_reply_to is None  # message d'origine illisible : envoi simple, comme avant


def test_forward_gets_no_reply_headers(monkeypatch):
    p = Prov(fail_times=2)
    assert run(monkeypatch, p, ref=("forward", "INBOX", "id1")) == ["fallback"] and p.built.in_reply_to is None


def test_already_sent_never_retried(monkeypatch):
    p = Prov(fail_times=0)
    p._send_threaded = lambda *a, **k: (_ for _ in ()).throw(ews._AlreadySent("x"))
    stats = []
    monkeypatch.setattr(ews, "_stat", lambda kind, subject="": stats.append(kind))
    try:
        p.send("a@b.fr", "s", "t", ref=("reply", "INBOX", "id1"))
        assert False
    except ews._AlreadySent:
        pass
    assert stats == ["uncertain"] and p.forgot == 0 and p.built is None  # jamais de second envoi quand on ignore si le premier est parti


def test_forget_folders_clears_cache():
    cleared = []
    p = ews.EwsProvider.__new__(ews.EwsProvider)
    p._account = SimpleNamespace(root=SimpleNamespace(clear_cache=lambda: cleared.append(1)), drafts="vieux", inbox="vieux")
    p._fmap = {"a": 1}
    p._forget_folders()
    assert cleared == [1] and not hasattr(p.account, "drafts") and p._fmap is None
