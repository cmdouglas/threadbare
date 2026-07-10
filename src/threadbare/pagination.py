"""Pure pagination math, shared by permalinks, jump-to-date, and search
context-links — anywhere "how many items come before this one" needs to
become "which page is it on."
"""

DEFAULT_PAGE_SIZE = 25


def page_number_for_offset(preceding: int, page_size: int = DEFAULT_PAGE_SIZE) -> int:
    return preceding // page_size + 1
