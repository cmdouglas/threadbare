"""Guards migration 0005_wizard_state.sql's deliberate "no secrets stored"
decision: the wizard's bot token and OAuth client secret must never gain a
column here, since this table (unlike the wizard's Flask session) has no
short-lived-storage story and would otherwise put secrets at rest in a
table with no backup/retention safeguards.
"""

EXPECTED_COLUMNS = {
    "id",
    "step",
    "discord_guild_id",
    "discord_client_id",
    "discord_oauth_redirect_uri",
    "channels_confirmed",
    "preflight_results",
    "updated_at",
}


async def test_wizard_state_has_no_secret_columns(db_conn):
    async with db_conn.cursor() as cur:
        await cur.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'wizard_state'"
        )
        rows = await cur.fetchall()

    columns = {row["column_name"] for row in rows}
    assert columns == EXPECTED_COLUMNS
    assert "discord_bot_token" not in columns
    assert "discord_client_secret" not in columns
