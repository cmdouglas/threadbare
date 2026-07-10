from threadbare.web.authz import has_mod_permissions, requires_login


def test_has_mod_permissions_true_for_manage_guild_bit():
    assert has_mod_permissions(1 << 5) is True


def test_has_mod_permissions_true_for_administrator_bit():
    assert has_mod_permissions(1 << 3) is True


def test_has_mod_permissions_false_when_neither_bit_set():
    assert has_mod_permissions(1 << 10) is False  # VIEW_CHANNEL, unrelated bit


def test_has_mod_permissions_false_for_zero():
    assert has_mod_permissions(0) is False


def test_has_mod_permissions_true_when_extra_unrelated_bits_also_set():
    assert has_mod_permissions((1 << 5) | (1 << 10) | (1 << 16)) is True


def test_requires_login_true_for_an_ordinary_endpoint():
    assert requires_login("board_index.board_index") is True


def test_requires_login_false_for_login_and_callback_and_static_endpoints():
    assert requires_login("auth.login") is False
    assert requires_login("auth.oauth_callback") is False
    assert requires_login("static") is False


def test_requires_login_true_when_endpoint_is_none():
    # No matched route (e.g. a 404) -- err on the side of gating.
    assert requires_login(None) is True
