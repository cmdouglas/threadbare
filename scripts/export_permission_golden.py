"""Regenerates tests/fixtures/permission_golden.json from the live
DISCORD_TEST_GUILD_ID test server (DESIGN.md §7's "golden tests against
permission fixtures exported from a real server", ROADMAP.md Phase 2's last
checklist item). Dev-only, not part of the test suite or CI -- run by hand
whenever the fixture server layout below needs to change.

Requires DISCORD_BOT_TOKEN/DISCORD_TEST_GUILD_ID (.env) and the following
server layout to already exist (the bot's own minimal permissions can't
create roles/overwrites itself, by this project's deliberate design -- see
DEVELOPMENT.md for why the bot stays read-only):

1. A second custom role, `threadbare_testing_2` (no permissions granted at
   creation), assigned to the bot alongside its existing `threadbare_testing`
   role.
2. A category `Permission Fixtures` with a category-level overwrite denying
   `threadbare_testing` View Channel, containing one channel:
   - `golden-category-precedence`: channel-level overwrite allowing
     `threadbare_testing` View Channel (channel tier must beat category tier).
3. Three top-level (no-category) channels:
   - `golden-deny-allow`: @everyone denied View Channel,
     `threadbare_testing` allowed View Channel (role-level allow must beat
     everyone-level deny at the same tier).
   - `golden-multi-role`: `threadbare_testing` denied View Channel,
     `threadbare_testing_2` allowed View Channel (one held role's allow must
     beat another held role's deny at the same tier).
   - `golden-admin`: @everyone denied both View Channel and Read Message
     History, with `threadbare_testing_2` granted the Administrator
     permission (must short-circuit the deny entirely).

Usage: uv run python scripts/export_permission_golden.py
"""

import asyncio
import json
import os
from datetime import UTC, datetime
from pathlib import Path

import discord
from dotenv import load_dotenv

OUTPUT_PATH = Path(__file__).parent.parent / "tests" / "fixtures" / "permission_golden.json"

EXPECTED_CATEGORY = "Permission Fixtures"
EXPECTED_CHANNELS = (
    "golden-category-precedence",
    "golden-deny-allow",
    "golden-multi-role",
    "golden-admin",
)
EXPECTED_ROLES = ("threadbare_testing", "threadbare_testing_2")


def _overwrite_rows(overwrites: dict, role_id_by_name: dict) -> list[dict]:
    name_by_role_id = {v: k for k, v in role_id_by_name.items()}
    rows = []
    for target, overwrite in overwrites.items():
        if not isinstance(target, discord.Role):
            continue  # member-tier overwrites aren't part of this fixture
        name = "@everyone" if target.name == "@everyone" else name_by_role_id.get(target.id)
        if name is None:
            continue  # an overwrite on some other role -- not part of this fixture
        allow, deny = overwrite.pair()
        rows.append({"role": name, "allow": allow.value, "deny": deny.value})
    return rows


async def main() -> None:
    load_dotenv()
    intents = discord.Intents.none()
    intents.guilds = True
    client = discord.Client(intents=intents)
    guild_id = int(os.environ["DISCORD_TEST_GUILD_ID"])

    @client.event
    async def on_ready():
        try:
            guild = await client.fetch_guild(guild_id)
            roles = await guild.fetch_roles()
            everyone = next(r for r in roles if r.name == "@everyone")
            role_id_by_name = {r.name: r.id for r in roles if r.name in EXPECTED_ROLES}
            missing_roles = set(EXPECTED_ROLES) - set(role_id_by_name)
            if missing_roles:
                raise SystemExit(
                    f"Missing expected role(s): {sorted(missing_roles)} -- "
                    "see this script's docstring for setup."
                )

            channels = await guild.fetch_channels()
            by_name = {c.name: c for c in channels}
            missing_channels = set(EXPECTED_CHANNELS) - set(by_name)
            if missing_channels:
                raise SystemExit(
                    f"Missing expected channel(s): {sorted(missing_channels)} -- "
                    "see this script's docstring for setup."
                )
            category = by_name.get(EXPECTED_CATEGORY)
            if category is None:
                raise SystemExit(
                    f"Missing expected category {EXPECTED_CATEGORY!r} -- "
                    "see this script's docstring for setup."
                )

            fixture = {
                "_meta": {
                    "description": (
                        "Real role permission bitfields and channel/category overwrites "
                        "exported from the live DISCORD_TEST_GUILD_ID test server (DESIGN.md "
                        "§7's 'golden tests against permission fixtures exported from a real "
                        "server'), not hand-invented numbers. Captured via "
                        "scripts/export_permission_golden.py against a purpose-built server "
                        "layout, each channel isolating one Discord permission-resolution edge "
                        "case. Regenerate by re-running that script after recreating the same "
                        "role/channel/overwrite layout documented in its docstring."
                    ),
                    "guild_id": guild_id,
                    "captured_at": datetime.now(UTC).date().isoformat(),
                },
                "everyone_role": {
                    "id": everyone.id,
                    "name": "@everyone",
                    "permissions": everyone.permissions.value,
                },
                "roles": {
                    name: {
                        "id": role_id,
                        "permissions": next(r for r in roles if r.id == role_id).permissions.value,
                    }
                    for name, role_id in role_id_by_name.items()
                },
                "categories": {
                    EXPECTED_CATEGORY: {
                        "id": category.id,
                        "overwrites": _overwrite_rows(category.overwrites, role_id_by_name),
                    }
                },
                "channels": {
                    name: {
                        "id": by_name[name].id,
                        "parent_category": EXPECTED_CATEGORY
                        if by_name[name].category_id == category.id
                        else None,
                        "overwrites": _overwrite_rows(by_name[name].overwrites, role_id_by_name),
                    }
                    for name in EXPECTED_CHANNELS
                },
            }

            OUTPUT_PATH.write_text(json.dumps(fixture, indent=2) + "\n")
            print(f"Wrote {OUTPUT_PATH}")
        finally:
            await client.close()

    await client.start(os.environ["DISCORD_BOT_TOKEN"])


if __name__ == "__main__":
    asyncio.run(main())
