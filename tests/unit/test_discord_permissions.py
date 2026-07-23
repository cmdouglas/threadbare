from dataclasses import dataclass

from threadbare.discord_permissions import (
    ADMINISTRATOR,
    READ_MESSAGE_HISTORY,
    VIEW_CHANNEL,
    OverwriteTier,
    apply_overwrite,
    compute_effective_permissions,
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
    category = Overwrite(allow=VIEW_CHANNEL)
    channel = Overwrite(deny=VIEW_CHANNEL)
    assert compute_is_public(READ_MESSAGE_HISTORY, category, channel) is False


def test_apply_overwrite_returns_unchanged_permissions_when_overwrite_is_none():
    assert apply_overwrite(BOTH_REQUIRED, None) == BOTH_REQUIRED


# compute_effective_permissions/OverwriteTier: the shared resolver both
# compute_is_public and wizard/preflight.compute_bot_effective_permissions
# now delegate to. These exercise cases neither narrow wrapper isolates on
# its own -- see DESIGN.md §7's note that this is the one place a bug is a
# disclosure bug, not a rendering bug.


def test_multiple_role_overwrites_combine_with_allow_winning_over_deny_on_conflict():
    # Discord's documented role-overwrite merge order: all denies first,
    # then all allows -- so a second role's allow wins over a first role's
    # deny of the same bit, not the reverse.
    denying_role = Overwrite(deny=VIEW_CHANNEL)
    allowing_role = Overwrite(allow=VIEW_CHANNEL)
    channel = OverwriteTier(role_overwrites=(denying_role, allowing_role))

    result = compute_effective_permissions(0, channel=channel)

    assert (result & VIEW_CHANNEL) == VIEW_CHANNEL


def test_member_overwrite_applies_with_no_role_tier_present():
    channel = OverwriteTier(member_overwrite=Overwrite(allow=BOTH_REQUIRED))

    result = compute_effective_permissions(0, channel=channel)

    assert (result & BOTH_REQUIRED) == BOTH_REQUIRED


def test_arbitrary_third_party_role_ids_neither_everyone_nor_a_modeled_identity():
    # Proves the shared function is genuinely identity-agnostic -- nothing
    # here special-cases "the bot" or "@everyone"; it's just whatever
    # OverwriteTier the caller built.
    category = OverwriteTier(
        everyone_overwrite=Overwrite(deny=VIEW_CHANNEL),
        role_overwrites=(Overwrite(allow=VIEW_CHANNEL),),
    )
    channel = OverwriteTier(member_overwrite=Overwrite(deny=READ_MESSAGE_HISTORY))

    result = compute_effective_permissions(0, category=category, channel=channel)

    assert (result & VIEW_CHANNEL) == VIEW_CHANNEL
    assert (result & READ_MESSAGE_HISTORY) == 0


def test_administrator_short_circuits_via_shared_function_directly():
    channel = OverwriteTier(everyone_overwrite=Overwrite(deny=BOTH_REQUIRED))

    result = compute_effective_permissions(ADMINISTRATOR, channel=channel)

    assert (result & BOTH_REQUIRED) == BOTH_REQUIRED


def test_role_overwrites_are_applied_unfiltered_trusting_the_caller():
    # compute_effective_permissions does no filtering of its own -- if a
    # caller puts an overwrite in role_overwrites, it applies, full stop.
    # The filtering-by-role-membership contract belongs to the caller (see
    # wizard/preflight._tier_from_rest_overwrites).
    channel = OverwriteTier(role_overwrites=(Overwrite(deny=VIEW_CHANNEL),))

    result = compute_effective_permissions(BOTH_REQUIRED, channel=channel)

    assert (result & VIEW_CHANNEL) == 0


def test_category_and_channel_tiers_both_apply_in_order_via_shared_function():
    category = OverwriteTier(everyone_overwrite=Overwrite(allow=VIEW_CHANNEL))
    channel = OverwriteTier(everyone_overwrite=Overwrite(deny=VIEW_CHANNEL))

    result = compute_effective_permissions(READ_MESSAGE_HISTORY, category=category, channel=channel)

    assert (result & VIEW_CHANNEL) == 0
