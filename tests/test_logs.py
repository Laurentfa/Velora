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
