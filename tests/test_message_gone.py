"""Un message que le serveur ne connaît plus : on le dit au lieu de « le serveur ne répond pas », et la copie périmée est retirée."""
import pytest
from conftest import add_message

from mailia import db, queries, sync


class ErrorItemNotFound(Exception):
    pass


def test_a_missing_server_item_is_reported_as_gone_not_as_a_network_failure(monkeypatch) -> None:
    add_message("testbox|INBOX|9", subject="Fantôme", att_json=None)
    m = queries.get_message("testbox|INBOX|9")
    prov = type("P", (), {"details": lambda self, folder, uid: (_ for _ in ()).throw(ErrorItemNotFound("L'objet spécifié est introuvable"))})()
    monkeypatch.setattr(sync, "provider", lambda key: prov)
    with pytest.raises(sync.MessageGone):
        sync.ensure_details(m)


def test_other_errors_stay_errors(monkeypatch) -> None:
    add_message("testbox|INBOX|8", subject="Réseau", att_json=None)
    m = queries.get_message("testbox|INBOX|8")
    prov = type("P", (), {"details": lambda self, folder, uid: (_ for _ in ()).throw(ConnectionError("réseau coupé"))})()
    monkeypatch.setattr(sync, "provider", lambda key: prov)
    with pytest.raises(ConnectionError):
        sync.ensure_details(m)
