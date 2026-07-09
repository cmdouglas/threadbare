import os

import pytest
from dotenv import load_dotenv

load_dotenv()


@pytest.fixture(autouse=True)
def _require_live_discord_credentials():
    missing = [
        var for var in ("DISCORD_BOT_TOKEN", "DISCORD_TEST_GUILD_ID") if not os.environ.get(var)
    ]
    if missing:
        pytest.skip(f"live_discord tests require {', '.join(missing)} to be set")
