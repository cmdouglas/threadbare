from threadbare.rendering.avatars import avatar_url


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
