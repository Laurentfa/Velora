"""Règles de tri du serveur Exchange : lecture, plan de modifications, écriture sûre, aperçu local, suggestions, vue."""
import asyncio
import json
from datetime import datetime, timezone

import pytest
from nicegui import ui

import main
from tests.conftest import add_message
from mailia import config, db, queries, server_rules as sr, sync, ui_server_rules

NS = ('xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" xmlns:m="http://schemas.microsoft.com/exchange/services/2006/messages" '
      'xmlns:t="http://schemas.microsoft.com/exchange/services/2006/types"')
FOLDERS = {"FID-OBOT": "Boîte de réception/OdooBot", "FID-COMPTA": "Compta", "FID-NEWS": "Newsletters"}


def rule_xml_live(rid, prio, name, conds, actions, enabled="true", exceptions=""):
    return (f"<t:Rule><t:RuleId>{rid}</t:RuleId><t:DisplayName>{name}</t:DisplayName><t:Priority>{prio}</t:Priority><t:IsEnabled>{enabled}</t:IsEnabled>"
            f"<t:Conditions>{conds}</t:Conditions>{exceptions}<t:Actions>{actions}</t:Actions></t:Rule>")


def rules_response(*rules) -> bytes:
    return (f'<s:Envelope {NS}><s:Body><m:GetInboxRulesResponse><m:ResponseCode>NoError</m:ResponseCode><m:RuleCollection>{"".join(rules)}</m:RuleCollection>'
            f'</m:GetInboxRulesResponse></s:Body></s:Envelope>').encode()


LIVE_XML = rules_response(
    rule_xml_live("R1", 1, "LinkedIn", "<t:ContainsSenderStrings><t:String>LINKEDIN.COM</t:String></t:ContainsSenderStrings>", "<t:Delete>true</t:Delete><t:StopProcessingRules>true</t:StopProcessingRules>"),
    rule_xml_live("R2", 2, "Gestis", '<t:FromAddresses><t:Address><t:Name>Prescription - GESTIS</t:Name></t:Address></t:FromAddresses>',
                  '<t:MoveToFolder><t:FolderId Id="FID-OBOT"/></t:MoveToFolder>'),
    rule_xml_live("R3", 3, "Odoo", "<t:ContainsSubjectStrings><t:String>Alerte</t:String><t:String>Récap</t:String></t:ContainsSubjectStrings>",
                  '<t:MoveToFolder><t:FolderId Id="FID-OBOT"/></t:MoveToFolder><t:MarkAsRead>true</t:MarkAsRead>', enabled="false"))


def live():
    return sr.parse_rules(LIVE_XML, FOLDERS)


class FakeClient:
    """Serveur Exchange imaginaire : garde les règles en mémoire et applique les opérations reçues."""

    def __init__(self):
        self.rules = live()
        self.sent = []

    def folders(self):
        return dict(FOLDERS)

    def inbox_rules(self, folders=None):
        return json.loads(json.dumps(self.rules))

    def update_rules(self, ops):
        self.sent.append(ops)
        by_id = {r["id"]: r for r in self.rules}
        for kind, payload in ops:
            if kind == "delete":
                self.rules = [r for r in self.rules if r["id"] != payload]
            elif kind == "set":
                rid = payload.split("<t:RuleId>")[1].split("</t:RuleId>")[0]
                r = by_id[rid]
                r["name"] = payload.split("<t:DisplayName>")[1].split("</t:DisplayName>")[0]
                r["enabled"] = "<t:IsEnabled>true" in payload
                r["order"] = int(payload.split("<t:Priority>")[1].split("</t:Priority>")[0])
        self.rules.sort(key=lambda r: r["order"])


def test_rules_are_parsed_described_and_flagged_editable() -> None:
    r = live()
    assert [x["name"] for x in r] == ["LinkedIn", "Gestis", "Odoo"] and r[2]["enabled"] is False
    assert sr.describe(r[0]) == "Si l'expéditeur contient « LINKEDIN.COM » → le mettre à la corbeille, ne pas appliquer les règles suivantes."
    assert "Compta" not in sr.describe(r[1]) and "OdooBot" in sr.describe(r[1]) and "le message vient de" in sr.describe(r[1])
    assert sr.editable(r[0]) and not sr.editable(r[1]) and sr.editable(r[2])  # « vient de » (FromAddresses) : lecture seule


def test_unchanged_plan_is_empty_and_toggle_reuses_original_xml() -> None:
    r = live()
    assert sr.plan(r, sr.desired_from(r), FOLDERS)["ops"] == []
    d = sr.desired_from(r)
    d[1]["enabled"] = False  # la règle « Gestis » n'est pas modifiable, mais s'active et se désactive
    p = sr.plan(r, d, FOLDERS)
    assert [o[0] for o in p["ops"]] == ["set"] and "FromAddresses" in p["ops"][0][1] and "<t:IsEnabled>false" in p["ops"][0][1]


def test_delete_reorder_and_create_make_the_right_operations() -> None:
    r = live()
    d = sr.desired_from(r)
    removed = d.pop(0)
    p = sr.plan(r, d, FOLDERS)
    assert p["ops"][0] == ("delete", removed["id"]) and any("sera supprimée" in w for w in p["warnings"])
    d = sr.desired_from(r)
    d[0], d[2] = d[2], d[0]
    assert [o[0] for o in sr.plan(r, d, FOLDERS)["ops"]] == ["set", "set"]  # la règle du milieu garde sa place
    d = sr.desired_from(r) + [{"id": None, "name": "Factures", "enabled": True, "stop_processing": False,
                                "conditions": [{"type": "subject_contains", "values": ["facture"]}], "exceptions": [],
                                "actions": [{"type": "move_to_folder", "folder_id": "FID-COMPTA", "value": "Compta"}]}]
    p = sr.plan(r, d, FOLDERS)
    assert p["ops"][0][0] == "create" and "<t:String>facture</t:String>" in p["ops"][0][1] and "FID-COMPTA" in p["ops"][0][1]


def test_dangerous_or_invalid_rules_are_refused_or_warned() -> None:
    r = live()
    new = {"id": None, "name": "Tout", "enabled": True, "stop_processing": False, "conditions": [], "exceptions": [], "actions": [{"type": "delete"}]}
    with pytest.raises(sr.RulesError, match="aucun critère"):
        sr.plan(r, sr.desired_from(r) + [new], FOLDERS)
    bad_folder = {**new, "conditions": [{"type": "subject_contains", "values": ["x"]}], "actions": [{"type": "move_to_folder", "folder_id": "INCONNU"}]}
    with pytest.raises(sr.RulesError, match="Dossier de destination inconnu"):
        sr.plan(r, sr.desired_from(r) + [bad_folder], FOLDERS)
    risky = {**new, "conditions": [{"type": "body_contains", "values": ["ok"]}]}
    w = sr.plan(r, sr.desired_from(r) + [risky], FOLDERS)["warnings"]
    assert any("supprimera les messages" in x for x in w) and any("termes très courts" in x for x in w)
    with pytest.raises(sr.RulesError, match="n'existe plus"):
        sr.plan(r, [{**sr.desired_from(r)[0], "id": "FANTOME"}], FOLDERS)


def test_update_response_errors_are_reported() -> None:
    ok = f'<s:Envelope {NS}><s:Body><m:UpdateInboxRulesResponse><m:ResponseCode>NoError</m:ResponseCode></m:UpdateInboxRulesResponse></s:Body></s:Envelope>'.encode()
    sr.parse_update_response(ok)
    bad = (f'<s:Envelope {NS}><s:Body><m:UpdateInboxRulesResponse><m:ResponseCode>NoError</m:ResponseCode><m:RuleOperationErrors><t:RuleOperationError>'
           f'<t:OperationIndex>0</t:OperationIndex><t:ValidationErrors><t:RuleValidationError><t:Value>nom en double</t:Value></t:RuleValidationError></t:ValidationErrors>'
           f'</t:RuleOperationError></m:RuleOperationErrors></m:UpdateInboxRulesResponse></s:Body></s:Envelope>').encode()
    with pytest.raises(sr.RulesError, match="nom en double"):
        sr.parse_update_response(bad)
    with pytest.raises(sr.RulesError, match="entités"):
        sr.parse_rules(b"<!ENTITY a 'b'>" + LIVE_XML)


def test_apply_needs_confirmation_backs_up_checks_for_changes_and_verifies() -> None:
    c = FakeClient()
    d = sr.desired_from(c.rules)
    d[2]["enabled"] = True
    plan = sr.make_plan(c, d)
    with pytest.raises(sr.RulesError, match="Confirmation"):
        sr.apply_plan(c, plan["plan_id"], False)
    plan = sr.make_plan(c, d)
    c.rules[0]["name"] = "Modifié ailleurs"  # le serveur a changé depuis le plan
    with pytest.raises(sr.RulesError, match="ont changé"):
        sr.apply_plan(c, plan["plan_id"], True)
    assert c.sent == []  # rien n'a été envoyé
    c.rules[0]["name"] = "LinkedIn"
    plan = sr.make_plan(c, d)
    out = sr.apply_plan(c, plan["plan_id"], True, "Boîte")
    assert out["verified"] and c.rules[2]["enabled"] is True and c.sent
    saved = config.DATA_DIR / "rules_backups" / out["backup"]
    assert json.loads(saved.read_text(encoding="utf-8"))["inbox_rules"][2]["enabled"] is False  # la sauvegarde contient l'état d'avant
    with pytest.raises(sr.RulesError, match="expiré"):
        sr.apply_plan(c, plan["plan_id"], True)  # un plan ne s'applique qu'une fois


def test_local_preview_activity_and_suggestions() -> None:
    for i in range(6):
        add_message(f"testbox|INBOX|{i}", sender="news@boutique.fr", subject=f"Promo {i}", category="newsletter")
    add_message("testbox|INBOX|99", sender="marie@example.com", subject="Facture mars", has_att=1)
    rule = sr.clean_rule({"id": None, "name": "x", "conditions": [{"type": "subject_contains", "values": ["facture"]}, {"type": "has_attachment", "values": ["true"]}],
                          "actions": [{"type": "delete"}]})
    rows = ui_server_rules.local_recent("testbox", days=3650)
    assert [m["subject"] for m in rows if sr.matches_local(rule, m)] == ["Facture mars"]
    s = ui_server_rules.suggestions("testbox", live())
    assert s and s[0]["sender"] == "news@boutique.fr" and s[0]["count"] == 6
    covered = sr.parse_rules(rules_response(rule_xml_live("R9", 1, "Boutique", "<t:ContainsSenderStrings><t:String>boutique.fr</t:String></t:ContainsSenderStrings>", "<t:Delete>true</t:Delete>")), FOLDERS)
    assert ui_server_rules.suggestions("testbox", covered) == []  # déjà couvert par une règle
    with db.conn() as c:
        c.execute("INSERT INTO folders(account,key,name,path,role,sync) VALUES('testbox','FID-OBOT','OdooBot','OdooBot','',1)")
    add_message("testbox|FID-OBOT|1", folder="FID-OBOT", date=datetime.now(timezone.utc).isoformat())
    act = ui_server_rules.activity("testbox", live())
    assert act["R3"] == 1 and act["R1"] is None


@pytest.fixture
def box(monkeypatch):
    fake = FakeClient()
    monkeypatch.setattr(sync, "run_cycle", lambda: None)
    monkeypatch.setattr(config, "ai_enabled", lambda: True)
    monkeypatch.setattr(queries, "_refresh_headline", lambda sig, items: None)
    monkeypatch.setattr(sync.ACCOUNTS["testbox"], "kind", "ews")
    real_provider = sync.provider
    prov = type("P", (), {"rules_client": lambda self: fake, "calendar_events": lambda self, a, b: [], "tasks": lambda self: []})()
    monkeypatch.setattr(sync, "provider", lambda key: prov if key == "testbox" else real_provider(key))
    ui.page("/")(main.index_page)
    seen = []
    real = main.Mailbox.__init__
    monkeypatch.setattr(main.Mailbox, "__init__", lambda self, *a, **k: (real(self, *a, **k), seen.append(self))[0])
    return type("Box", (), {"fake": fake, "boxes": seen})()


async def test_the_rules_view_lists_rules_and_toggling_saves_with_a_backup(user, box) -> None:
    await user.open("/")
    b, fake = box.boxes[-1], box.fake
    await b.go("srules", "", "")
    await asyncio.sleep(1.0)
    await user.should_see("LinkedIn")
    await user.should_see("Gestis")
    await b.srules_toggle(live()[2], True)  # active la règle « Odoo »
    await asyncio.sleep(0.5)
    assert fake.rules[2]["enabled"] is True and fake.sent and list((config.DATA_DIR / "rules_backups").glob("avant_ecriture_*.json"))


async def test_deleting_a_rule_shows_the_plan_and_waits_for_confirmation(user, box) -> None:
    await user.open("/")
    b, fake = box.boxes[-1], box.fake
    await b.go("srules", "", "")
    await asyncio.sleep(1.0)
    task = asyncio.create_task(b.srules_delete(live()[0]))
    await asyncio.sleep(0.6)
    await user.should_see("Supprimer la règle « LinkedIn »")
    assert fake.sent == []  # rien n'est envoyé avant la confirmation
    user.find(marker="srules-confirm").click()
    await task
    assert [r["name"] for r in fake.rules] == ["Gestis", "Odoo"]


async def test_creating_a_rule_from_a_mail_prefills_the_sender(user, box) -> None:
    add_message("testbox|INBOX|1", sender="promo@boutique.fr", subject="Soldes")
    await user.open("/")
    b = box.boxes[-1]
    m = queries.get_message("testbox|INBOX|1")
    task = asyncio.create_task(b.srules_from_mail(m))
    await asyncio.sleep(1.0)
    await user.should_see("Nouvelle règle de tri")
    assert user.find(marker="srules-words").elements.pop().value == "promo@boutique.fr"
    user.find(marker="srules-save").click()
    await asyncio.sleep(0.3)
    await user.should_see("Nouvelle règle de tri")  # sans action, la fenêtre reste ouverte
    user.find(marker="srules-folder").elements.pop().set_value("FID-COMPTA")
    user.find(marker="srules-save").click()
    await asyncio.sleep(0.6)
    await user.should_see("Enregistrer sur le serveur Exchange ?")
    await user.should_see("Créer « boutique.fr »")
    user.find(marker="srules-confirm").click()
    await task


def test_the_blob_warning_names_the_rules_that_only_run_on_the_workstation() -> None:
    r = live()
    d = sr.desired_from(r)
    d[0]["enabled"] = False
    w = sr.plan(r, d, FOLDERS)["warnings"]
    assert any("Aucune règle propre au poste" in x for x in w)  # aucune règle locale détectée : on explique quand même le risque
    r[1]["not_supported"] = True
    w = sr.plan(r, d, FOLDERS)["warnings"]
    assert any("ne fonctionnent que sur le poste" in x and "« Gestis »" in x for x in w)
