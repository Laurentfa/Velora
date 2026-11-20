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


def test_page_title_baseline_matches_the_velora_word_and_both_rules_stay_aligned():
    """Le titre de la page est sur la même ligne de base que « Velora » (47,5 px du haut) : 14 px de marge d'origine + 12,5 px mesurés. Le haut de la colonne de gauche grandit d'autant,
    pour que le filet sous « Nouveau message » reste aligné avec celui du bandeau de la liste (marge de la colonne : 16 px)."""
    import re
    from mailia import ui_assets
    css = ui_assets.CSS
    lhead = re.search(r"\.lhead\{[^}]*\}", css).group(0)
    sidetop = re.search(r"\.sidetop\{[^}]*\}", css).group(0)
    top = float(re.search(r"padding:([\d.]+)px 14px 0", lhead).group(1))
    lh = float(re.search(r"height:([\d.]+)px", lhead).group(1))
    side = float(re.search(r"height:([\d.]+)px", sidetop).group(1))
    assert top == 26.5 and lh == 132.5
    assert 16 + side == lh  # colonne de gauche : 16 px de marge puis le haut ; les deux filets tombent à la même hauteur
    assert ".lhead .title{font-size:19px" in css  # la police et la taille du titre ne changent pas
