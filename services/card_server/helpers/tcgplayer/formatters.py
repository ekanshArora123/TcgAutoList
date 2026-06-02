"""Formatters for converting internal DB values to TCGplayer API format.

Internal DB uses short codes (NM, LP, Holo, Reverse-Holo). TCGplayer API uses
full strings (Near Mint, Holofoil, Reverse Holofoil). Ported from formatters.ts.
"""

from __future__ import annotations

# ─── Condition Formatting ────────────────────────────────────

_CONDITION_MAP: dict[str, list[str]] = {
    "MINT": ["Near Mint"],
    "NM": ["Near Mint"],
    "LP-NM": ["Lightly Played", "Near Mint"],
    "LP": ["Lightly Played"],
    "MP-LP": ["Moderately Played", "Lightly Played"],
    "MP": ["Moderately Played"],
    "HP-MP": ["Heavily Played", "Moderately Played"],
    "HP": ["Heavily Played"],
    "DM": ["Damaged"],
    "DMG": ["Damaged"],
    "DM-HP": ["Damaged", "Heavily Played"],
}


def format_condition_for_api(condition: str) -> list[str]:
    """Convert internal condition code(s) to TCGplayer API condition strings.

    In-between conditions return BOTH neighbors since TCGplayer has no in-between.
    """
    return _CONDITION_MAP.get(condition, ["Damaged"])


# ─── Finish Formatting ───────────────────────────────────────

# WOTC-era sets that use "Unlimited" / "Unlimited Holofoil" finish naming.
_UNLIMITED_FINISH_SETS = frozenset(
    {
        "Base Set (Shadowless)",
        "Jungle",
        "Fossil",
        "Gym Challenge",
        "Gym Heroes",
        "Team Rocket",
        "Neo Genesis",
        "Neo Discovery",
        "Neo Revelation",
        "Neo Destiny",
    }
)


def format_finish_for_api(finish: str, specialty_one: str, set_name: str) -> str:
    """Convert internal finish + specialty + set name to TCGplayer API 'printing' value."""
    if specialty_one == "First Edition":
        return "1st Edition Holofoil" if finish == "Holo" else "1st Edition"

    if set_name in _UNLIMITED_FINISH_SETS:
        return "Unlimited Holofoil" if finish == "Holo" else "Unlimited"

    if finish == "Reverse-Holo":
        return "Reverse Holofoil"
    if finish == "Holo":
        return "Holofoil"
    return "Normal"


def parse_finish_from_api(printing: str) -> dict[str, str]:
    """Convert TCGplayer API finish back to internal format."""
    if printing == "1st Edition Holofoil":
        return {"finish": "Holo", "specialty_one": "First Edition"}
    if printing == "1st Edition":
        return {"finish": "Regular", "specialty_one": "First Edition"}
    if printing == "Unlimited Holofoil":
        return {"finish": "Holo", "specialty_one": "None"}
    if printing == "Unlimited":
        return {"finish": "Regular", "specialty_one": "None"}
    if printing == "Reverse Holofoil":
        return {"finish": "Reverse-Holo", "specialty_one": "None"}
    if printing == "Holofoil":
        return {"finish": "Holo", "specialty_one": "None"}
    return {"finish": "Regular", "specialty_one": "None"}


def parse_condition_from_api(api_condition: str) -> str:
    """Convert TCGplayer API condition back to internal format."""
    return {
        "Near Mint": "NM",
        "Lightly Played": "LP",
        "Moderately Played": "MP",
        "Heavily Played": "HP",
        "Damaged": "DMG",
    }.get(api_condition, "DMG")
