from threadbare.rendering.avatars import avatar_url, guild_icon_url


def test_avatar_url_uses_png_for_a_static_hash():
    assert avatar_url(123, "abcdef") == "https://cdn.discordapp.com/avatars/123/abcdef.png"


def test_avatar_url_uses_gif_for_an_animated_hash():
    assert avatar_url(123, "a_abcdef") == "https://cdn.discordapp.com/avatars/123/a_abcdef.gif"


def test_avatar_url_falls_back_to_a_default_avatar_when_no_hash():
    # index = (user_id >> 22) % 6
    assert avatar_url(0, None) == "https://cdn.discordapp.com/embed/avatars/0.png"


def test_avatar_url_default_avatar_index_depends_on_user_id():
    user_id = 123456789012345678
    expected_index = (user_id >> 22) % 6

    assert (
        avatar_url(user_id, None)
        == f"https://cdn.discordapp.com/embed/avatars/{expected_index}.png"
    )


def test_guild_icon_url_uses_png_for_a_static_hash():
    assert guild_icon_url(456, "abcdef") == "https://cdn.discordapp.com/icons/456/abcdef.png"


def test_guild_icon_url_uses_gif_for_an_animated_hash():
    assert guild_icon_url(456, "a_abcdef") == "https://cdn.discordapp.com/icons/456/a_abcdef.gif"


def test_guild_icon_url_returns_none_when_guild_has_no_icon():
    # Unlike a user avatar, Discord has no default guild-icon asset to fall
    # back to -- a guild with no icon set just has no icon.
    assert guild_icon_url(456, None) is None
