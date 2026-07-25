from threadbare.reply_tree import build_reply_tree


def _row(id, reply_to_id=None):
    return {"id": id, "reply_to_id": reply_to_id}


def test_messages_with_no_reply_to_id_all_become_roots():
    rows = [_row(1), _row(2), _row(3)]

    tree = build_reply_tree(rows)

    assert [node["row"]["id"] for node in tree] == [1, 2, 3]
    assert all(node["children"] == [] for node in tree)


def test_a_reply_becomes_a_child_of_its_target():
    rows = [_row(1), _row(2, reply_to_id=1)]

    tree = build_reply_tree(rows)

    assert len(tree) == 1
    root = tree[0]
    assert root["row"]["id"] == 1
    assert [child["row"]["id"] for child in root["children"]] == [2]


def test_sibling_replies_to_the_same_parent_keep_chronological_order():
    rows = [_row(1), _row(2, reply_to_id=1), _row(3, reply_to_id=1)]

    tree = build_reply_tree(rows)

    root = tree[0]
    assert [child["row"]["id"] for child in root["children"]] == [2, 3]


def test_a_reply_to_a_reply_nests_two_levels_deep():
    rows = [_row(1), _row(2, reply_to_id=1), _row(3, reply_to_id=2)]

    tree = build_reply_tree(rows)

    root = tree[0]
    child = root["children"][0]
    assert child["row"]["id"] == 2
    assert [grandchild["row"]["id"] for grandchild in child["children"]] == [3]


def test_a_reply_target_outside_the_row_set_becomes_a_root_instead_of_dropped():
    rows = [_row(2, reply_to_id=999)]

    tree = build_reply_tree(rows)

    assert [node["row"]["id"] for node in tree] == [2]


def test_empty_input_returns_empty_tree():
    assert build_reply_tree([]) == []
