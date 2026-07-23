from threadbare.rendering.embeds import render_embed_html
from threadbare.rendering.markdown import ResolvedRefs

EMPTY_REFS = ResolvedRefs(users={}, channels={})


def _full_embed_row(**overrides):
    row = {
        "position": 0,
        "type": "rich",
        "title": "A title",
        "description": "some **bold** text",
        "url": "https://example.com",
        "color": 0x00FF00,
        "author_name": "alice",
        "author_url": "https://example.com/alice",
        "footer_text": "a footer",
        "image_url": "https://example.com/image.png",
        "thumbnail_url": "https://example.com/thumb.png",
        "video_url": None,
        "fields": [{"name": "k", "value": "v", "inline": True}],
    }
    row.update(overrides)
    return row


def _empty_embed_row(**overrides):
    row = {
        "position": 0,
        "type": "rich",
        "title": None,
        "description": None,
        "url": None,
        "color": None,
        "author_name": None,
        "author_url": None,
        "footer_text": None,
        "image_url": None,
        "thumbnail_url": None,
        "video_url": None,
        "fields": [],
    }
    row.update(overrides)
    return row


def test_render_embed_html_full_embed_includes_all_pieces():
    html = render_embed_html(_full_embed_row(), refs=EMPTY_REFS)

    assert 'class="embed"' in html
    assert "alice" in html
    assert 'href="https://example.com/alice"' in html
    assert "A title" in html
    assert 'href="https://example.com"' in html
    assert "<strong>bold</strong>" in html  # description ran through markdown
    assert 'src="https://example.com/image.png"' in html
    assert 'src="https://example.com/thumb.png"' in html
    assert '<dl class="embed-fields">' in html
    assert "<dt>k</dt>" in html
    assert "<dd>v</dd>" in html
    assert "a footer" in html
    assert "#00ff00" in html


def test_render_embed_html_handles_missing_optional_fields():
    html = render_embed_html(_empty_embed_row(), refs=EMPTY_REFS)

    assert html == '<div class="embed"></div>'


def test_render_embed_html_title_without_url_is_not_a_link():
    html = render_embed_html(_empty_embed_row(title="A title"), refs=EMPTY_REFS)

    assert "<a " not in html
    assert "A title" in html


def test_render_embed_html_description_resolves_mentions():
    refs = ResolvedRefs(users={42: "alice"}, channels={})
    html = render_embed_html(_empty_embed_row(description="hi <@42>"), refs=refs)

    assert '<span class="mention mention-user">@alice</span>' in html


def test_render_embed_html_escapes_field_content():
    html = render_embed_html(
        _empty_embed_row(fields=[{"name": "<script>", "value": "v", "inline": False}]),
        refs=EMPTY_REFS,
    )

    assert "<script>" not in html


def test_render_embed_html_link_type_lone_thumbnail_renders_large():
    row = _empty_embed_row(type="link", thumbnail_url="https://example.com/thumb.png")
    html = render_embed_html(row, refs=EMPTY_REFS)

    assert 'class="embed-image"' in html
    assert 'class="embed-thumbnail"' not in html


def test_render_embed_html_rich_type_thumbnail_renders_small_and_floated():
    row = _empty_embed_row(type="rich", thumbnail_url="https://example.com/thumb.png")
    html = render_embed_html(row, refs=EMPTY_REFS)

    assert 'class="embed-thumbnail"' in html
    assert 'class="embed-image"' not in html


def test_render_embed_html_missing_type_lone_thumbnail_renders_large():
    row = _empty_embed_row(type=None, thumbnail_url="https://example.com/thumb.png")
    html = render_embed_html(row, refs=EMPTY_REFS)

    assert 'class="embed-image"' in html


def test_render_embed_html_video_renders_as_a_video_tag():
    # e.g. a Tenor/Giphy "gifv"-style unfurl: image/thumbnail are just a
    # static preview frame, the actual animated content is embed.video.
    row = _empty_embed_row(video_url="https://example.com/clip.mp4")
    html = render_embed_html(row, refs=EMPTY_REFS)

    assert '<video class="embed-video" src="https://example.com/clip.mp4"' in html
    assert "autoplay" in html
    assert "loop" in html
    assert "muted" in html


def test_render_embed_html_video_takes_precedence_over_the_static_image():
    row = _empty_embed_row(
        video_url="https://example.com/clip.mp4", image_url="https://example.com/preview.png"
    )
    html = render_embed_html(row, refs=EMPTY_REFS)

    assert "<video" in html
    assert 'class="embed-image"' not in html
