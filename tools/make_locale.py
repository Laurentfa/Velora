"""Traduit les textes d'interface en anglais avec Claude (Haiku) et écrit mailia/locales/en.json.
Usage : python tools/extract_strings.py > /tmp/strings.json && python tools/make_locale.py /tmp/strings.json en
Les entrées déjà présentes dans le fichier sont conservées (relecture manuelle possible) ; seules les nouvelles sont traduites."""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from mailia import ai, config, db  # noqa: E402

LANGS = {"en": "natural American English"}
GLOSSARY = """Glossary (use consistently): boîte mail → mailbox; mail/e-mail → email; Boîte de réception → Inbox; Brouillons → Drafts; Éléments envoyés → Sent;
Corbeille → Trash; Archive → Archive; non lu → unread; À traiter → To do; Reporter → Snooze; À relancer → Follow-up; Tâches → Tasks; Cci → Bcc;
Épinglé → Pinned; Brief du jour → Daily brief; Paramètres → Settings; pièce jointe → attachment; destinataire → recipient; expéditeur → sender;
Newsletters → Newsletters; Notifications → Notifications; Règles de tri → Sorting rules; Réponse IA → AI reply; Résumer → Summarize; hameçonnage → phishing;
désabonner → unsubscribe; numériser → scan; brouillon → draft; conversation → thread. Keep brand names unchanged: Velora, Exchange, IMAP, SMTP, Claude, Anthropic, Haiku, Gmail, Outlook."""


def translate_batch(items: list[str], lang: str) -> dict[str, str]:
    system = (f"You translate the user-interface texts of a French e-mail client into {LANGS[lang]}. {GLOSSARY}\n"
              "Rules: keep placeholders like {0}, {1} exactly as they are (same count, sensible position); keep emojis, symbols (✦ ✓ ✗ ⚑ …), "
              "ellipses, trailing punctuation and capitalization style; be concise like UI text; do not add explanations. "
              "The input is a numbered list, one text per line (a text never spans lines). Reply with exactly one line per input line, "
              "formatted as  N|translation  (same number N), nothing else.")
    numbered = "\n".join(f"{i}|{t.replace(chr(10), ' ')}" for i, t in enumerate(items, 1))
    raw = ai._ask(system, numbered, model=config.model("ASK"), max_tokens=8000)
    out: dict[str, str] = {}
    for line in raw.splitlines():
        m = re.match(r"^\s*(\d+)\s*\|\s*(.+?)\s*$", line)
        if m and 1 <= int(m.group(1)) <= len(items):
            out[items[int(m.group(1)) - 1]] = m.group(2)
    return out


def main() -> None:
    src, lang = Path(sys.argv[1]), sys.argv[2]
    out = ROOT / "mailia" / "locales" / f"{lang}.json"
    strings = [s for s in json.loads(src.read_text(encoding="utf-8"))
               if not re.search(r"\\|\(\?|\[\^|\[a-|\|[a-z]", s) and not (" " not in s and len(s) < 4) and not s.isupper()]
    have = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {}
    todo = [s for s in strings if s not in have]
    print(f"{len(strings)} textes, {len(todo)} à traduire", file=sys.stderr)
    db.init()
    for i in range(0, len(todo), 45):
        batch = todo[i:i + 45]
        try:
            got = translate_batch(batch, lang)
        except Exception as e:
            print("lot", i, "échec :", e, file=sys.stderr)
            continue
        for fr in batch:
            en = got.get(fr)
            if not en:
                continue
            if sorted(re.findall(r"\{\d+\}", fr)) != sorted(re.findall(r"\{\d+\}", en)):
                print("  placeholders différents, ignoré :", fr[:60], "->", en[:60], file=sys.stderr)
                continue
            have[fr] = en
        out.write_text(json.dumps(have, ensure_ascii=False, indent=0, sort_keys=True), encoding="utf-8")
        print(f"  lot {i // 45 + 1}/{(len(todo) + 44) // 45} ok", file=sys.stderr)
    print(f"{len(have)} traductions dans {out}", file=sys.stderr)


if __name__ == "__main__":
    main()
