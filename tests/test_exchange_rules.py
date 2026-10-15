from exchangelib.properties import Actions, Conditions, MoveToFolder, FolderId

from mailia.providers import ews


def test_describe_rule_in_french():
    cond = Conditions(contains_sender_strings=["LINKEDIN.COM"], has_attachments=True)
    act = Actions(move_to_folder=MoveToFolder(folder_id=FolderId(id="abc")), mark_as_read=True, stop_processing_rules=True)
    assert ews._describe(cond, ews._COND, {}) == ["l'expéditeur contient « LINKEDIN.COM »", "il a une pièce jointe"]
    assert set(ews._describe(act, ews._ACT, {"abc": "Service DAP"})) == {
        "le déplacer vers « Service DAP »", "le marquer comme lu", "ne pas appliquer les règles suivantes"}
    assert ews._describe(None, ews._COND, {}) == []
