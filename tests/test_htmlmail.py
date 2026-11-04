

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


def test_the_print_fragment_loads_nothing_from_the_internet_unless_images_are_allowed() -> None:
    from mailia import htmlmail
    clean = htmlmail.sanitize('<p style="background:url(https://t.example/x.png)">Bonjour</p><img src="https://t.example/p.gif"><img src="data:image/png;base64,AAA"><style>@import url(https://e.x/a.css); p{color:red}</style>')
    frag = htmlmail.build_print_fragment({"subject": "S <x>", "from": "A"}, clean, "", False)
    assert "t.example" not in frag and "e.x/a.css" not in frag and "data:image/png;base64,AAA" in frag and "S &lt;x&gt;" in frag
    assert "<script" not in frag and "Bonjour" in frag
    assert "t.example/p.gif" in htmlmail.build_print_fragment({"subject": "S"}, clean, "", True)  # images distantes autorisées par l'utilisateur
