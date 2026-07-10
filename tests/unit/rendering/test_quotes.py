from threadbare.rendering.quotes import truncate_snippet


def test_truncate_snippet_returns_short_text_unchanged():
    assert truncate_snippet("hello", limit=280) == "hello"


def test_truncate_snippet_at_exact_limit_is_unchanged():
    text = "a" * 280
    assert truncate_snippet(text, limit=280) == text


def test_truncate_snippet_truncates_and_adds_ellipsis():
    text = "a" * 281
    result = truncate_snippet(text, limit=280)

    assert result == "a" * 280 + "…"


def test_truncate_snippet_strips_trailing_whitespace_before_ellipsis():
    text = "word " + "a" * 276  # 281 chars, truncation lands right after "word "
    result = truncate_snippet(text, limit=5)

    assert result == "word…"


def test_truncate_snippet_does_not_split_a_multi_codepoint_character():
    # family emoji is a single grapheme made of multiple codepoints joined by
    # ZWJ -- truncating mid-sequence would produce a broken/mismatched glyph.
    family = "\U0001f468‍\U0001f469‍\U0001f467"  # man+woman+girl ZWJ family
    text = "hi " + family
    result = truncate_snippet(text, limit=100)

    assert result == text  # under the limit, so nothing should be cut at all


def test_truncate_snippet_default_limit_is_280():
    text = "a" * 300
    assert truncate_snippet(text) == "a" * 280 + "…"
