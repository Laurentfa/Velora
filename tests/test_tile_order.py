"""Ordre des tuiles de modules choisi par glisser-déposer : mémoire, nouveaux modules, modules absents."""
import main as app_main
from mailia import sync, tile_order

V = ["mod_a", "mod_b", "mod_c", "mod_d"]


def test_default_order_is_the_loading_order():
    assert tile_order.arrange(V, []) == V


def test_chosen_order_first_then_new_modules_in_loading_order():
    assert tile_order.arrange(V, ["mod_c", "mod_a"]) == ["mod_c", "mod_a", "mod_b", "mod_d"]


def test_unknown_or_removed_modules_are_ignored_but_keep_their_place():
    assert tile_order.arrange(V, ["gone", "mod_d", "mod_a"]) == ["mod_d", "mod_a", "mod_b", "mod_c"]
    new = tile_order.move(V, ["gone", "mod_d", "mod_a"], "mod_b", "mod_d", False)
    assert new[:4] == ["mod_b", "mod_d", "mod_a", "mod_c"] and "gone" in new


def test_move_before_and_after_a_target():
    assert tile_order.move(V, [], "mod_d", "mod_a", False) == ["mod_d", "mod_a", "mod_b", "mod_c"]
    assert tile_order.move(V, [], "mod_a", "mod_c", True) == ["mod_b", "mod_c", "mod_a", "mod_d"]
    assert tile_order.move(V, [], "mod_a", "mod_b", True) == ["mod_b", "mod_a", "mod_c", "mod_d"]


def test_impossible_moves_return_none():
    assert tile_order.move(V, [], "mod_a", "mod_a", True) is None
    assert tile_order.move(V, [], "mod_a", "inconnu", True) is None
    assert tile_order.move(V, [], "inconnu", "mod_a", False) is None


def test_saved_in_the_settings_and_reset():
    tile_order.save(["mod_b", "mod_a"])
    assert tile_order.load() == ["mod_b", "mod_a"]
    tile_order.reset()
    assert tile_order.load() == []


def test_corrupt_setting_falls_back_to_the_default_order():
    from mailia import db
    db.kv_set("tile_order", "pas du json")
    assert tile_order.load() == [] and tile_order.arrange(V, tile_order.load()) == V


async def test_tiles_follow_the_chosen_order_and_a_drop_saves_it(user, monkeypatch) -> None:
    from nicegui import ui
    monkeypatch.setattr(sync, "run_cycle", lambda: None)
    views = {f"mod_{k}_v": {"title": f"Page {k}", "icon": "⚕"} for k in "abc"}
    monkeypatch.setattr(app_main.modules, "views", views)
    tile_order.save(["mod_c_v", "mod_a_v"])
    ui.page("/")(app_main.index_page)
    await user.open("/")
    tiles = [e for e in user.find(marker="modules-section").elements][0]
    order = [t._props.get("data-vid") for t in tiles.descendants() if t._props.get("data-vid")]
    assert order == ["mod_c_v", "mod_a_v", "mod_b_v"]  # l'ordre choisi, puis les modules sans position
    assert all(t._props.get("draggable") == "true" for t in tiles.descendants() if t._props.get("data-vid"))
    # un dépôt de « mod_b_v » avant « mod_c_v » est mémorisé
    box = [o for o in __import__("gc").get_objects() if type(o).__name__ == "Mailbox"][-1]
    box.tile_dropped({"detail": {"from": "mod_b_v", "after": False}}, "mod_c_v")
    assert tile_order.load()[:3] == ["mod_b_v", "mod_c_v", "mod_a_v"]


def test_hidden_module_tiles_are_filtered_and_remembered():
    views = {"a_v": {"module": "mod_a"}, "b_v": {"module": "mod_b"}, "c_v": {"module": "mod_a"}}
    assert tile_order.hidden() == set()
    tile_order.set_hidden("mod_a", True)
    assert tile_order.hidden() == {"mod_a"}
    assert tile_order.visible(["a_v", "b_v", "c_v"], views, tile_order.hidden()) == ["b_v"]  # toutes les vues du module masqué disparaissent
    tile_order.set_hidden("mod_a", True)  # sans doublon
    tile_order.set_hidden("mod_a", False)
    assert tile_order.hidden() == set() and tile_order.visible(["a_v", "b_v"], views, set()) == ["a_v", "b_v"]
    assert tile_order.visible(["inconnu"], views, {"mod_a"}) == ["inconnu"]  # une vue sans module connu reste affichée
