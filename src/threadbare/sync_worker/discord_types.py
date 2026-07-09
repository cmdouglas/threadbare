"""Narrow structural types describing only the attributes our code reads off
discord.py objects. Business logic is written against these Protocols, never
against discord.py types directly, so it can be unit tested with plain
fixtures (SimpleNamespace, dataclasses) instead of a live gateway connection.
"""

from typing import Protocol


class OverwriteLike(Protocol):
    allow: int
    deny: int
