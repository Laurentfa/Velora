

def test_the_print_page_shows_the_header_the_attachments_and_runs_nothing_from_the_mail() -> None:
    from mailia import htmlmail
    clean = htmlmail.sanitize('<p>Bonjour</p><script>alert(1)</script><img src="cid:logo">')
    doc = htmlmail.build_print_doc({"subject": "Objet <b>", "from": "A <a@x.fr>", "to": "b@x.fr", "date": "lundi 2 novembre 2026", "attachments": ["a.pdf", "b.pdf"]},
                                   clean, "", False, {"logo": "data:image/png;base64,AAA"})
    assert "alert(1)" not in doc and "data:image/png;base64,AAA" in doc and "Bonjour" in doc
    assert "Objet &lt;b&gt;" in doc and "a.pdf · b.pdf" in doc and "A &lt;a@x.fr&gt;" in doc
    assert "script-src &#x27;nonce-" in doc and "img-src data:;" in doc  # images distantes bloquées par défaut
    assert htmlmail.build_print_doc({"subject": "S"}, None, "ligne 1\n<b>texte</b>", True).count("<b>texte</b>") == 0  # le texte brut est échappé
    assert "img-src data: https: http:" in htmlmail.build_print_doc({"subject": "S"}, "<p>x</p>", "", True)


def test_the_hidden_html_title_of_a_message_is_not_shown_as_text():
    """Le titre caché d'un modèle de signature Outlook (« Signature Mails ») ne doit pas devenir la première ligne du message."""
    from mailia import htmlmail
    raw = "<html><head><title>Signature Mails</title></head><body><p>Coucou</p></body></html>"
    for out in (htmlmail.sanitize(raw), htmlmail.sanitize_quote(raw), htmlmail.sanitize_quote(raw, images=True), htmlmail.sanitize_outgoing(raw)):
        assert "Signature Mails" not in out and "Coucou" in out
