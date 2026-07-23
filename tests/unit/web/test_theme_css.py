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
