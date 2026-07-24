import logging

from threadbare.channel_visibility import compute_visible_channel_ids
from threadbare.discord_permissions import ADMINISTRATOR, READ_MESSAGE_HISTORY, VIEW_CHANNEL

EVERYONE_ROLE_ID = 1
HELD_ROLE_ID = 42
OTHER_ROLE_ID = 99
USER_ID = 7

BOTH_REQUIRED = VIEW_CHANNEL | READ_MESSAGE_HISTORY


def test_no_special_roles_sees_only_everyone_public_channels():
    channels = [
        {"id": 100, "parent_id": None},
        {"id": 200, "parent_id": None},
    ]
    role_overwrites = [
        {"channel_id": 200, "role_id": EVERYONE_ROLE_ID, "allow": 0, "deny": VIEW_CHANNEL},
    ]

    visible = compute_visible_channel_ids(
        base_permissions=BOTH_REQUIRED,
        everyone_role_id=EVERYONE_ROLE_ID,
        channels=channels,
        role_overwrites=role_overwrites,
        member_overwrites=[],
    )

    assert visible == {100}


def test_category_level_role_allow_overwrite_regains_access_after_everyone_deny():
    channels = [{"id": 300, "parent_id": 30}]
    role_overwrites = [
        {"channel_id": 30, "role_id": EVERYONE_ROLE_ID, "allow": 0, "deny": VIEW_CHANNEL},
        {"channel_id": 30, "role_id": HELD_ROLE_ID, "allow": VIEW_CHANNEL, "deny": 0},
    ]

    visible = compute_visible_channel_ids(
        base_permissions=READ_MESSAGE_HISTORY,
        everyone_role_id=EVERYONE_ROLE_ID,
        channels=channels,
        role_overwrites=role_overwrites,
        member_overwrites=[],
    )

    assert visible == {300}


def test_member_specific_deny_overrides_every_role_level_allow():
    channels = [{"id": 400, "parent_id": None}]
    role_overwrites = [
        {"channel_id": 400, "role_id": EVERYONE_ROLE_ID, "allow": BOTH_REQUIRED, "deny": 0},
        {"channel_id": 400, "role_id": HELD_ROLE_ID, "allow": BOTH_REQUIRED, "deny": 0},
    ]
    member_overwrites = [{"channel_id": 400, "allow": 0, "deny": VIEW_CHANNEL}]

    visible = compute_visible_channel_ids(
        base_permissions=BOTH_REQUIRED,
        everyone_role_id=EVERYONE_ROLE_ID,
        channels=channels,
        role_overwrites=role_overwrites,
        member_overwrites=member_overwrites,
    )

    assert visible == set()


def test_administrator_short_circuits_regardless_of_any_deny_overwrite():
    channels = [{"id": 500, "parent_id": None}]
    role_overwrites = [
        {"channel_id": 500, "role_id": EVERYONE_ROLE_ID, "allow": 0, "deny": BOTH_REQUIRED},
    ]

    visible = compute_visible_channel_ids(
        base_permissions=ADMINISTRATOR,
        everyone_role_id=EVERYONE_ROLE_ID,
        channels=channels,
        role_overwrites=role_overwrites,
        member_overwrites=[],
    )

    assert visible == {500}


def test_channel_with_no_category_treats_category_tier_as_empty():
    channels = [{"id": 600, "parent_id": None}]

    visible = compute_visible_channel_ids(
        base_permissions=BOTH_REQUIRED,
        everyone_role_id=EVERYONE_ROLE_ID,
        channels=channels,
        role_overwrites=[],
        member_overwrites=[],
    )

    assert visible == {600}


def test_multiple_role_overwrites_for_the_same_channel_combine():
    # Discord's merge order: all denies first, then all allows -- so a
    # second held role's allow wins over a first held role's deny of the
    # same bit, matching discord_permissions' own tested precedent.
    channels = [{"id": 700, "parent_id": None}]
    role_overwrites = [
        {"channel_id": 700, "role_id": HELD_ROLE_ID, "allow": 0, "deny": VIEW_CHANNEL},
        {"channel_id": 700, "role_id": OTHER_ROLE_ID, "allow": VIEW_CHANNEL, "deny": 0},
    ]

    visible = compute_visible_channel_ids(
        base_permissions=READ_MESSAGE_HISTORY,
        everyone_role_id=EVERYONE_ROLE_ID,
        channels=channels,
        role_overwrites=role_overwrites,
        member_overwrites=[],
    )

    assert visible == {700}


def test_row_dicts_are_not_passed_through_as_overwritelike_directly():
    # Plain dict_row-shaped rows must be wrapped into something with
    # attribute access before reaching compute_effective_permissions --
    # feeding rows straight through would raise AttributeError.
    channels = [{"id": 800, "parent_id": None}]
    role_overwrites = [
        {"channel_id": 800, "role_id": EVERYONE_ROLE_ID, "allow": BOTH_REQUIRED, "deny": 0},
    ]

    visible = compute_visible_channel_ids(
        base_permissions=0,
        everyone_role_id=EVERYONE_ROLE_ID,
        channels=channels,
        role_overwrites=role_overwrites,
        member_overwrites=[],
    )

    assert visible == {800}


def test_debug_logging_names_the_channel_and_final_visibility_decision(caplog):
    channels = [{"id": 900, "parent_id": None}]
    role_overwrites = [
        {"channel_id": 900, "role_id": HELD_ROLE_ID, "allow": 0, "deny": VIEW_CHANNEL},
    ]

    with caplog.at_level(logging.DEBUG):
        visible = compute_visible_channel_ids(
            base_permissions=BOTH_REQUIRED,
            everyone_role_id=EVERYONE_ROLE_ID,
            channels=channels,
            role_overwrites=role_overwrites,
            member_overwrites=[],
        )

    assert visible == set()
    assert "channel 900" in caplog.text
    assert "visible=False" in caplog.text
    # The held role's deny overwrite shows up in the channel-tier detail
    # line, not just the final effective-permissions summary -- this is
    # the line that answers "which overwrite is/isn't taking effect".
    assert str(HELD_ROLE_ID) in caplog.text


def test_debug_logging_reports_visible_true_when_channel_passes(caplog):
    channels = [{"id": 950, "parent_id": None}]

    with caplog.at_level(logging.DEBUG):
        visible = compute_visible_channel_ids(
            base_permissions=BOTH_REQUIRED,
            everyone_role_id=EVERYONE_ROLE_ID,
            channels=channels,
            role_overwrites=[],
            member_overwrites=[],
        )

    assert visible == {950}
    assert "channel 950" in caplog.text
    assert "visible=True" in caplog.text
