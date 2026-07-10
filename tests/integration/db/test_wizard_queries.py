import asyncio

import psycopg
import pytest
from psycopg.rows import dict_row

from threadbare.db import wizard_queries


async def _cleanup(conn, guild_id):
    await conn.execute("DELETE FROM channels WHERE guild_id = %s", (guild_id,))
    await conn.execute("DELETE FROM guilds WHERE id = %s", (guild_id,))
    await conn.execute("UPDATE wizard_state SET step = 'intro', discord_guild_id = NULL")


async def test_get_or_create_wizard_state_creates_singleton_row_on_first_call(db_conn):
    await db_conn.execute("DELETE FROM wizard_state")

    state = await wizard_queries.get_or_create_wizard_state(db_conn)

    assert state["step"] == "intro"

    again = await wizard_queries.get_or_create_wizard_state(db_conn)
    assert again["step"] == "intro"


async def test_update_wizard_state_sets_given_fields(db_conn):
    await wizard_queries.get_or_create_wizard_state(db_conn)

    await wizard_queries.update_wizard_state(db_conn, step="token", discord_client_id="abc")

    state = await wizard_queries.get_or_create_wizard_state(db_conn)
    assert state["step"] == "token"
    assert state["discord_client_id"] == "abc"


async def test_update_wizard_state_rejects_unknown_field(db_conn):
    with pytest.raises(ValueError):
        await wizard_queries.update_wizard_state(db_conn, not_a_real_column="x")


async def test_seed_guild_and_channels_forces_indexed_false_when_not_yet_confirmed(db_conn):
    guild_id = 555001
    try:
        await wizard_queries.seed_guild_and_channels(
            db_conn,
            {"id": guild_id, "name": "Test Guild", "icon": None},
            [
                {
                    "id": 555002,
                    "guild_id": guild_id,
                    "parent_id": None,
                    "type": 0,
                    "name": "general",
                    "position": 0,
                    "topic": None,
                }
            ],
            already_confirmed=False,
        )

        channels = await wizard_queries.get_channels_for_guild(db_conn, guild_id)
        assert channels[0]["indexed"] is False
    finally:
        await _cleanup(db_conn, guild_id)


async def test_seed_guild_and_channels_preserves_indexed_flags_when_already_confirmed(db_conn):
    guild_id = 555010
    channel_id = 555011
    try:
        await wizard_queries.seed_guild_and_channels(
            db_conn,
            {"id": guild_id, "name": "Test Guild", "icon": None},
            [
                {
                    "id": channel_id,
                    "guild_id": guild_id,
                    "parent_id": None,
                    "type": 0,
                    "name": "general",
                    "position": 0,
                    "topic": None,
                }
            ],
            already_confirmed=False,
        )
        await wizard_queries.confirm_channel_selection(db_conn, guild_id, {channel_id})

        # Revisit the channels step -- re-seeding (e.g. a name change synced
        # from Discord) must not wipe the prior confirmation back to false.
        await wizard_queries.seed_guild_and_channels(
            db_conn,
            {"id": guild_id, "name": "Test Guild", "icon": None},
            [
                {
                    "id": channel_id,
                    "guild_id": guild_id,
                    "parent_id": None,
                    "type": 0,
                    "name": "general-renamed",
                    "position": 0,
                    "topic": None,
                }
            ],
            already_confirmed=True,
        )

        channels = await wizard_queries.get_channels_for_guild(db_conn, guild_id)
        assert channels[0]["indexed"] is True
        assert channels[0]["name"] == "general-renamed"
    finally:
        await _cleanup(db_conn, guild_id)


async def test_confirm_channel_selection_replaces_full_indexed_set(db_conn):
    guild_id = 555020
    channel_a = 555021
    channel_b = 555022
    try:
        await wizard_queries.seed_guild_and_channels(
            db_conn,
            {"id": guild_id, "name": "Test Guild", "icon": None},
            [
                {
                    "id": channel_a,
                    "guild_id": guild_id,
                    "parent_id": None,
                    "type": 0,
                    "name": "a",
                    "position": 0,
                    "topic": None,
                },
                {
                    "id": channel_b,
                    "guild_id": guild_id,
                    "parent_id": None,
                    "type": 0,
                    "name": "b",
                    "position": 1,
                    "topic": None,
                },
            ],
            already_confirmed=False,
        )

        await wizard_queries.confirm_channel_selection(db_conn, guild_id, {channel_a})
        rows = await wizard_queries.get_channels_for_guild(db_conn, guild_id)
        channels = {c["id"]: c["indexed"] for c in rows}
        assert channels[channel_a] is True
        assert channels[channel_b] is False

        # Re-confirming with a different set fully replaces the prior one --
        # unchecking a previously-checked box on a revisit works too.
        await wizard_queries.confirm_channel_selection(db_conn, guild_id, {channel_b})
        rows = await wizard_queries.get_channels_for_guild(db_conn, guild_id)
        channels = {c["id"]: c["indexed"] for c in rows}
        assert channels[channel_a] is False
        assert channels[channel_b] is True
    finally:
        await _cleanup(db_conn, guild_id)


def test_seed_guild_and_channels_persists_across_a_second_connection(test_database_url):
    guild_id = 555030
    channel_id = 555031

    async def _write():
        conn = await psycopg.AsyncConnection.connect(
            test_database_url, autocommit=False, row_factory=dict_row
        )
        try:
            await wizard_queries.seed_guild_and_channels(
                conn,
                {"id": guild_id, "name": "Test Guild", "icon": None},
                [
                    {
                        "id": channel_id,
                        "guild_id": guild_id,
                        "parent_id": None,
                        "type": 0,
                        "name": "general",
                        "position": 0,
                        "topic": None,
                    }
                ],
                already_confirmed=False,
            )
            await conn.commit()
        finally:
            await conn.close()

    try:
        asyncio.run(_write())

        verify_conn = psycopg.connect(test_database_url, row_factory=dict_row)
        try:
            with verify_conn.cursor() as cur:
                cur.execute("SELECT indexed FROM channels WHERE id = %s", (channel_id,))
                row = cur.fetchone()
            assert row is not None
            assert row["indexed"] is False
        finally:
            verify_conn.close()
    finally:
        cleanup_conn = psycopg.connect(test_database_url)
        with cleanup_conn.cursor() as cur:
            cur.execute("DELETE FROM channels WHERE guild_id = %s", (guild_id,))
            cur.execute("DELETE FROM guilds WHERE id = %s", (guild_id,))
        cleanup_conn.commit()
        cleanup_conn.close()
