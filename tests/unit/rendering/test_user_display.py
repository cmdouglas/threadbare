from threadbare.rendering.user_display import role_color_hex


def test_role_color_hex_returns_none_for_none():
    assert role_color_hex(None) is None


def test_role_color_hex_returns_none_for_zero():
    # Discord's own sentinel for "no custom color" on a role -- rendering it
    # as literal black would be wrong, the caller should fall back to the
    # theme's default text color instead.
    assert role_color_hex(0) is None


def test_role_color_hex_formats_as_hash_rrggbb():
    assert role_color_hex(0xFF0000) == "#ff0000"


def test_role_color_hex_zero_pads_small_values():
    assert role_color_hex(0x0000FF) == "#0000ff"
