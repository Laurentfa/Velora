"""Avertissement « message suspect » : cas particuliers."""
from mailia import security


def test_exchange_system_notifications_are_not_flagged() -> None:
    s = "MicrosoftExchange329e71ec88ae4615bbc36ab6ce41109e@OPALECENTER.LOCAL"
    assert security.check(s, "Microsoft Outlook", "Non remis : Test")["level"] == ""
    assert security.check(s, "Microsoft Outlook", "Non remis : Test", auth={"authas": "anonymous"})["level"] == "suspect"
