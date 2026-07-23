from threadbare.rendering.emoji import render_custom_emoji_html, render_emoji_token_html


def test_render_custom_emoji_html_static():
    html = render_custom_emoji_html(emoji_id=123, name="pog", animated=False)

    assert html == (
        '<img class="emoji" src="https://cdn.discordapp.com/emojis/123.png" '
        'alt=":pog:" title=":pog:">'
    )


def test_render_custom_emoji_html_animated():
    html = render_custom_emoji_html(emoji_id=123, name="pog", animated=True)

    assert html == (
        '<img class="emoji" src="https://cdn.discordapp.com/emojis/123.gif" '
        'alt=":pog:" title=":pog:">'
    )


def test_render_custom_emoji_html_escapes_name():
    html = render_custom_emoji_html(emoji_id=123, name='"><script>', animated=False)

    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_render_emoji_token_html_static_custom_emoji():
    html = render_emoji_token_html("<:pog:123>")

    assert html == (
        '<img class="emoji" src="https://cdn.discordapp.com/emojis/123.png" '
        'alt=":pog:" title=":pog:">'
    )


def test_render_emoji_token_html_animated_custom_emoji():
    html = render_emoji_token_html("<a:pog:123>")

    assert html == (
        '<img class="emoji" src="https://cdn.discordapp.com/emojis/123.gif" '
        'alt=":pog:" title=":pog:">'
    )


def test_render_emoji_token_html_unicode_passthrough():
    assert render_emoji_token_html("👍") == "👍"


def test_render_emoji_token_html_escapes_malformed_token():
    # Not Discord's <:name:id> shape at all — render as escaped literal text
    # rather than raising, since this string ultimately comes from Discord's
    # own reaction payload and should never be trusted as pre-sanitized HTML.
    assert render_emoji_token_html("<script>") == "&lt;script&gt;"
