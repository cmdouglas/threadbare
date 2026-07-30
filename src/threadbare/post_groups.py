"""Groups a container's messages into merged posts, the data shape behind the
admin-level "merge consecutive posts by the same author" option (DESIGN.md
§5). Pure, DB-free sibling of reply_tree.py and pseudotopics.py.

Nothing stores group *membership*. Each message only records whether it starts
a post (messages.starts_group, computed at ingestion by db/grouping.py), and a
post is a head plus every message following it until the next head. This
module is the read-side half of that: it turns the flat, ordered rows
get_messages_page already returns into a list of posts.
"""


def group_messages(rows: list[dict]) -> list[list[dict]]:
    """rows must already be in chronological (posted_at, id) order -- the same
    ordering every other query in this codebase sorts on -- and each must
    carry a `starts_group` key.

    Leading continuation rows (a `starts_group=False` row with no head ahead
    of it in this set) become a post of their own rather than being dropped.
    get_messages_page fetches whole posts so that shouldn't arise, but a
    caller that fetched a partial run must still get every message back
    somewhere -- same "keep it visible rather than lose it" rule
    build_reply_tree applies to a reply whose parent isn't in the set.
    """
    groups: list[list[dict]] = []
    for row in rows:
        if row["starts_group"] or not groups:
            groups.append([row])
        else:
            groups[-1].append(row)
    return groups
