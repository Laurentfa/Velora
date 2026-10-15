"""Règles autour de l'IA : qui a le droit d'y passer (boîtes, expéditeurs exclus), combien elle coûte (suivi, budget),
et ce qu'elle apprend de vos corrections. Rien ici n'appelle l'API."""
import json
import sys
from datetime import datetime, timedelta

from . import config, db, i18n


class AIError(Exception):
    """Erreur d'IA. `fallback=True` : l'autre fournisseur peut prendre le relais (panne, clé refusée, crédits épuisés, budget atteint…)."""

    def __init__(self, message: str = "", fallback: bool = False) -> None:
        super().__init__(message)
        self.fallback = fallback


# $ par million de jetons (entrée, sortie) : sert aux estimations affichées
PRICES = {"claude-haiku-4-5": (1.0, 5.0), "claude-sonnet-5-5": (2.0, 10.0), "claude-opus-5-5": (4.0, 20.0)}

FEATURES = {
    "analyze": "Tri et résumé des mails", "draft_reply": "Réponses rédigées par l'IA", "draft_new": "Nouveaux mails rédigés par l'IA",
    "suggest_replies": "Réponses rapides", "translate": "Traduction", "summarize_thread": "Résumé de conversation",
    "summarize_attachment": "Résumé de pièce jointe", "summarize_scan": "Lecture de documents numérisés",
    "suggest_event": "Suggestions d'agenda", "suggest_task": "Suggestions de tâches", "brief_structured": "Brief du jour",
    "rewrite": "Correction et réécriture", "answer": "Demander à ma boîte", "interpret": "Règles de tri en français", "phishing": "Vérification d'hameçonnage",
}


# fonctions dont le résultat s'affiche dans l'interface : il doit suivre la langue choisie (les brouillons suivent la langue du correspondant)
_UI_LANGUAGE_FEATURES = {"analyze", "brief_structured", "summarize_thread", "summarize_attachment", "summarize_scan", "suggest_replies",
                         "suggest_event", "suggest_task", "translate", "answer", "interpret", "phishing"}


def language_note(feature: str) -> str:
    """Consigne ajoutée au prompt quand l'interface n'est pas en français."""
    lang = i18n.language()
    if lang == "fr" or feature not in _UI_LANGUAGE_FEATURES:
        return ""
    name = {"en": "English"}.get(lang, lang)
    return (f"\n\nIMPORTANT: the user's interface language is {name}. Write every free-text value of your answer (summaries, titles, labels, "
            f"explanations) in {name}, even though the instructions above are in French" + (
                ". For a translation request, translate into " + name + " instead of French." if feature == "translate" else
                ". Keep JSON keys and fixed values (such as category names) unchanged."))


def feature_label(name: str) -> str:
    return FEATURES.get(name, name or "Autre")


def feature_of_caller() -> str:
    """Nom de la fonction qui a demandé l'appel à l'IA (les deux premiers niveaux au-dessus de _ask)."""
    f = sys._getframe(2)
    return f.f_code.co_name


# =====================================================================================
#  Exclusions : boîtes et expéditeurs qui ne passent jamais par l'IA
# =====================================================================================
def excluded_patterns() -> list[str]:
    try:
        return [p.strip().lower() for p in json.loads(db.kv_get("ai_exclude") or "[]") if p.strip()]
    except ValueError:
        return []


def set_excluded_patterns(items: list[str]) -> None:
    clean = []
    for p in items:
        p = p.strip().lower().lstrip("@")
        if p and p not in clean:
            clean.append(p)
    db.kv_set("ai_exclude", json.dumps(clean))


def matches_sender(pattern: str, sender: str) -> bool:
    """« jean@x.fr » : cette adresse ; « x.fr » ou « @x.fr » : tout le domaine (sous-domaines compris)."""
    s = (sender or "").strip().lower()
    p = pattern.strip().lower().lstrip("@")
    if "@" in p:
        return s == p
    dom = s.rsplit("@", 1)[-1]
    return dom == p or dom.endswith("." + p)


def account_ai_off(account: str) -> bool:
    cfg = config.load_accounts(enabled_only=False).get(account)
    return bool(cfg) and not cfg.ai_enabled


def is_excluded(m: dict) -> bool:
    if account_ai_off(m.get("account", "")):
        return True
    return any(matches_sender(p, m.get("sender", "")) for p in excluded_patterns())


def guard(m: dict) -> None:
    if is_excluded(m):
        raise AIError("L'IA est désactivée pour cette boîte ou cet expéditeur (Paramètres › Confidentialité).")


def sql_clause() -> tuple[str, list]:
    """Condition SQL « ce mail a le droit d'aller à l'IA » (pour la file d'analyse automatique)."""
    off = [k for k, c in config.load_accounts(enabled_only=False).items() if not c.ai_enabled]
    parts, args = [], []
    if off:
        parts.append(f"account NOT IN ({','.join('?' * len(off))})"); args += off
    for p in excluded_patterns():
        if "@" in p:
            parts.append("lower(sender)<>?"); args.append(p)
        else:
            parts.append("NOT (lower(sender) LIKE ? OR lower(sender) LIKE ?)"); args += [f"%@{p}", f"%.{p}"]
    return (" AND ".join(parts) or "1"), args


# =====================================================================================
#  Coût : suivi par fonction, budget mensuel
# =====================================================================================
def usd_eur() -> float:
    try:
        return max(0.1, float(db.kv_get("usd_eur") or 0.92))
    except ValueError:
        return 0.92


def cost_usd(model: str, tokens_in: int, tokens_out: int) -> float:
    if model.startswith("1min:"):
        return 0.0  # crédits prépayés chez 1min.AI : pas de coût en euros à suivre ici (seulement les jetons)
    pin, pout = PRICES.get(model, (4.0, 20.0))
    return (tokens_in * pin + tokens_out * pout) / 1_000_000


def budget_eur() -> float:
    try:
        return max(0.0, float(db.kv_get("ai_budget_eur") or 0))
    except ValueError:
        return 0.0


def month_spent_eur(month: str | None = None) -> float:
    month = month or datetime.now().strftime("%Y-%m")
    with db.conn() as c:
        rows = c.execute("SELECT model, tokens_in, tokens_out FROM usage WHERE month=?", (month,)).fetchall()
    return sum(cost_usd(r["model"], r["tokens_in"], r["tokens_out"]) for r in rows) * usd_eur()


def check_budget() -> None:
    """Avant chaque appel : arrête l'IA si le plafond choisi est atteint ; prévient une fois à 80 %."""
    budget = budget_eur()
    if not budget:
        return
    spent = month_spent_eur()
    month = datetime.now().strftime("%Y-%m")
    if spent >= budget and db.kv_get("ai_budget_hard") == "1":
        raise AIError(f"Budget IA du mois atteint ({spent:.2f} € sur {budget:.2f} €). Augmentez-le dans Paramètres › Utilisation et données.", fallback=True)
    if spent >= budget * 0.8:
        from . import notify
        notify.once(f"budget:{month}:{'100' if spent >= budget else '80'}", "Budget IA",
                    f"{spent:.2f} € dépensés sur {budget:.2f} € ce mois-ci" + (" : le plafond est atteint." if spent >= budget else "."))


def usage_summary() -> dict:
    """{'month': €, 'budget': €, 'by_feature': [(libellé, appels, €)], 'by_day': [(jour, €)] sur 30 jours, 'by_month': [(mois, €)] sur 6 mois}"""
    rate = usd_eur()
    month = datetime.now().strftime("%Y-%m")
    with db.conn() as c:
        feats = c.execute("SELECT feature, model, SUM(calls) calls, SUM(tokens_in) ti, SUM(tokens_out) to_ FROM usage_log "
                          "WHERE substr(day,1,7)=? GROUP BY feature, model", (month,)).fetchall()
        days = c.execute("SELECT day, model, SUM(tokens_in) ti, SUM(tokens_out) to_ FROM usage_log WHERE day>=? GROUP BY day, model",
                         ((datetime.now() - timedelta(days=29)).strftime("%Y-%m-%d"),)).fetchall()
        months = c.execute("SELECT month, model, tokens_in, tokens_out FROM usage ORDER BY month DESC").fetchall()
    by_feat: dict[str, list] = {}
    for r in feats:
        e = by_feat.setdefault(feature_label(r["feature"]), [0, 0.0])
        e[0] += r["calls"] or 0
        e[1] += cost_usd(r["model"], r["ti"] or 0, r["to_"] or 0) * rate
    by_day: dict[str, float] = {}
    for r in days:
        by_day[r["day"]] = by_day.get(r["day"], 0.0) + cost_usd(r["model"], r["ti"] or 0, r["to_"] or 0) * rate
    by_month: dict[str, float] = {}
    for r in months:
        by_month[r["month"]] = by_month.get(r["month"], 0.0) + cost_usd(r["model"], r["tokens_in"], r["tokens_out"]) * rate
    series = [((datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")) for i in range(29, -1, -1)]
    return {
        "month": month_spent_eur(), "budget": budget_eur(), "hard": db.kv_get("ai_budget_hard") == "1", "rate": rate,
        "by_feature": sorted(((k, v[0], v[1]) for k, v in by_feat.items()), key=lambda x: -x[2]),
        "by_day": [(d, by_day.get(d, 0.0)) for d in series],
        "by_month": sorted(by_month.items(), reverse=True)[:6],
    }


# =====================================================================================
#  Expéditeurs prioritaires
# =====================================================================================
def vip_list() -> list[str]:
    try:
        return [p.strip().lower() for p in json.loads(db.kv_get("vip_senders") or "[]") if p.strip()]
    except ValueError:
        return []


def set_vip_list(items: list[str]) -> None:
    clean = []
    for p in items:
        p = p.strip().lower().lstrip("@")
        if p and p not in clean:
            clean.append(p)
    db.kv_set("vip_senders", json.dumps(clean))


def is_vip(sender: str) -> bool:
    return any(matches_sender(p, sender) for p in vip_list())


# =====================================================================================
#  Apprentissage à partir de vos corrections de classement
# =====================================================================================
GENERIC_DOMAINS = {"gmail.com", "outlook.com", "outlook.fr", "hotmail.com", "hotmail.fr", "live.com", "live.fr", "yahoo.com", "yahoo.fr",
                   "orange.fr", "wanadoo.fr", "free.fr", "sfr.fr", "laposte.net", "icloud.com", "me.com", "proton.me", "protonmail.com",
                   "gmx.com", "gmx.fr", "msn.com", "bbox.fr", "neuf.fr"}
CATS = ("action", "info", "newsletter", "notification", "personnel")


def learning_enabled() -> bool:
    return db.kv_get("learn_corrections") != "0"


def _domain(sender: str) -> str:
    return (sender or "").lower().rsplit("@", 1)[-1]


def record_correction(m: dict, old: str, new: str) -> str | None:
    """Garde la correction ; si deux expéditeurs différents d'un même domaine sont corrigés de la même façon, retient le domaine entier.
    Retourne le domaine appris, s'il y en a un."""
    with db.conn() as c:
        c.execute("INSERT INTO corrections(ts,sender,domain,subject,old_cat,new_cat) VALUES(?,?,?,?,?,?)",
                  (datetime.now().isoformat(), (m.get("sender") or "").lower(), _domain(m.get("sender", "")), (m.get("subject") or "")[:120], old or "", new))
    if not learning_enabled():
        return None
    dom = _domain(m.get("sender", ""))
    if not dom or dom in GENERIC_DOMAINS or new not in CATS:
        return None
    with db.conn() as c:
        rows = c.execute("SELECT sender, new_cat FROM corrections WHERE domain=? ORDER BY id DESC LIMIT 20", (dom,)).fetchall()
    last_by_sender: dict[str, str] = {}
    for r in rows:
        last_by_sender.setdefault(r["sender"], r["new_cat"])  # la correction la plus récente de chaque expéditeur
    cats = set(last_by_sender.values())
    if len(last_by_sender) >= 2 and cats == {new} and not db.kv_get(f"domain_cat:{dom}"):
        db.kv_set(f"domain_cat:{dom}", new)
        n = apply_domain(dom, new)
        db.kv_set(f"domain_cat_n:{dom}", str(n))
        return dom
    return None


def apply_domain(dom: str, cat: str) -> int:
    noise = cat in ("newsletter", "notification")
    with db.conn() as c:
        return c.execute(
            f"UPDATE messages SET category=?{', needs_reply=0, priority=MIN(priority,1)' if noise else ''} "
            "WHERE (lower(sender) LIKE ? OR lower(sender) LIKE ?) AND category<>?", (cat, f"%@{dom}", f"%.{dom}", cat)).rowcount


def forced_category(sender: str) -> str | None:
    """Catégorie imposée par vos corrections : l'adresse d'abord, puis son domaine."""
    s = (sender or "").lower()
    v = db.kv_get("sender_cat:" + s)
    if v in CATS:
        return v
    dom = _domain(s)
    while dom and "." in dom:
        v = db.kv_get(f"domain_cat:{dom}")
        if v in CATS:
            return v
        dom = dom.split(".", 1)[1]
    return None


def learned() -> list[tuple[str, str, str]]:
    """[(type, cible, catégorie)] : ce que l'appli a retenu de vos corrections (adresses et domaines)."""
    with db.conn() as c:
        rows = c.execute("SELECT k, v FROM kv WHERE k LIKE 'sender_cat:%' OR k LIKE 'domain_cat:%' ORDER BY k").fetchall()
    return [("domaine" if r["k"].startswith("domain_cat:") else "adresse", r["k"].split(":", 1)[1], r["v"]) for r in rows]


def forget(kind: str, target: str) -> None:
    with db.conn() as c:
        c.execute("DELETE FROM kv WHERE k=?", (("domain_cat:" if kind == "domaine" else "sender_cat:") + target,))


def few_shot_block(limit: int = 8) -> str:
    """Quelques-unes de vos dernières corrections, ajoutées à la consigne de tri pour que l'IA s'en inspire."""
    if not learning_enabled():
        return ""
    with db.conn() as c:
        rows = c.execute("SELECT sender, subject, old_cat, new_cat FROM corrections WHERE old_cat<>new_cat ORDER BY id DESC LIMIT 40").fetchall()
    seen, lines = set(), []
    for r in rows:
        if r["sender"] in seen:
            continue
        seen.add(r["sender"])
        lines.append(f"- {r['sender']} « {r['subject'][:50]} » : classé « {r['new_cat']} » (et non « {r['old_cat']} »)")
        if len(lines) >= limit:
            break
    return ("Corrections récentes de ‹‹PRENOM›› (inspire-toi de ces exemples) :\n" + "\n".join(lines) + "\n") if lines else ""
