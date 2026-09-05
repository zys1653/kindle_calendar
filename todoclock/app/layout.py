"""Shared layout constants used by controllers and pure renderers."""
HOURS_PER_PAGE = 4


def hour_page(index, count):
    pages = max(1, (count + HOURS_PER_PAGE - 1) // HOURS_PER_PAGE)
    return max(0, min(index, pages - 1)), pages
