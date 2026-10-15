"""Demander à sa boîte : on retrouve les mails pertinents (recherche plein texte locale), puis l'IA répond en citant ses sources."""
import re

from . import ai, aipolicy, config, db, queries
from .textutil import normalize

STOP = set("""le la les un une des du de d l et ou mais donc or ni car que qui quoi dont où ce cet cette ces mon ma mes ton ta tes son sa ses
notre nos votre vos leur leurs je tu il elle on nous vous ils elles me te se lui y en au aux a ai as avons avez ont est sont été être avoir
dans sur sous par pour avec sans chez vers entre comme plus moins très tout tous toute toutes si ne pas non oui quel quelle quels quelles
quand combien comment pourquoi peux peut veux veut dit dite dire fait faire mail mails message messages email emails courrier boîte boite
reçu reçus reçue envoyé envoyés envoyée parlé parle parlait concernant propos sujet the of to and in is it for on with was were""".split())


def keywords(question: str) -> list[str]:
    words = re.findall(r"[0-9A-Za-zÀ-ÿ'’.-]{3,}", question or "")
    out: list[str] = []
    for w in words:
        w = w.strip(".-'’")
        if len(w) >= 3 and w.lower() not in STOP and w.lower() not in out:
            out.append(w.lower())
    return out[:10]


_QUOTE = re.compile(r"^(De ?:|From ?:|Le .{5,80} a écrit|On .{5,80} wrote|-{3,}\s*(Original|Message)|_{5,})", re.I)


def split_quote(body: str) -> tuple[str, str]:
    """(ce que l'expéditeur a écrit, message cité auquel il répond)."""
    lines = normalize(body).split("\n")
    for i, line in enumerate(lines):
        if i > 0 and _QUOTE.match(line.strip()):
            return "\n".join(lines[:i]).strip(), "\n".join(lines[i:]).strip()
    return "\n".join(lines).strip(), ""


def retrieve(question: str, limit: int = 12) -> list[dict]:
    """Les mails (toutes boîtes actives, tous dossiers suivis) qui contiennent les mots importants de la question, les mieux classés d'abord."""
    terms = keywords(question)
    with db.conn() as c:
        if terms:
            match = " OR ".join('"' + t.replace('"', "") + '"*' for t in terms)
            ids = [r[0] for r in c.execute("SELECT id FROM fts WHERE fts MATCH ? ORDER BY rank LIMIT 80", (match,))]
        else:
            ids = []
        if not ids:
            return []
        marks = ",".join("?" * len(ids))
        rows = {r["id"]: dict(r) for r in c.execute(
            f"SELECT id,account,folder,subject,sender,sender_name,date,body FROM messages WHERE id IN ({marks}) AND {queries.enabled_sql()}", ids)}
    allowed = [rows[i] for i in ids if i in rows and not aipolicy.is_excluded(rows[i])]  # boîtes et expéditeurs exclus : jamais envoyés à l'IA
    return allowed[:limit]


def answer(question: str) -> dict:
    """{'text': réponse, 'sources': [mails cités ou utilisés]}"""
    if not (question or "").strip():
        raise ai.AIError("Posez une question.")
    hits = retrieve(question)
    if not hits:
        return {"text": "Je n'ai trouvé aucun mail qui correspond à cette question. Essayez avec un nom, un numéro de dossier ou un mot précis.", "sources": []}
    blocks = []
    for n, m in enumerate(hits, 1):
        top, quoted = split_quote(m["body"] or "")
        block = f"[{n}] Mail de {m['sender_name'] or m['sender']} <{m['sender']}> daté du {m['date'][:10]}, objet « {m['subject']} »\n"
        block += f"TEXTE ÉCRIT PAR {m['sender_name'] or m['sender']} : {top[:900] or '(vide)'}"
        if quoted and n <= 6:
            block += f"\nMESSAGE AUQUEL IL RÉPOND (citation) : {quoted[:600]}"
        blocks.append(block)
    system = f"""Tu réponds à ‹‹NOM›› à partir d'extraits de ses e-mails, numérotés [1], [2]… {ai._UNTRUSTED}
Chaque extrait distingue le TEXTE ÉCRIT par l'expéditeur (même très court : une valeur, un code, un oui) et, s'il existe, le MESSAGE AUQUEL IL RÉPOND.
Un texte très court est la réponse à la demande citée : interprète-le (par exemple deux codes envoyés en réponse à une demande de codes).
Appuie-toi UNIQUEMENT sur ces extraits et cite tes sources par leur numéro entre crochets, par exemple [2].
Si les extraits ne permettent pas de répondre, dis-le franchement et indique ce qui manque. Ne devine rien.
Réponds TOUJOURS en français, en une à quatre phrases précises (noms, dates, montants, valeurs s'ils figurent dans les extraits)."""
    user = f"Question : {question.strip()}\n\nExtraits :\n\n" + "\n\n".join(blocks) + "\n\nRéponds en français, en citant les numéros."
    text = ai._ask(system, user, model=config.model("ASK"), max_tokens=900, feature="answer")
    cited = {int(n) for n in re.findall(r"\[(\d{1,2})\]", text)}
    sources = [{"n": n, "id": m["id"], "subject": m["subject"], "sender": m["sender_name"] or m["sender"], "date": m["date"], "cited": n in cited}
               for n, m in enumerate(hits, 1)]
    return {"text": text, "sources": sorted(sources, key=lambda s: (not s["cited"], s["n"]))}
