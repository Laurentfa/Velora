import json
import re
import subprocess
import sys
from pathlib import Path

from mailia import autoconfig, db, i18n

ROOT = Path(__file__).resolve().parent.parent
XML = b"""<clientConfig><emailProvider id="x"><displayName>Chez Moi</displayName>
<incomingServer type="imap"><hostname>imap.chezmoi.fr</hostname><port>143</port><socketType>STARTTLS</socketType></incomingServer>
<outgoingServer type="smtp"><hostname>smtp.chezmoi.fr</hostname><port>465</port><socketType>SSL</socketType></outgoingServer></emailProvider></clientConfig>"""


def test_autoconfig_parse_and_known_providers():
    cfg = autoconfig.parse_autoconfig(XML)
    assert cfg == {"host": "imap.chezmoi.fr", "port": 143, "tls": "starttls", "smtp_host": "smtp.chezmoi.fr", "smtp_port": 465, "name": "Chez Moi"}
    assert autoconfig.parse_autoconfig(b"<pas du xml") is None
    g = autoconfig.detect("x@gmail.com")
    assert g["host"] == "imap.gmail.com" and g["oauth"] == "google" and "application" in g["help"]
    assert autoconfig.detect("bad")["error"]


def test_i18n_templates_and_server_side():
    assert i18n._template_regex("Résumé impossible : {0}") == "Résumé impossible : ([\\s\\S]+?)"
    assert i18n._template_regex("Brouillons") is None
    db.kv_set("ui_lang", "en")
    assert i18n.tr("Enregistrer") == "Save" and i18n.tr("Texte jamais traduit") == "Texte jamais traduit"
    assert "window.MAILIA_I18N" in i18n.client_script()
    db.kv_set("ui_lang", "fr")
    assert i18n.client_script() == "" and i18n.tr("Enregistrer") == "Enregistrer"


def test_translation_coverage_of_interface_strings():
    """Les textes d'interface (extraits du code) doivent presque tous avoir leur traduction : relancer tools/make_locale.py sinon."""
    out = subprocess.run([sys.executable, "tools/extract_strings.py"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    strings = [s for s in json.loads(out) if not re.search(r"\\|\(\?|\[\^|\[a-|\|[a-z]", s) and not (" " not in s and len(s) < 4) and not s.isupper()]
    have = json.loads((ROOT / "mailia" / "locales" / "en.json").read_text(encoding="utf-8"))
    missing = [s for s in strings if s not in have]
    assert len(missing) / max(1, len(strings)) < 0.03, f"{len(missing)} textes sans traduction, par exemple : {missing[:8]}"
