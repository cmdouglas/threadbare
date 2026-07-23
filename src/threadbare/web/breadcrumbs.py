"""Ancestor-chain breadcrumbs for board/topic pages: links back to each
container level (the guild root, and category if any) above the current
page -- not including the current page itself, since its own <h1> already
names it.
"""

from flask import url_for

from threadbare import urls
from threadbare.db import queries


async def board_breadcrumbs(conn, channel: dict, *, script_root: str) -> list[dict]:
    crumbs = [{"label": "Home", "href": url_for("board_index.board_index")}]
    if channel["parent_id"] is not None:
        category = await queries.get_channel(conn, channel["parent_id"])
        if category is not None:
            # Categories have no page of their own in this app -- shown as
            # plain unlinked text in the trail, not a dead link.
            crumbs.append({"label": category["name"], "href": None})
    return crumbs


async def topic_breadcrumbs(conn, thread: dict, *, script_root: str) -> list[dict]:
    channel = await queries.get_channel(conn, thread["parent_channel_id"])
    if channel is None:
        return [{"label": "Home", "href": url_for("board_index.board_index")}]
    crumbs = await board_breadcrumbs(conn, channel, script_root=script_root)
    crumbs.append(
        {"label": channel["name"], "href": f"{script_root}{urls.board_url(channel['id'])}"}
    )
    return crumbs
