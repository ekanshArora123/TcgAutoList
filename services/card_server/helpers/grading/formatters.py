"""Pure formatters for grading-company data → internal representation. No I/O."""

from __future__ import annotations

import re
from typing import Optional

_GRADE_NUM = re.compile(r"(\d+(?:\.\d+)?)")

# Language tokens grading companies embed in variety/brand/subject strings.
_LANGUAGES = (
    "JAPANESE", "FRENCH", "GERMAN", "ITALIAN", "SPANISH", "PORTUGUESE",
    "KOREAN", "CHINESE", "DUTCH", "ENGLISH",
)


def parse_grade(label: Optional[str]) -> Optional[float]:
    """Numeric grade from a grader's grade label. 'PR 1' → 1.0, 'GEM-MT 10' → 10.0,
    'NM-MT 8.5' → 8.5. Returns None for label-only grades like 'Authentic'."""
    if not label:
        return None
    m = _GRADE_NUM.search(label)
    return float(m.group(1)) if m else None


def parse_language(*fields: Optional[str]) -> str:
    """Best-effort language from grader text fields (variety/brand/subject).
    Grading companies mark non-English cards clearly. Defaults to 'English'."""
    blob = " ".join(f.upper() for f in fields if f)
    for lang in _LANGUAGES:
        if lang in blob:
            return lang.capitalize()
    return "English"
