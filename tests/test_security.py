"""Avertissement « message suspect » : cas particuliers."""
from mailia import security


def test_exchange_system_notifications_are_not_flagged() -> None:
    s = "MicrosoftExchange329e71ec88ae4615bbc36ab6ce41109e@OPALECENTER.LOCAL"
    assert security.check(s, "Microsoft Outlook", "Non remis : Test")["level"] == ""
    assert security.check(s, "Microsoft Outlook", "Non remis : Test", auth={"authas": "anonymous"})["level"] == "suspect"


def test_fake_subscription_notice_from_an_unrelated_domain_is_flagged():
    body = "Prime Vidéo Formule annuelle Montant 69,90 EUR TTC Vous disposez de 14 jours pour modifier votre formule."
    res = security.check("mailing@ecell.in", "Information", "Récapitulatif de vos frais d'abonnement", "", body)
    assert res["level"] == "suspect" and "prime vidéo" in res["reasons"][0]
    assert security.check("no-reply@amazon.fr", "Amazon", "Abonnement", "", body)["level"] == ""
