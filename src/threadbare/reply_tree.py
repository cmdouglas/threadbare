"""Reconstructs a container's reply structure into a nested tree, the data
shape behind topic.topic_tree_view's alternative to flat chronological
pagination. messages.reply_to_id is a single self-referencing FK -- Discord's
own reply feature, not a stored chain -- but following each row's parent
link transitively reassembles the same multi-level chains a chat client
shows, since a parent is itself just another row in the same set.
"""


def build_reply_tree(rows: list[dict]) -> list[dict]:
    """rows must already be in chronological (posted_at, id) order, the same
    ordering every other query in this codebase sorts on -- sibling order in
    the resulting tree falls directly out of that, no extra sort needed.

    A row becomes a root if it has no reply_to_id, or if reply_to_id doesn't
    point at another row in this same set (a reply to a message outside the
    container, or one this instance never indexed) -- treating it as a root
    rather than dropping it is the only way to keep every message visible
    somewhere in the tree.
    """
    nodes = {row["id"]: {"row": row, "children": []} for row in rows}
    roots = []
    for row in rows:
        node = nodes[row["id"]]
        parent = nodes.get(row["reply_to_id"])
        if parent is not None:
            parent["children"].append(node)
        else:
            roots.append(node)
    return roots
