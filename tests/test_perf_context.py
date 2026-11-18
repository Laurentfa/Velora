"""Le contexte de la page, noté dans le journal quand elle se fige : lisible, sans texte de la page."""
import sys
sys.path.insert(0, ".")
from mailia import perf

ctx = {"v": "visible", "f": True, "idle": 12, "dlg": 1, "menu": 0, "ed": 0, "nodes": 4120, "rows": 380, "spin": 0, "lock": True, "focus": "BODY", "c": "DIV.q-dialog__backdrop", "tl": "DIV.q-dialog__backdrop", "br": "DIV.q-page", "size": "1470x900"}
t = perf.context_text(ctx, 7.4)
assert "visibilité visible" in t and "fenêtre active" in t and "1 fenêtre(s) de dialogue" in t and "4120 éléments dont 380 lignes" in t
assert "défilement verrouillé" in t and "au centre DIV.q-dialog__backdrop" in t and "relevé il y a 7 s" in t and "menu" not in t
assert perf.context_text(None) == "contexte inconnu"
assert "fenêtre inactive" in perf.context_text({**ctx, "f": False})
assert "dlg" in perf.CONTEXT_JS and "elementFromPoint" in perf.CONTEXT_JS
print("test_perf_context : ok")
