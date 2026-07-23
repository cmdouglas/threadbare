from threadbare.rendering.markdown import (
    ReferencedIds,
    ResolvedRefs,
    collect_referenced_ids,
    render_message_content,
)

EMPTY_REFS = ResolvedRefs(users={}, channels={})


def test_render_message_content_escapes_plain_text():
    assert render_message_content("<script>alert(1)</script>", refs=EMPTY_REFS) == (
        "&lt;script&gt;alert(1)&lt;/script&gt;"
    )


def test_render_message_content_bold():
    assert render_message_content("**hi**", refs=EMPTY_REFS) == "<strong>hi</strong>"


def test_render_message_content_italic():
    assert render_message_content("*hi*", refs=EMPTY_REFS) == "<em>hi</em>"


def test_render_message_content_underline():
    assert render_message_content("__hi__", refs=EMPTY_REFS) == "<u>hi</u>"


def test_render_message_content_strikethrough():
    assert render_message_content("~~hi~~", refs=EMPTY_REFS) == "<s>hi</s>"


def test_render_message_content_inline_code():
    assert render_message_content("`hi`", refs=EMPTY_REFS) == "<code>hi</code>"


def test_render_message_content_code_block():
    # The library's own lexer/parser deliberately drops the leading newline
    # right after the opening ``` but keeps a trailing one (its own doc
    # comment: "```\ntest\n``` is <code-block>test<br /></code-block>").
    assert render_message_content("```\nhi\n```", refs=EMPTY_REFS) == (
        "<pre><code>hi\n</code></pre>"
    )


def test_render_message_content_code_block_with_language():
    html = render_message_content("```python\nhi\n```", refs=EMPTY_REFS)
    assert html == '<pre><code class="language-python">hi\n</code></pre>'


def test_render_message_content_quote_block():
    assert render_message_content("> hi", refs=EMPTY_REFS) == "<blockquote>hi</blockquote>"


def test_render_message_content_spoiler():
    html = render_message_content("||hi||", refs=EMPTY_REFS)
    assert html == '<details class="spoiler"><summary>Spoiler</summary>hi</details>'


def test_render_message_content_resolves_known_user_mention():
    refs = ResolvedRefs(users={42: "alice"}, channels={})

    html = render_message_content("hi <@42>", refs=refs)

    assert html == 'hi <span class="mention mention-user">@alice</span>'


def test_render_message_content_user_mention_with_nickname_syntax():
    # <@!id> is Discord's "with server nickname" mention form.
    refs = ResolvedRefs(users={42: "alice"}, channels={})

    html = render_message_content("hi <@!42>", refs=refs)

    assert html == 'hi <span class="mention mention-user">@alice</span>'


def test_render_message_content_unresolved_user_mention_falls_back():
    html = render_message_content("hi <@999>", refs=EMPTY_REFS)

    assert html == 'hi <span class="mention mention-user">@unknown user</span>'


def test_render_message_content_role_mention_always_unresolved():
    refs = ResolvedRefs(users={}, channels={}, roles={5: "moderators"})

    html = render_message_content("hi <@&5>", refs=refs)

    assert html == 'hi <span class="mention mention-role">@unknown role</span>'


def test_render_message_content_resolves_known_channel_mention():
    refs = ResolvedRefs(users={}, channels={7: "general"})

    html = render_message_content("hi <#7>", refs=refs)

    assert html == ('hi <span class="mention mention-channel" data-channel-id="7">#general</span>')


def test_render_message_content_unresolved_channel_mention_falls_back():
    html = render_message_content("hi <#999>", refs=EMPTY_REFS)

    assert html == (
        'hi <span class="mention mention-channel" data-channel-id="999">#unknown-channel</span>'
    )


def test_render_message_content_custom_emoji():
    html = render_message_content("hi <:pog:123>", refs=EMPTY_REFS)

    assert html == (
        'hi <img class="emoji" src="https://cdn.discordapp.com/emojis/123.png" alt=":pog:">'
    )


def test_render_message_content_animated_custom_emoji_renders_animated():
    # discord-markdown-ast-parser's lexer doesn't recognize the <a:name:id>
    # form at all (confirmed by reading its source), so the `a:` prefix is
    # stripped before parsing to avoid garbled output -- but the animated-ness
    # is recovered separately (scanned from the raw content before that
    # stripping) so the actual emoji still renders as a real animated .gif.
    html = render_message_content("hi <a:pog:123>", refs=EMPTY_REFS)

    assert html == (
        'hi <img class="emoji" src="https://cdn.discordapp.com/emojis/123.gif" alt=":pog:">'
    )


def test_render_message_content_animated_emoji_id_does_not_leak_to_other_static_emoji():
    html = render_message_content("hi <a:pog:123> and <:wave:456>", refs=EMPTY_REFS)

    assert 'src="https://cdn.discordapp.com/emojis/123.gif"' in html
    assert 'src="https://cdn.discordapp.com/emojis/456.png"' in html


def test_render_message_content_unicode_emoji_shortcode_passthrough():
    # Discord's own client resolves :name: shortcodes to real unicode before
    # sending, so this only appears from bots/webhooks bypassing that -- we
    # don't have a lookup table, so render the literal shortcode text.
    assert render_message_content("hi :thinking:", refs=EMPTY_REFS) == "hi :thinking:"


def test_render_message_content_url_renders_as_link():
    html = render_message_content("see https://example.com/page", refs=EMPTY_REFS)

    assert html == (
        'see <a href="https://example.com/page" rel="noopener noreferrer">'
        "https://example.com/page</a>"
    )


def test_render_message_content_bare_url_in_angle_brackets_suppresses_preview():
    html = render_message_content("see <https://example.com/page>", refs=EMPTY_REFS)

    assert html == (
        'see <a href="https://example.com/page" rel="noopener noreferrer">'
        "https://example.com/page</a>"
    )


def test_render_message_content_nested_formatting():
    # Different delimiter characters nest cleanly (the library's own
    # docstring example: "strikethrough completely inside italic works").
    # ***bold and italic*** (same-character nesting) is a documented,
    # unfixed parser limitation (a literal `# TODO` in its source) -- not
    # exercised here since it's a known upstream gap, not our bug.
    html = render_message_content("**bold _and italic_**", refs=EMPTY_REFS)

    assert html == "<strong>bold <em>and italic</em></strong>"


def test_render_message_content_escapes_html_smuggled_inside_bold():
    html = render_message_content("**<img src=x onerror=alert(1)>**", refs=EMPTY_REFS)

    assert "<img" not in html
    assert "&lt;img" in html


def test_render_message_content_strips_script_tag_smuggled_via_code_language():
    # code_lang comes from user-controlled text right after ``` -- make sure
    # it can't break out of the class="language-..." attribute.
    html = render_message_content('```"><script>alert(1)</script>\nhi\n```', refs=EMPTY_REFS)

    assert "<script>" not in html


def test_collect_referenced_ids_extracts_all_mention_kinds():
    ids = collect_referenced_ids("<@1> <@!2> <@&3> <#4>")

    assert ids == ReferencedIds(
        user_ids=frozenset({1, 2}), role_ids=frozenset({3}), channel_ids=frozenset({4})
    )


def test_collect_referenced_ids_dedups_repeated_mentions():
    ids = collect_referenced_ids("<@1> hi <@1> again")

    assert ids.user_ids == frozenset({1})


def test_collect_referenced_ids_empty_for_plain_text():
    assert collect_referenced_ids("just some text") == ReferencedIds(
        user_ids=frozenset(), role_ids=frozenset(), channel_ids=frozenset()
    )


def test_collect_referenced_ids_finds_mentions_nested_inside_formatting():
    ids = collect_referenced_ids("**hi <@1>**")

    assert ids.user_ids == frozenset({1})
