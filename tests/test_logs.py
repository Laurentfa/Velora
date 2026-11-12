from mailia import logs


def test_scrub_masks_secrets_and_addresses():
    raw = ("connexion marie.dupont@exemple.fr avec mia_AbCdEfGhIjKlMnOpQrStUvWxYz012345 et sk-ant-api03-abcdefghijkl "
           "Authorization: Bearer abcdef123456 password=hunter22222 jeton " + "A" * 60)
    out = logs.scrub(raw)
    for secret in ("marie.dupont", "AbCdEf", "sk-ant-api03-abc", "abcdef123456", "hunter22222", "A" * 60):
        assert secret not in out
    assert "***@exemple.fr" in out


def test_diagnostic_has_no_mail_content():
    logs.setup()
    import logging
    logging.getLogger("t").info("analyse de message@example.org")
    d = logs.diagnostic()
    assert "Velora" in d and "message@example.org" not in d and "***@example.org" in d


def test_a_console_that_cannot_encode_an_arrow_never_breaks_the_caller():
    """Régression (Windows, console cp1252) : log.info("… → OK") levait UnicodeEncodeError chez l'appelant, et la lecture des patients échouait."""
    import io
    import logging

    class Cp1252(io.TextIOWrapper):
        pass

    raw = io.BytesIO()
    stream = Cp1252(raw, encoding="cp1252", errors="strict")
    h = logs.ConsoleHandler(stream)
    log = logging.getLogger("test_console_arrow")
    log.propagate = False
    log.addHandler(h)
    try:
        log.warning("onglet Ventiléa → OK ✓")  # ne lève rien
        log.warning("suite")
    finally:
        log.removeHandler(h)
    stream.flush()
    text = raw.getvalue().decode("cp1252")
    assert "onglet Ventiléa ? OK ?" in text and "suite" in text  # les caractères inconnus sont remplacés, le reste s'écrit
