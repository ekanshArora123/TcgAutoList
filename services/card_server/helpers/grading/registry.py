"""Grading-provider registry — maps a grading company to its fetcher module.

Add a new company by writing a `<company>.py` with a `fetch_cert(cert, ...)`
coroutine returning the normalized dict (see psa.map_cert) and registering it
here. Callers do `get_provider("PSA").fetch_cert(cert)`.
"""

from __future__ import annotations

from types import ModuleType
from typing import Optional

from . import psa

_PROVIDERS: dict[str, ModuleType] = {
    psa.COMPANY: psa,
}


def get_provider(grading_company: str) -> Optional[ModuleType]:
    return _PROVIDERS.get((grading_company or "").upper())


def supported_companies() -> list[str]:
    return sorted(_PROVIDERS.keys())
