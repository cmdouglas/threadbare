from threadbare.rendering.reactions import render_reaction_badges_html


def test_render_reaction_badges_html_single_unicode_reaction():
    html = render_reaction_badges_html([("👍", 3)])

    assert html == (
        '<div class="reactions">'
        '<span class="reaction-badge"><span title=":thumbsup:">👍</span>'
        '<span class="reaction-count">3</span></span>'
        "</div>"
    )


def test_render_reaction_badges_html_multiple_reactions_in_order():
    html = render_reaction_badges_html([("👍", 3), ("🎉", 1)])

    assert html.index("👍") < html.index("🎉")
    assert '<span class="reaction-count">3</span>' in html
    assert '<span class="reaction-count">1</span>' in html


def test_render_reaction_badges_html_custom_emoji_reaction():
    html = render_reaction_badges_html([("<:pog:123>", 2)])

    assert '<img class="emoji" src="https://cdn.discordapp.com/emojis/123.png"' in html
    assert '<span class="reaction-count">2</span>' in html


def test_render_reaction_badges_html_empty_list_renders_nothing():
    assert render_reaction_badges_html([]) == ""
