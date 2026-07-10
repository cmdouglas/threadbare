from threadbare.channel_types import CATEGORY, FORUM, NEWS, TEXT
from threadbare.web.board_tree import board_view_mode, group_channels_by_category


def _channel(id, type, *, name="chan", position=0, parent_id=None):
    return {"id": id, "type": type, "name": name, "position": position, "parent_id": parent_id}


def test_board_view_mode_forum_is_topics_only():
    assert board_view_mode(_channel(1, FORUM)) == "topics_only"


def test_board_view_mode_text_is_freeform():
    assert board_view_mode(_channel(1, TEXT)) == "freeform"


def test_board_view_mode_news_is_freeform():
    assert board_view_mode(_channel(1, NEWS)) == "freeform"


def test_group_channels_by_category_orders_categories_by_position():
    rows = [
        _channel(1, CATEGORY, name="second", position=1),
        _channel(2, CATEGORY, name="first", position=0),
        _channel(10, TEXT, parent_id=1, position=0),
        _channel(11, TEXT, parent_id=2, position=0),
    ]

    groups = group_channels_by_category(rows)

    assert [g["category"]["name"] for g in groups] == ["first", "second"]


def test_group_channels_by_category_orders_boards_within_a_category_by_position():
    rows = [
        _channel(1, CATEGORY, position=0),
        _channel(10, TEXT, name="b", parent_id=1, position=1),
        _channel(11, TEXT, name="a", parent_id=1, position=0),
    ]

    groups = group_channels_by_category(rows)

    assert [b["name"] for b in groups[0]["boards"]] == ["a", "b"]


def test_group_channels_by_category_uncategorized_boards_appear_first():
    rows = [
        _channel(1, CATEGORY, position=0),
        _channel(10, TEXT, parent_id=1, position=0),
        _channel(11, TEXT, parent_id=None, position=0),
    ]

    groups = group_channels_by_category(rows)

    assert groups[0]["category"] is None
    assert [b["id"] for b in groups[0]["boards"]] == [11]


def test_group_channels_by_category_omits_uncategorized_group_when_empty():
    rows = [_channel(1, CATEGORY, position=0), _channel(10, TEXT, parent_id=1, position=0)]

    groups = group_channels_by_category(rows)

    assert all(g["category"] is not None for g in groups)


def test_group_channels_by_category_includes_empty_categories():
    rows = [_channel(1, CATEGORY, position=0)]

    groups = group_channels_by_category(rows)

    assert groups == [{"category": rows[0], "boards": []}]


def test_group_channels_by_category_folds_board_with_unlisted_parent_into_uncategorized():
    # The board's category (e.g. a private category filtered out upstream)
    # isn't in the input rows at all -- must not silently drop the board.
    rows = [_channel(10, TEXT, parent_id=999, position=0)]

    groups = group_channels_by_category(rows)

    assert groups == [{"category": None, "boards": [rows[0]]}]
