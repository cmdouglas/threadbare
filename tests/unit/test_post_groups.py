from threadbare.post_groups import group_messages


def _row(id: int, starts_group: bool = True, **extra) -> dict:
    return {"id": id, "starts_group": starts_group, **extra}


def test_no_rows_produces_no_groups():
    assert group_messages([]) == []


def test_every_message_starting_a_group_is_one_post_each():
    """The default state of the column, and what an install with merging
    switched off must keep seeing.
    """
    rows = [_row(1), _row(2), _row(3)]

    assert group_messages(rows) == [[rows[0]], [rows[1]], [rows[2]]]


def test_a_run_of_continuations_joins_its_head():
    rows = [_row(1), _row(2, starts_group=False), _row(3, starts_group=False), _row(4)]

    assert group_messages(rows) == [[rows[0], rows[1], rows[2]], [rows[3]]]


def test_a_page_starting_mid_run_keeps_its_leading_continuations_together():
    """get_messages_page fetches whole posts, so a page's first row is
    normally a head -- but nothing in this function may *depend* on that.
    Rows that arrive already orphaned from their head (a reaction-filtered
    view, a partial fetch, a page boundary computed wrong) must still render
    as one post rather than being dropped or silently split per message.
    """
    rows = [_row(2, starts_group=False), _row(3, starts_group=False), _row(4)]

    assert group_messages(rows) == [[rows[0], rows[1]], [rows[2]]]


def test_groups_preserve_row_identity_not_copies():
    """Callers render straight from these dicts (render_message_for_display
    takes the row itself), so grouping must hand back the same objects.
    """
    rows = [_row(1), _row(2, starts_group=False)]

    grouped = group_messages(rows)

    assert grouped[0][0] is rows[0]
    assert grouped[0][1] is rows[1]


def test_input_order_is_preserved_within_and_across_groups():
    rows = [_row(10), _row(11, starts_group=False), _row(20), _row(21, starts_group=False)]

    grouped = group_messages(rows)

    assert [[r["id"] for r in group] for group in grouped] == [[10, 11], [20, 21]]
