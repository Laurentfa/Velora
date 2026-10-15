from mailia import checks, db


def test_forgotten_attachment():
    w = checks.send_warnings("<p>Voir ci-joint le devis</p>", "Devis", "a@x.fr", "", "", 0, True)
    assert any("pièce jointe" in x for x in w)
    assert not any("pièce jointe" in x for x in checks.send_warnings("<p>Voir ci-joint</p>", "Devis", "a@x.fr", "", "", 1, True))


def test_empty_subject_and_many_recipients():
    w = checks.send_warnings("<p>ok</p>", "", ", ".join(f"u{i}@x.fr" for i in range(11)), "", "", 0, True)
    assert any("objet" in x.lower() for x in w) and any("11 destinataires" in x for x in w)


def test_new_recipient_but_not_myself():
    w = checks.send_warnings("<p>ok</p>", "S", "inconnu@zzz.fr, moi@example.org", "", "", 0, False, {"moi@example.org"})
    assert any("inconnu@zzz.fr" in x for x in w) and not any("moi@example.org" in x for x in w)
    db.contact_save("inconnu@zzz.fr", "Inconnu", "manual")
    assert not checks.send_warnings("<p>ok</p>", "S", "inconnu@zzz.fr", "", "", 0, False)


def test_can_be_disabled():
    db.kv_set("send_checks", "0")
    assert checks.send_warnings("<p>ci-joint</p>", "", "a@x.fr", "", "", 0) == []
