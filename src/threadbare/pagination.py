"""Pure pagination math, shared by permalinks, jump-to-date, and search
context-links — anywhere "how many items come before this one" needs to
become "which page is it on."
"""

DEFAULT_PAGE_SIZE = 25


def page_number_for_offset(preceding: int, page_size: int = DEFAULT_PAGE_SIZE) -> int:
    return preceding // page_size + 1


def page_window(
    current: int, total_pages: int, *, edge: int = 3, spread: int = 2
) -> list[int | None]:
    """Page numbers to render, with None marking an elided gap. Collapses to
    one contiguous run (no gaps at all) whenever the edge/middle windows
    already overlap or touch -- e.g. total_pages=6 renders all 6 pages, no
    gap marker.
    """
    if total_pages <= 0:
        return []
    head = set(range(1, min(edge, total_pages) + 1))
    tail = set(range(max(1, total_pages - edge + 1), total_pages + 1))
    middle = set(range(max(1, current - spread), min(total_pages, current + spread) + 1))
    pages = sorted(head | middle | tail)
    result: list[int | None] = []
    for i, p in enumerate(pages):
        if i > 0 and p != pages[i - 1] + 1:
            result.append(None)
        result.append(p)
    return result
