"""Condition vocabulary — the one place that knows how internal condition codes
relate to the five conditions TCGplayer actually sells.

Three separate callers need the same answers and used to each half-answer them:
the collector (which variants must I fetch for this card?), the pricing algorithm
(what does an in-between condition interpolate between?), and the TCGplayer
formatters (which API conditions does this code map to?). Keeping the mapping
here means a new condition spelling is added once.

Vocabulary:
  primary     — a condition TCGplayer has data for: NM, LP, MP, HP, DMG.
  in-between  — a borderline card graded between two primaries (MP-LP). TCGplayer
                has no such grade; its price interpolates between its neighbors.
  alias       — a legacy/alternate spelling of a primary (DM -> DMG, MINT -> NM).
"""

from __future__ import annotations

from typing import Optional

from .config import IN_BETWEEN_CONDITIONS, PRIMARY_CONDITIONS

# Legacy / alternate spellings that resolve to a primary condition.
#   DM   — older imports spelled Damaged "DM"; TCGplayer calls it "Damaged" (DMG).
#   MINT — TCGplayer has no MINT tier, so it is priced as NM (see compute_price).
CONDITION_ALIASES: dict[str, str] = {
    "DM": "DMG",
    "MINT": "NM",
}

# Primaries excluding MINT, which is an alias rather than a tier TCGplayer sells.
FETCHABLE_CONDITIONS = tuple(c for c in PRIMARY_CONDITIONS if c != "MINT")


def normalize_condition(condition: str) -> str:
    """Resolve a condition to its canonical spelling.

    Trims whitespace, upper-cases, and maps aliases onto their primary. Leaves
    in-between codes (and anything unrecognized) untouched.
    """
    code = (condition or "").strip().upper()
    return CONDITION_ALIASES.get(code, code)


def is_primary(condition: str) -> bool:
    """True if TCGplayer sells this condition directly (after alias resolution)."""
    return normalize_condition(condition) in FETCHABLE_CONDITIONS


def primary_neighbors(condition: str) -> Optional[tuple[str, str]]:
    """The (better, worse) primaries an in-between condition sits between.

    Returns None for primaries and unrecognized codes — callers use `is_primary`
    to tell those two cases apart.
    """
    return IN_BETWEEN_CONDITIONS.get(normalize_condition(condition))


def expand_to_primaries(condition: str) -> tuple[str, ...]:
    """The primary condition(s) that must be fetched to price this condition.

    A primary needs only itself; an in-between needs BOTH neighbors, since its
    price is the interpolation of the two. Unrecognized codes expand to nothing
    so the collector skips them instead of fetching a bogus variant.
    """
    code = normalize_condition(condition)
    if is_primary(code):
        return (code,)
    neighbors = primary_neighbors(code)
    return neighbors if neighbors else ()


def normalize_finish(finish: str) -> str:
    """Canonical finish spelling. Guards against stored values that carry stray
    whitespace or are empty (both exist in the live DB from early imports)."""
    return (finish or "").strip() or "Regular"
