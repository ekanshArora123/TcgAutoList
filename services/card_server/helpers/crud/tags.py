"""Comma-separated tag manipulation — the one shared piece of logic behind the
raw `inventory.tags` and the parallel `graded_inventory.tags` columns.

Both chains store tags the same way (a comma-separated TEXT column), so the
parse/add/remove behavior is extracted here and each helper's add_tag/remove_tag
calls it instead of re-implementing the string handling.
"""

from __future__ import annotations

from typing import Optional


def parse_tags(csv: Optional[str]) -> list[str]:
    """Split a tags column into a clean list (drops blanks/whitespace)."""
    return [t.strip() for t in csv.split(",") if t.strip()] if csv else []


def add_tag(csv: Optional[str], tag: str) -> Optional[str]:
    """Return the tags column with `tag` present (idempotent). Order preserved."""
    tags = parse_tags(csv)
    if tag not in tags:
        tags.append(tag)
    return ",".join(tags) if tags else None


def remove_tag(csv: Optional[str], tag: str) -> Optional[str]:
    """Return the tags column with `tag` absent; None when nothing remains."""
    tags = [t for t in parse_tags(csv) if t != tag]
    return ",".join(tags) if tags else None
