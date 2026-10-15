from mailia import backup, db


def test_template_recipients_roundtrip_and_old_templates_have_none():
    tid = db.template_save(None, "Point hebdo", "Point", "<p>Bonjour</p>", "a@x.fr, b@x.fr", "c@x.fr", "d@x.fr")
    t = next(x for x in db.templates_list() if x["id"] == tid)
    assert (t["to_addrs"], t["cc"], t["bcc"]) == ("a@x.fr, b@x.fr", "c@x.fr", "d@x.fr")
    old = db.template_save(None, "Ancien", "", "<p>x</p>")  # appel sans destinataires : comme avant
    t2 = next(x for x in db.templates_list() if x["id"] == old)
    assert (t2["to_addrs"], t2["cc"], t2["bcc"]) == ("", "", "")
    db.template_save(tid, "Point hebdo", "Point", "<p>Bonjour</p>", "", "", "")  # on peut aussi les retirer
    assert next(x for x in db.templates_list() if x["id"] == tid)["to_addrs"] == ""


def test_old_database_without_columns_is_migrated():
    with db.conn() as c:
        c.execute("DROP TABLE templates")
        c.execute("CREATE TABLE templates (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, subject TEXT DEFAULT '', html TEXT DEFAULT '', updated TEXT)")
        c.execute("INSERT INTO templates(name,subject,html) VALUES('vieux','s','<p>h</p>')")
    db.init()
    t = db.templates_list()[0]
    assert t["name"] == "vieux" and t["to_addrs"] == "" and t["bcc"] == ""


def test_backup_keeps_template_recipients():
    db.template_save(None, "Avec copie", "S", "<p>h</p>", "a@x.fr", "b@x.fr", "c@x.fr")
    data = backup.export_data()
    with db.conn() as c:
        c.execute("DELETE FROM templates")
    backup.import_data(data)
    t = next(x for x in db.templates_list() if x["name"] == "Avec copie")
    assert (t["to_addrs"], t["cc"], t["bcc"]) == ("a@x.fr", "b@x.fr", "c@x.fr")
    old_style = {"app": "Mail IA", "kv": {}, "templates": [{"name": "Sans", "subject": "", "html": "<p>x</p>"}]}  # sauvegarde d'avant : sans les champs
    backup.import_data(old_style)
    assert any(x["name"] == "Sans" for x in db.templates_list())
