from threadbare.web.preferences import resolve_posts_per_page, resolve_show_avatars


def test_resolve_show_avatars_defaults_true_when_nothing_set():
    assert resolve_show_avatars(query_param=None, cookie_value=None) is True


def test_resolve_show_avatars_uses_valid_cookie_value():
    assert resolve_show_avatars(query_param=None, cookie_value="off") is False


def test_resolve_show_avatars_falls_back_to_default_for_invalid_cookie_value():
    assert resolve_show_avatars(query_param=None, cookie_value="bogus") is True


def test_resolve_show_avatars_query_param_overrides_cookie():
    assert resolve_show_avatars(query_param="off", cookie_value="on") is False


def test_resolve_show_avatars_ignores_invalid_query_param_and_uses_cookie():
    assert resolve_show_avatars(query_param="bogus", cookie_value="off") is False


def test_resolve_show_avatars_ignores_invalid_query_param_with_no_cookie():
    assert resolve_show_avatars(query_param="bogus", cookie_value=None) is True


def test_resolve_posts_per_page_defaults_to_25_when_nothing_set():
    assert resolve_posts_per_page(query_param=None, cookie_value=None) == 25


def test_resolve_posts_per_page_uses_valid_cookie_value():
    assert resolve_posts_per_page(query_param=None, cookie_value="50") == 50


def test_resolve_posts_per_page_falls_back_to_default_for_invalid_cookie_value():
    assert resolve_posts_per_page(query_param=None, cookie_value="bogus") == 25


def test_resolve_posts_per_page_falls_back_to_default_for_out_of_range_cookie_value():
    assert resolve_posts_per_page(query_param=None, cookie_value="999") == 25


def test_resolve_posts_per_page_query_param_overrides_cookie():
    assert resolve_posts_per_page(query_param="100", cookie_value="50") == 100


def test_resolve_posts_per_page_ignores_invalid_query_param_and_uses_cookie():
    assert resolve_posts_per_page(query_param="bogus", cookie_value="50") == 50


def test_resolve_posts_per_page_ignores_invalid_query_param_with_no_cookie():
    assert resolve_posts_per_page(query_param="bogus", cookie_value=None) == 25
