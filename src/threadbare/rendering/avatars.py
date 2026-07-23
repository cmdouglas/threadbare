"""User avatar -> CDN URL. Pure, no I/O. Discord's avatar CDN URLs are
static and unsigned (unlike attachments' signed, expiring cached_url) — no
proxy or expiry handling needed here, same as rendering/emoji.py.
"""

DEFAULT_AVATAR_COUNT = 6


def avatar_url(user_id: int, avatar_hash: str | None) -> str:
    if avatar_hash:
        ext = "gif" if avatar_hash.startswith("a_") else "png"
        return f"https://cdn.discordapp.com/avatars/{user_id}/{avatar_hash}.{ext}"
    # Discord's current (post-2023 username-migration) default-avatar
    # formula — doesn't need a discriminator, which this project never
    # captured (users only ever get a discord.Member/User's display_name).
    index = (user_id >> 22) % DEFAULT_AVATAR_COUNT
    return f"https://cdn.discordapp.com/embed/avatars/{index}.png"
