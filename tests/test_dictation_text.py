"""Texte dicté : nettoyage local, vocabulaire personnel (noms de vos contacts) et correction des noms propres."""
import asyncio
import queue
from datetime import datetime

import pytest
from nicegui import events, ui

import main
from mailia import db, dictation, dictation_text, dictation_vocab, sync, whisper_engine

clean = dictation_text.clean


def test_fillers_and_stutters_are_removed():
    assert clean("euh bonjour, euh, je je voudrais savoir si le le rendez-vous est confirmé.") == "Bonjour, je voudrais savoir si le rendez-vous est confirmé."
    assert clean("hmm... alors, heu, on y va ?") == "Alors, on y va ?"


def test_legitimate_repetitions_are_kept():
    assert clean("Vous vous êtes trompé. Nous nous voyons demain.") == "Vous vous êtes trompé. Nous nous voyons demain."
    assert clean("Il est très très gentil.") == "Il est très très gentil."


def test_capital_after_a_full_stop_but_not_after_an_abbreviation():
    assert clean("c'est fait. merci beaucoup. à demain") == "c'est fait. Merci beaucoup. À demain"  # le début reste tel quel : la phrase peut continuer un morceau précédent
    assert clean("c'est fait. merci beaucoup.") == "C'est fait. Merci beaucoup."
    assert clean("Voir etc. et puis M. dupont arrive") == "Voir etc. et puis M. dupont arrive"


def test_spaces_before_punctuation():
    assert clean("le prix est de 3,5 euros , ok .") == "Le prix est de 3,5 euros, ok."
    assert clean("") == "" and clean("   ") == ""


NAMES = ["Quentin Dubreuil", "Aurélie Kerjean", "Velora", "Ventiléa", "Apneassist", "Martin"]


def test_near_misses_of_known_names_are_corrected():
    c = dictation_vocab.correct
    assert c("Merci à Madame Kerjan et Quentin Dubreuill.", NAMES) == "Merci à Madame Kerjean et Quentin Dubreuil."
    assert c("On parle de Ventilea, puis d'apnéassist.", NAMES) == "On parle de Ventiléa, puis d'Apneassist."
    assert c("Bonjour Aurelie Kerjean,", NAMES) == "Bonjour Aurélie Kerjean,"


def test_a_common_word_is_never_turned_into_a_name():
    c = dictation_vocab.correct
    assert c("Le marin voit la mer.", NAMES) == "Le marin voit la mer."
    assert c("Il martèle son avis.", NAMES) == "Il martèle son avis."
    assert c("Appelez Quentin Dubreuil, svp.", NAMES) == "Appelez Quentin Dubreuil, svp."
    assert c("rien à corriger ici", []) == "rien à corriger ici"


def test_vocabulary_comes_from_you_your_mailboxes_and_your_contacts():
    db.contact_save("t.leroy@exemple.fr", "Thomas LEROY - VENTILEA", "manual")
    db.contact_favorite("t.leroy@exemple.fr", True)
    db.contact_save("a@x.fr", "Claire Martin", "manual")
    db.contact_save("sans-nom@exemple.fr", "", "manual")
    terms = dictation_vocab.terms()
    assert "Velora" in terms and "Thomas Leroy" in terms and "Claire Martin" in terms
    assert terms.index("Thomas Leroy") < terms.index("Claire Martin")  # favoris d'abord
    assert not any("@" in t for t in terms)


def test_whisper_prompt_is_short_and_lists_names():
    p = dictation_vocab.whisper_prompt(["Quentin Dubreuil", "Velora"])
    assert p == "Noms propres : Quentin Dubreuil, Velora."
    assert dictation_vocab.whisper_prompt([]) is None
    many = [f"Nom{i:03d}Prenom" for i in range(200)]
    assert len(dictation_vocab.whisper_prompt(many)) <= dictation_vocab.MAX_PROMPT_CHARS + 30


def test_whisper_session_gives_the_prompt_to_the_engine(monkeypatch):
    db.contact_save("a@x.fr", "Quentin Dubreuil", "manual")
    got = {}

    def factory(path):
        got["prompt"] = sess.prompt
        return lambda audio: ""

    class Mic:
        def open(self):
            pass

        def read(self, timeout=0.3):
            return None

        def close(self):
            pass
    monkeypatch.setattr(whisper_engine, "keep_runner", lambda *a, **k: None)
    sess = whisper_engine.WhisperSession("rapide", mic=Mic(), runner_factory=factory)
    sess.start()
    assert sess.events.get(timeout=10)["t"] == "loading" and sess.events.get(timeout=10)["t"] == "ready"
    sess.stop()
    assert sess.events.get(timeout=10)["t"] == "done"
    assert "Quentin Dubreuil" in got["prompt"]


@pytest.fixture
def box(monkeypatch):
    monkeypatch.setattr(sync, "run_cycle", lambda: None)
    ui.page("/")(main.index_page)
    db.kv_set("brief_day", datetime.now().strftime("%Y-%m-%d"))
    seen = []
    real = main.Mailbox.__init__
    monkeypatch.setattr(main.Mailbox, "__init__", lambda self, *a, **k: (real(self, *a, **k), seen.append(self))[0])
    return seen


async def test_the_dictated_instruction_is_cleaned_and_names_corrected(user, box, monkeypatch) -> None:
    class Fake:
        def __init__(self):
            self.events = queue.Queue()
            self.finished = False

        def start(self):
            self.events.put({"t": "ready"})

        def stop(self):
            self.events.put({"t": "done"})

        def kill(self):
            pass
    sess = Fake()
    db.contact_save("a@x.fr", "Aurélie Kerjean", "manual")
    monkeypatch.setattr(dictation, "usable", lambda: True)
    monkeypatch.setattr(dictation, "engine", lambda: "apple")
    monkeypatch.setattr(dictation, "check", lambda locale="fr-FR": {"available": True, "installed": True})
    monkeypatch.setattr(dictation, "new_session", lambda: sess)
    await user.open("/")
    b = box[-1]
    b.new_message()
    await asyncio.sleep(0.4)
    (el,) = user.find(marker="dictate-ins").elements
    with user.client:
        for listener in el._event_listeners.values():
            if listener.type == "click":
                events.handle_event(listener.handler, events.ClickEventArguments(sender=el, client=user.client))
    await asyncio.sleep(0.5)
    sess.events.put({"t": "final", "text": "euh relancer Madame Kerjan pour le le devis."})
    await asyncio.sleep(0.6)
    assert b.draft["ins"] == "Relancer Madame Kerjean pour le devis."
