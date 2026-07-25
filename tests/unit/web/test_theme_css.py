"""Regression guard for CSS rules that have no other test coverage --
plain string/regex checks over the stylesheet source, not real rendering.
"""

import re
from pathlib import Path

import pytest

from threadbare.web.themes import AVAILABLE_THEMES

STATIC_DIR = Path(__file__).parents[3] / "src" / "threadbare" / "web" / "static"


@pytest.mark.parametrize("stylesheet", sorted(AVAILABLE_THEMES.values()))
def test_attachment_image_rule_caps_height_as_well_as_width(stylesheet):
    css = (STATIC_DIR / stylesheet).read_text()

    match = re.search(r"\.attachment img\s*\{([^}]*)\}", css)

    assert match is not None, f"{stylesheet} has no .attachment img rule"
    assert "max-height" in match.group(1)


@pytest.mark.parametrize("stylesheet", sorted(AVAILABLE_THEMES.values()))
def test_post_avatar_rule_is_sized(stylesheet):
    css = (STATIC_DIR / stylesheet).read_text()

    match = re.search(r"\.post-avatar[^{]*\{([^}]*)\}", css)

    assert match is not None, f"{stylesheet} has no .post-avatar rule"
    assert "width" in match.group(1)


@pytest.mark.parametrize("stylesheet", sorted(AVAILABLE_THEMES.values()))
def test_embed_video_rule_is_capped_like_embed_image(stylesheet):
    css = (STATIC_DIR / stylesheet).read_text()

    match = re.search(r"\.embed-video[^{]*\{([^}]*)\}", css)

    assert match is not None, f"{stylesheet} has no .embed-video rule"
    assert "max-height" in match.group(1)


@pytest.mark.parametrize("stylesheet", sorted(AVAILABLE_THEMES.values()))
def test_bot_badge_rule_exists(stylesheet):
    css = (STATIC_DIR / stylesheet).read_text()

    assert re.search(r"\.bot-badge\s*\{", css) is not None, f"{stylesheet} has no .bot-badge rule"


@pytest.mark.parametrize("stylesheet", sorted(AVAILABLE_THEMES.values()))
def test_user_role_badge_rule_exists(stylesheet):
    css = (STATIC_DIR / stylesheet).read_text()

    assert re.search(r"\.user-role-badge\s*\{", css) is not None, (
        f"{stylesheet} has no .user-role-badge rule"
    )


@pytest.mark.parametrize("stylesheet", sorted(AVAILABLE_THEMES.values()))
def test_unread_dot_rule_exists(stylesheet):
    css = (STATIC_DIR / stylesheet).read_text()

    assert re.search(r"\.unread-dot\s*\{", css) is not None, f"{stylesheet} has no .unread-dot rule"


@pytest.mark.parametrize("stylesheet", sorted(AVAILABLE_THEMES.values()))
def test_visited_row_rule_exists(stylesheet):
    css = (STATIC_DIR / stylesheet).read_text()

    assert re.search(r"\.board-row-visited", css) is not None, (
        f"{stylesheet} has no .board-row-visited rule"
    )


@pytest.mark.parametrize("stylesheet", sorted(AVAILABLE_THEMES.values()))
def test_unread_count_rule_exists(stylesheet):
    css = (STATIC_DIR / stylesheet).read_text()

    assert re.search(r"\.unread-count\s*\{", css) is not None, (
        f"{stylesheet} has no .unread-count rule"
    )


@pytest.mark.parametrize("stylesheet", sorted(AVAILABLE_THEMES.values()))
def test_reaction_filter_rule_exists(stylesheet):
    css = (STATIC_DIR / stylesheet).read_text()

    assert re.search(r"\.reaction-filter\s*\{", css) is not None, (
        f"{stylesheet} has no .reaction-filter rule"
    )


@pytest.mark.parametrize("stylesheet", sorted(AVAILABLE_THEMES.values()))
def test_thread_children_rule_is_indented(stylesheet):
    css = (STATIC_DIR / stylesheet).read_text()

    match = re.search(r"\.thread-children\s*\{([^}]*)\}", css)

    assert match is not None, f"{stylesheet} has no .thread-children rule"
    assert "margin-left" in match.group(1)


def _root_properties(stylesheet: str) -> set[str]:
    css = (STATIC_DIR / stylesheet).read_text()
    root_block = re.search(r":root\s*\{(.*?)\}", css, re.DOTALL).group(1)
    return set(re.findall(r"(--[a-z0-9-]+)\s*:", root_block))


def test_every_theme_declares_the_identical_root_property_set():
    """The four built-ins are hand-synced by convention (ROADMAP.md), so nothing
    structural stops one drifting. This is the single test that catches it: a
    property added to one theme and forgotten in the others shows up here rather
    than as a silently-unstyled element in three themes.
    """
    by_theme = {name: _root_properties(name) for name in sorted(AVAILABLE_THEMES.values())}
    reference_name, reference = next(iter(by_theme.items()))

    for name, properties in by_theme.items():
        assert properties == reference, (
            f"{name}'s :root property set differs from {reference_name}'s -- "
            f"missing: {sorted(reference - properties)}, "
            f"extra: {sorted(properties - reference)}"
        )


# Selectors emitted by templates that every theme must actually style. Each of
# these was found unstyled in at least one theme by an audit pass: the two
# unread row classes in all four, the preference controls in all or most.
_SELECTORS_EVERY_THEME_MUST_STYLE = [
    ".board-row-unread",
    ".topic-row-unread",
    ".board-row-visited",
    ".topic-row-visited",
    ".theme-switcher",
    ".avatar-toggle",
    ".posts-per-page-switcher",
    ".current-option",
    ".unread-dot",
]


@pytest.mark.parametrize("stylesheet", sorted(AVAILABLE_THEMES.values()))
@pytest.mark.parametrize("selector", _SELECTORS_EVERY_THEME_MUST_STYLE)
def test_theme_styles_every_selector_the_templates_emit(stylesheet, selector):
    css = (STATIC_DIR / stylesheet).read_text()

    assert selector in css, f"{stylesheet} has no rule mentioning {selector}"


def test_color_danger_is_actually_used_not_just_declared():
    """--color-danger is required of every uploaded bundle
    (theme_bundle.REQUIRED_CUSTOM_PROPERTIES), so the app has to honour it
    somewhere -- it was declared by all four themes and referenced by none.
    """
    for stylesheet in sorted(AVAILABLE_THEMES.values()):
        css = (STATIC_DIR / stylesheet).read_text()
        assert "var(--color-danger" in css, (
            f"{stylesheet} declares --color-danger but never uses it"
        )
