from datetime import UTC, datetime

from threadbare.rendering.attachments import (
    display_filename,
    is_spoiler_attachment,
    render_attachment_html,
)

EXPIRES_AT = datetime(2026, 1, 2, tzinfo=UTC)


def _row(*, filename, content_type, cached_url="https://cdn.example/x"):
    return {
        "id": 1,
        "filename": filename,
        "content_type": content_type,
        "size": 100,
        "cached_url": cached_url,
        "url_expires_at": EXPIRES_AT,
    }


def test_is_spoiler_attachment_detects_prefix():
    assert is_spoiler_attachment("SPOILER_cat.png") is True


def test_is_spoiler_attachment_false_for_normal_filename():
    assert is_spoiler_attachment("cat.png") is False


def test_display_filename_strips_spoiler_prefix():
    assert display_filename("SPOILER_cat.png") == "cat.png"


def test_display_filename_leaves_normal_filename_unchanged():
    assert display_filename("cat.png") == "cat.png"


def test_render_attachment_html_image():
    html = render_attachment_html(_row(filename="cat.png", content_type="image/png"))

    assert 'class="attachment attachment-image"' in html
    assert "<img" in html
    assert 'src="https://cdn.example/x"' in html
    assert 'alt="cat.png"' in html


def test_render_attachment_html_generic_file():
    html = render_attachment_html(_row(filename="notes.txt", content_type="text/plain"))

    assert 'class="attachment attachment-file"' in html
    assert "<img" not in html
    assert "notes.txt" in html


def test_render_attachment_html_generic_file_for_missing_content_type():
    html = render_attachment_html(_row(filename="notes.txt", content_type=None))

    assert 'class="attachment attachment-file"' in html


def test_render_attachment_html_wraps_spoiler_attachments():
    html = render_attachment_html(_row(filename="SPOILER_cat.png", content_type="image/png"))

    assert html.startswith('<details class="spoiler"><summary>Spoiler</summary>')
    assert html.endswith("</details>")
    # displayed with the SPOILER_ prefix stripped, not the raw filename
    assert 'alt="cat.png"' in html
    assert "SPOILER_" not in html


def test_render_attachment_html_escapes_filename():
    html = render_attachment_html(_row(filename='<script>.txt', content_type="text/plain"))

    assert "<script>" not in html
