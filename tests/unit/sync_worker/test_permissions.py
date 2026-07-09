from dataclasses import dataclass

from threadbare.sync_worker.permissions import (
    READ_MESSAGE_HISTORY,
    VIEW_CHANNEL,
    compute_is_public,
)


@dataclass
class Overwrite:
    allow: int = 0
    deny: int = 0


BOTH_REQUIRED = VIEW_CHANNEL | READ_MESSAGE_HISTORY


def test_no_overwrites_public_when_base_permissions_grant_both():
    assert compute_is_public(BOTH_REQUIRED, None, None) is True


def test_no_overwrites_private_when_base_permissions_deny_view():
    assert compute_is_public(0, None, None) is False


def test_category_denies_view_with_no_channel_overwrite_stays_private():
    category = Overwrite(deny=VIEW_CHANNEL)
    assert compute_is_public(BOTH_REQUIRED, category, None) is False


def test_channel_overwrite_allows_after_category_denies():
    category = Overwrite(deny=VIEW_CHANNEL)
    channel = Overwrite(allow=VIEW_CHANNEL)
    assert compute_is_public(READ_MESSAGE_HISTORY, category, channel) is True


def test_channel_overwrite_denies_even_when_base_permissions_allow():
    channel = Overwrite(deny=VIEW_CHANNEL)
    assert compute_is_public(BOTH_REQUIRED, None, channel) is False


def test_channel_overwrite_allows_even_when_base_permissions_deny():
    channel = Overwrite(allow=BOTH_REQUIRED)
    assert compute_is_public(0, None, channel) is True


def test_view_allowed_but_read_history_denied_is_private():
    channel = Overwrite(deny=READ_MESSAGE_HISTORY)
    assert compute_is_public(BOTH_REQUIRED, None, channel) is False


def test_channel_overwrite_takes_precedence_over_category_on_same_bit():
    # Category allows, channel explicitly denies the same bit — channel wins
    # because it's resolved last.
    category = Overwrite(allow=VIEW_CHANNEL)
    channel = Overwrite(deny=VIEW_CHANNEL)
    assert compute_is_public(READ_MESSAGE_HISTORY, category, channel) is False
