from threadbare.pagination import DEFAULT_PAGE_SIZE, page_number_for_offset, page_window


def test_page_number_for_offset_zero_preceding_is_page_one():
    assert page_number_for_offset(0, page_size=25) == 1


def test_page_number_for_offset_last_item_on_first_page():
    assert page_number_for_offset(24, page_size=25) == 1


def test_page_number_for_offset_first_item_on_second_page():
    assert page_number_for_offset(25, page_size=25) == 2


def test_page_number_for_offset_last_item_on_second_page():
    assert page_number_for_offset(49, page_size=25) == 2


def test_page_number_for_offset_first_item_on_third_page():
    assert page_number_for_offset(50, page_size=25) == 3


def test_page_number_for_offset_uses_default_page_size():
    assert page_number_for_offset(0) == 1
    assert page_number_for_offset(DEFAULT_PAGE_SIZE) == 2


def test_page_window_with_zero_pages_is_empty():
    assert page_window(1, 0) == []


def test_page_window_single_page():
    assert page_window(1, 1) == [1]


def test_page_window_collapses_when_head_middle_tail_all_overlap():
    assert page_window(1, 6) == [1, 2, 3, 4, 5, 6]


def test_page_window_one_gap_when_current_is_at_the_start():
    assert page_window(1, 21) == [1, 2, 3, None, 19, 20, 21]


def test_page_window_two_gaps_when_current_is_in_the_middle():
    assert page_window(9, 23) == [1, 2, 3, None, 7, 8, 9, 10, 11, None, 21, 22, 23]


def test_page_window_one_gap_when_current_is_near_the_start():
    assert page_window(2, 23) == [1, 2, 3, 4, None, 21, 22, 23]


def test_page_window_one_gap_when_current_is_near_the_end():
    assert page_window(22, 23) == [1, 2, 3, None, 20, 21, 22, 23]
