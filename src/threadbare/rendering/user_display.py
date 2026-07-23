"""Poster role color -> CSS hex string. Pure, no I/O, mirrors
rendering/avatars.py's shape.
"""


def role_color_hex(color: int | None) -> str | None:
    # 0 is Discord's own sentinel for "no custom color" on a role, same as
    # None -- both mean "use the theme's default text color".
    if not color:
        return None
    return f"#{color:06x}"
