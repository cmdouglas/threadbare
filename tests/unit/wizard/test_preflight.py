from threadbare.discord_permissions import READ_MESSAGE_HISTORY, VIEW_CHANNEL
from threadbare.wizard.preflight import (
    RestOverwrite,
    compute_bot_effective_permissions,
    compute_channel_permission_table,
    message_content_intent_ok,
    parse_overwrites,
)

EVERYONE_ROLE_ID = 1
BOT_USER_ID = 999
BOT_ROLE_ID = 42

BOTH_REQUIRED = VIEW_CHANNEL | READ_MESSAGE_HISTORY

ROLE_TYPE = 0
MEMBER_TYPE = 1


def _overwrite(id_, type_, *, allow=0, deny=0):
    return RestOverwrite(id=id_, type=type_, allow=allow, deny=deny)


def test_plain_allow_when_base_permissions_grant_both_and_no_overwrites():
    result = compute_bot_effective_permissions(
        base_permissions=BOTH_REQUIRED,
        everyone_role_id=EVERYONE_ROLE_ID,
        bot_role_ids={BOT_ROLE_ID},
        bot_user_id=BOT_USER_ID,
        category_overwrites=[],
        channel_overwrites=[],
    )
    assert (result & BOTH_REQUIRED) == BOTH_REQUIRED


def test_guild_level_grant_insufficient_with_no_overwrites():
    result = compute_bot_effective_permissions(
        base_permissions=0,
        everyone_role_id=EVERYONE_ROLE_ID,
        bot_role_ids={BOT_ROLE_ID},
        bot_user_id=BOT_USER_ID,
        category_overwrites=[],
        channel_overwrites=[],
    )
    assert (result & BOTH_REQUIRED) != BOTH_REQUIRED


def test_category_everyone_overwrite_denies_despite_sufficient_base_permissions():
    category_overwrites = [_overwrite(EVERYONE_ROLE_ID, ROLE_TYPE, deny=VIEW_CHANNEL)]
    result = compute_bot_effective_permissions(
        base_permissions=BOTH_REQUIRED,
        everyone_role_id=EVERYONE_ROLE_ID,
        bot_role_ids={BOT_ROLE_ID},
        bot_user_id=BOT_USER_ID,
        category_overwrites=category_overwrites,
        channel_overwrites=[],
    )
    assert (result & VIEW_CHANNEL) == 0


def test_channel_role_overwrite_reallows_over_a_category_deny():
    category_overwrites = [_overwrite(EVERYONE_ROLE_ID, ROLE_TYPE, deny=VIEW_CHANNEL)]
    channel_overwrites = [_overwrite(BOT_ROLE_ID, ROLE_TYPE, allow=VIEW_CHANNEL)]
    result = compute_bot_effective_permissions(
        base_permissions=READ_MESSAGE_HISTORY,
        everyone_role_id=EVERYONE_ROLE_ID,
        bot_role_ids={BOT_ROLE_ID},
        bot_user_id=BOT_USER_ID,
        category_overwrites=category_overwrites,
        channel_overwrites=channel_overwrites,
    )
    assert (result & BOTH_REQUIRED) == BOTH_REQUIRED


def test_channel_member_specific_overwrite_denies_bot_despite_every_role_level_allow():
    channel_overwrites = [
        _overwrite(EVERYONE_ROLE_ID, ROLE_TYPE, allow=BOTH_REQUIRED),
        _overwrite(BOT_ROLE_ID, ROLE_TYPE, allow=BOTH_REQUIRED),
        _overwrite(BOT_USER_ID, MEMBER_TYPE, deny=VIEW_CHANNEL),
    ]
    result = compute_bot_effective_permissions(
        base_permissions=BOTH_REQUIRED,
        everyone_role_id=EVERYONE_ROLE_ID,
        bot_role_ids={BOT_ROLE_ID},
        bot_user_id=BOT_USER_ID,
        category_overwrites=[],
        channel_overwrites=channel_overwrites,
    )
    assert (result & VIEW_CHANNEL) == 0


def test_administrator_base_permission_bypasses_every_overwrite():
    ADMINISTRATOR = 1 << 3
    channel_overwrites = [_overwrite(EVERYONE_ROLE_ID, ROLE_TYPE, deny=BOTH_REQUIRED)]
    result = compute_bot_effective_permissions(
        base_permissions=ADMINISTRATOR,
        everyone_role_id=EVERYONE_ROLE_ID,
        bot_role_ids={BOT_ROLE_ID},
        bot_user_id=BOT_USER_ID,
        category_overwrites=[],
        channel_overwrites=channel_overwrites,
    )
    assert (result & BOTH_REQUIRED) == BOTH_REQUIRED


def test_parse_overwrites_converts_raw_rest_json():
    raw = [{"id": "1", "type": 0, "allow": "1024", "deny": "0"}]
    parsed = parse_overwrites(raw)
    assert parsed == [RestOverwrite(id=1, type=0, allow=1024, deny=0)]


def test_message_content_intent_ok_is_none_when_no_message_available():
    assert message_content_intent_ok(None) is None


def test_message_content_intent_ok_is_false_for_empty_content():
    assert message_content_intent_ok({"content": ""}) is False


def test_message_content_intent_ok_is_true_for_nonempty_content():
    assert message_content_intent_ok({"content": "hello"}) is True


def test_compute_channel_permission_table_assembles_independent_per_channel_results():
    channels = [
        {"id": 100, "parent_id": None, "overwrites": []},
        {
            "id": 200,
            "parent_id": None,
            "overwrites": [_overwrite(EVERYONE_ROLE_ID, ROLE_TYPE, deny=VIEW_CHANNEL)],
        },
    ]
    table = compute_channel_permission_table(
        base_permissions=BOTH_REQUIRED,
        everyone_role_id=EVERYONE_ROLE_ID,
        bot_role_ids={BOT_ROLE_ID},
        bot_user_id=BOT_USER_ID,
        channels=channels,
        category_overwrites={},
    )
    by_id = {row["channel_id"]: row for row in table}

    assert by_id[100]["ok"] is True
    assert by_id[100]["overwrite_denied"] is False

    assert by_id[200]["ok"] is False
    assert by_id[200]["overwrite_denied"] is True


def test_overwrite_denied_is_false_when_base_grant_already_insufficient():
    channels = [{"id": 300, "parent_id": None, "overwrites": []}]
    table = compute_channel_permission_table(
        base_permissions=0,
        everyone_role_id=EVERYONE_ROLE_ID,
        bot_role_ids={BOT_ROLE_ID},
        bot_user_id=BOT_USER_ID,
        channels=channels,
        category_overwrites={},
    )
    assert table[0]["ok"] is False
    assert table[0]["overwrite_denied"] is False
