"""Colonne de gauche : son ascenseur reste en place quand elle est redessinée (à chaque clic sur une entrée, comme Ventiléa Connect tout en bas de la colonne)."""
from mailia import ui_assets


def test_side_column_scroll_position_is_kept_across_redraws():
    js = ui_assets.SWIPE_JS
    assert ".sidescroll .q-scrollarea__container" in js  # l'élément qui défile dans la colonne de gauche
    assert "new MutationObserver" in js and "scrollTop = pos" in js  # la position retenue est rendue au nouvel élément dès qu'il apparaît
    assert "addEventListener('scroll'" in js  # et elle est mise à jour quand on défile


def test_the_observer_does_not_use_document_body():
    """Le script est chargé dans <head> (ui.add_head_html) : document.body y vaut null, et observe(null) lève une erreur qui arrêtait tout le reste du script (colonnes, glisser-déposer…)."""
    from mailia import ui_assets
    assert "observe(document.body" not in ui_assets.SWIPE_JS
    assert "observe(document.documentElement" in ui_assets.SWIPE_JS
