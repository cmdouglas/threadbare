from threadbare.pagination import DEFAULT_PAGE_SIZE, page_number_for_offset


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
