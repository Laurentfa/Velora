"""Langue de l'interface. Le français est la langue d'origine ; les autres langues sont des dictionnaires (mailia/locales/<code>.json)
appliqués dans la fenêtre : un petit script traduit les textes affichés (libellés, infobulles, notifications, champs) au fur et à mesure,
sans toucher au contenu de vos mails. Les textes qui contiennent des valeurs (« {0} mails ») sont reconnus par leur modèle."""
import json
import locale
import os
import re
import sys
from functools import lru_cache
from pathlib import Path

from . import db

LOCALES = Path(__file__).parent / "locales"
AVAILABLE = {"fr": "Français", "en": "English"}
QUASAR = {"fr": "fr", "en": "en-US"}


def system_language() -> str:
    """Langue du système (« fr », « en »…), sans bibliothèque supplémentaire."""
    try:
        if sys.platform == "darwin":
            from Foundation import NSLocale
            langs = list(NSLocale.preferredLanguages())
            if langs:
                return str(langs[0])[:2].lower()
        elif sys.platform.startswith("win"):
            import ctypes
            lid = ctypes.windll.kernel32.GetUserDefaultUILanguage()
            return (locale.windows_locale.get(lid, "en_US") or "en_US")[:2].lower()
    except Exception:
        pass
    for var in ("LC_ALL", "LC_MESSAGES", "LANG"):
        v = os.environ.get(var, "")
        if v:
            return v[:2].lower()
    return "en"


def setting() -> str:
    """Choix enregistré : « fr », « en » ou « auto » (suivre le système). Sans choix enregistré : le français, quelle que soit la langue du système."""
    try:
        v = db.kv_get("ui_lang")
    except Exception:  # base pas encore créée (tout premier lancement)
        v = None
    return v if v in AVAILABLE or v == "auto" else "fr"


def language() -> str:
    s = setting()
    if s != "auto":
        return s
    sys_lang = system_language()
    return sys_lang if sys_lang in AVAILABLE else "en"


def set_language(code: str) -> None:
    db.kv_set("ui_lang", code if code in AVAILABLE else "auto")


@lru_cache(maxsize=4)
def _load(lang: str) -> dict:
    p = LOCALES / f"{lang}.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _js_escape(s: str) -> str:
    return re.sub(r"([.*+?^${}()|\[\]\\/])", r"\\\1", s)


def _template_regex(fr: str) -> str | None:
    """« Résumé impossible : {0} » -> source d'expression régulière JavaScript, ou None s'il n'y a aucune valeur à capturer."""
    parts = re.split(r"(\{\d+\})", fr)
    if len(parts) == 1:
        return None
    out = []
    for p in parts:
        if re.fullmatch(r"\{\d+\}", p):
            out.append("([\\s\\S]+?)")
        else:
            out.append(_js_escape(p.replace("{{", "{").replace("}}", "}")))
    return "".join(out)


def tr(text: str, **fmt) -> str:
    """Traduction côté serveur (listes de jours, messages construits en Python). Français : le texte tel quel."""
    lang = language()
    if lang == "fr" or not text:
        return text.format(**fmt) if fmt else text
    d = _load(lang)
    out = d.get(text.strip())
    return (out if out is not None else text).format(**fmt) if fmt else (out if out is not None else text)


def client_script() -> str:
    """Balise <script> à placer dans l'en-tête de la page : dictionnaire + traducteur du DOM. Vide en français."""
    lang = language()
    if lang == "fr":
        return ""
    d = _load(lang)
    exact, tpl, prefix = {}, [], []
    for fr, en in d.items():
        rx = _template_regex(fr)
        if rx:
            tpl.append([rx, en.replace("{{", "{").replace("}}", "}")])
        else:
            key = fr.replace("{{", "{").replace("}}", "}")
            exact[key] = en.replace("{{", "{").replace("}}", "}")
            if key.endswith(":") and len(key) <= 40:
                prefix.append([key, exact[key]])
    tpl.sort(key=lambda t: -len(t[0]))
    prefix.sort(key=lambda t: -len(t[0]))
    data = json.dumps({"exact": exact, "tpl": tpl, "prefix": prefix}, ensure_ascii=False).replace("</", "<\\/")
    return f"<script>window.MAILIA_I18N = {data};</script><script>{TRANSLATOR_JS}</script>"


TRANSLATOR_JS = r"""
(() => {
  const D = window.MAILIA_I18N; if (!D) return;
  const exact = D.exact, prefixes = D.prefix;
  const tpls = D.tpl.map(([src, en]) => { try { return [new RegExp('^' + src + '$'), en]; } catch (e) { return null; } }).filter(Boolean);
  const memo = new Map();
  const SKIP = 'script, style, textarea, input, select, iframe, pre, code, [contenteditable], .q-editor__content, .mailbody, .from, .subj, .sum, .no-i18n';
  const plain = /^[\d\s.,:;\/\-–—+%€$()|·•★✓✗✦…]+$/;
  function tr(s) {
    if (memo.has(s)) return memo.get(s);
    let out = s;
    const t = s.trim();
    if (t && !plain.test(t)) {
      if (Object.prototype.hasOwnProperty.call(exact, t)) out = s.replace(t, () => exact[t]);
      else {
        let done = false;
        for (const [re, en] of tpls) {
          const m = re.exec(t);
          if (m) { out = s.replace(t, () => en.replace(/\{(\d+)\}/g, (_, i) => (m[+i + 1] !== undefined ? m[+i + 1] : ''))); done = true; break; }
        }
        if (!done) for (const [pre, en] of prefixes) {
          if (t.startsWith(pre) && t.length > pre.length) { out = s.replace(pre, () => en); break; }
        }
      }
    }
    if (memo.size > 5000) memo.clear();
    memo.set(s, out);
    return out;
  }
  const SKIP_ATTR = 'script, style, iframe, [contenteditable], .q-editor__content, .mailbody, .from, .subj, .sum, .no-i18n';
  const skipped = el => el && el.closest && el.closest(SKIP);
  const skippedAttr = el => el && el.closest && el.closest(SKIP_ATTR);
  function node(n) {
    if (n.nodeType === 3) {
      if (skipped(n.parentElement)) return;
      const v = n.nodeValue, o = tr(v);
      if (o !== v) n.nodeValue = o;
    } else if (n.nodeType === 1) {
      if (n.matches && n.matches('script, style, iframe, textarea')) return;
      attrs(n);
      for (const c of n.childNodes) node(c);
    }
  }
  function attrs(el) {
    if (!el.getAttribute || skippedAttr(el)) return;
    for (const a of ['placeholder', 'title', 'aria-label']) {
      const v = el.getAttribute(a);
      if (v) { const o = tr(v); if (o !== v) el.setAttribute(a, o); }
    }
  }
  let queue = [], scheduled = false;
  function flush() { scheduled = false; const q = queue; queue = []; for (const n of q) { try { node(n); } catch (e) {} } }
  function push(n) { queue.push(n); if (!scheduled) { scheduled = true; Promise.resolve().then(flush); } }
  new MutationObserver(ms => {
    for (const m of ms) {
      if (m.type === 'childList') m.addedNodes.forEach(push);
      else if (m.type === 'characterData') push(m.target);
      else if (m.type === 'attributes') { try { attrs(m.target); } catch (e) {} }
    }
  }).observe(document.documentElement, {childList: true, subtree: true, characterData: true, attributes: true, attributeFilter: ['placeholder', 'title', 'aria-label']});
  document.addEventListener('DOMContentLoaded', () => push(document.body));
})();
"""


# ---------- jours et mois (listes utilisées par l'affichage des dates) ----------
DAYS = {
    "fr": (["lun.", "mar.", "mer.", "jeu.", "ven.", "sam.", "dim."], ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"],
           ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre", "novembre", "décembre"],
           ["janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc."]),
    "en": (["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"], ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"],
           ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"],
           ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]),
}


def at() -> str:
    """« à » devant une heure (« lundi 5 oct. à 08:00 »)."""
    return {"fr": "à", "en": "at"}.get(language(), "à")


def calendar_names() -> tuple[list, list, list, list]:
    """(jours courts, jours longs, mois, mois courts) dans la langue de l'interface."""
    return DAYS.get(language(), DAYS["fr"])
