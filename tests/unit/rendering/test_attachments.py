from datetime import UTC, datetime

from threadbare.rendering.attachments import (
    display_filename,
    is_spoiler_attachment,
    render_attachment_html,
)

EXPIRES_AT = datetime(2026, 1, 2, tzinfo=UTC)


def _row(*, filename, content_type, attachment_id=1, cached_url="https://cdn.example/x"):
    return {
        "id": attachment_id,
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
    # links through the /att/{id} proxy, not the raw (often-expired) cached_url
    assert 'href="/att/1"' in html
    assert 'src="/att/1"' in html
    assert "cdn.example" not in html
    assert 'alt="cat.png"' in html


def test_render_attachment_html_generic_file():
    html = render_attachment_html(_row(filename="notes.txt", content_type="text/plain"))

    assert 'class="attachment attachment-file"' in html
    assert "<img" not in html
    assert 'href="/att/1"' in html
    assert "notes.txt" in html


def test_render_attachment_html_generic_file_for_missing_content_type():
    html = render_attachment_html(_row(filename="notes.txt", content_type=None))

    assert 'class="attachment attachment-file"' in html


def test_render_attachment_html_falls_back_to_extension_when_content_type_missing():
    # Discord omits content_type for some attachments (older uploads, or
    # detection failures) -- filename is the only field Discord guarantees.
    html = render_attachment_html(_row(filename="image0.jpg", content_type=None))

    assert 'class="attachment attachment-image"' in html
    assert "<img" in html


def test_render_attachment_html_wraps_spoiler_attachments():
    html = render_attachment_html(_row(filename="SPOILER_cat.png", content_type="image/png"))

    assert html.startswith('<details class="spoiler"><summary>Spoiler</summary>')
    assert html.endswith("</details>")
    # displayed with the SPOILER_ prefix stripped, not the raw filename
    assert 'alt="cat.png"' in html
    assert "SPOILER_" not in html


def test_render_attachment_html_uses_the_attachment_id_in_the_proxy_url():
    html = render_attachment_html(
        _row(filename="cat.png", content_type="image/png", attachment_id=999)
    )

    assert 'href="/att/999"' in html
    assert 'src="/att/999"' in html


def test_render_attachment_html_escapes_filename():
    html = render_attachment_html(_row(filename="<script>.txt", content_type="text/plain"))

    assert "<script>" not in html


def test_render_attachment_html_prefixes_urls_with_script_root():
    html = render_attachment_html(
        _row(filename="cat.png", content_type="image/png"), script_root="/mirror"
    )

    assert 'href="/mirror/att/1"' in html
    assert 'src="/mirror/att/1"' in html


def test_render_attachment_html_script_root_defaults_empty():
    html = render_attachment_html(_row(filename="cat.png", content_type="image/png"))

    assert 'href="/att/1"' in html
