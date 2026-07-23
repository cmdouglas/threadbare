from threadbare.rendering.emoji import (
    render_custom_emoji_html,
    render_emoji_token_html,
    render_unicode_text_with_emoji_titles,
    unicode_emoji_title,
)


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


def test_render_emoji_token_html_unicode_gets_a_shortcode_tooltip():
    # Discord's own client shows the shortcode (e.g. ":thumbsup:") on hover
    # for a standard unicode emoji too, not just custom server emoji.
    assert render_emoji_token_html("👍") == '<span title=":thumbsup:">👍</span>'


def test_render_emoji_token_html_escapes_malformed_token():
    # Not Discord's <:name:id> shape at all, and not a recognized unicode
    # emoji either — render as escaped literal text rather than raising,
    # since this string ultimately comes from Discord's own reaction payload
    # and should never be trusted as pre-sanitized HTML.
    assert render_emoji_token_html("<script>") == "&lt;script&gt;"


def test_unicode_emoji_title_prefers_the_short_discord_style_alias():
    assert unicode_emoji_title("😏") == ":smirk:"


def test_unicode_emoji_title_falls_back_to_the_canonical_name_with_no_alias():
    assert unicode_emoji_title("🔥") == ":fire:"


def test_unicode_emoji_title_returns_none_for_non_emoji_text():
    assert unicode_emoji_title("hi") is None


def test_render_unicode_text_with_emoji_titles_wraps_only_the_emoji():
    html = render_unicode_text_with_emoji_titles("hi 😏 there")

    assert html == 'hi <span title=":smirk:">😏</span> there'


def test_render_unicode_text_with_emoji_titles_escapes_the_rest():
    html = render_unicode_text_with_emoji_titles("<script> 😏")

    assert html == '&lt;script&gt; <span title=":smirk:">😏</span>'


def test_render_unicode_text_with_emoji_titles_handles_plain_text():
    assert render_unicode_text_with_emoji_titles("just text") == "just text"
