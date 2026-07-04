"""PSA grading provider — fetch a graded card's identity + population by cert number.

Uses PSA's sanctioned public JSON API (NOT web scraping — the psacard.com site is
Cloudflare-protected). Requires a Bearer token in env `PSA_API_TOKEN`
(free tier ~100 calls/day). Reuses the shared JSON transport.

    GET https://api.psacard.com/publicapi/cert/GetByCertNumber/{cert}
    Authorization: bearer <token>

Returns a normalized, provider-agnostic dict (see `map_cert`) or None if the cert
isn't found / no token. Pure `map_cert` is split out for unit-testing without I/O.
"""

from __future__ import annotations

import os
from typing import Any, Optional

import httpx

from ..tcgplayer.transport import RateLimiter, make_client, request_json
from .formatters import parse_grade, parse_language

COMPANY = "PSA"
_CERT_URL = "https://api.psacard.com/publicapi/cert/GetByCertNumber/{cert}"


def _get_token() -> Optional[str]:
    tok = os.environ.get("PSA_API_TOKEN")
    return tok.strip() if tok else None


def map_cert(data: dict[str, Any]) -> Optional[dict[str, Any]]:
    """PSA GetByCertNumber JSON → normalized grader-identity dict. Pure."""
    cert = (data or {}).get("PSACert")
    if not cert:
        return None
    grade_label = cert.get("CardGrade") or cert.get("GradeDescription")
    return {
        "grading_company": COMPANY,
        "cert_id": str(cert.get("CertNumber")) if cert.get("CertNumber") is not None else None,
        "grader_spec_id": str(cert.get("SpecID")) if cert.get("SpecID") is not None else None,
        "grade": parse_grade(grade_label),
        "grade_label": grade_label,
        "card_year": cert.get("Year"),
        "card_set": cert.get("Brand"),
        "card_category": cert.get("Category"),
        "card_number": cert.get("CardNumber"),
        "card_subject": cert.get("Subject"),
        "card_variety": cert.get("Variety"),
        "card_language": parse_language(cert.get("Variety"), cert.get("Brand"), cert.get("Subject")),
        "population": cert.get("TotalPopulation"),
        "population_higher": cert.get("PopulationHigher"),
    }


async def fetch_cert(
    cert_number: str,
    *,
    token: Optional[str] = None,
    client: Optional[httpx.AsyncClient] = None,
    limiter: Optional[RateLimiter] = None,
) -> Optional[dict[str, Any]]:
    """Fetch + normalize a PSA cert. Returns None if not found. Raises
    RuntimeError with 'PSA_API_TOKEN' if no token is configured."""
    token = token or _get_token()
    if not token:
        raise RuntimeError("PSA_API_TOKEN is not set — cannot query the PSA API.")

    headers = {"Authorization": f"bearer {token}", "Accept": "application/json"}
    url = _CERT_URL.format(cert=cert_number)

    own_client = client is None
    if own_client:
        client = make_client()
    try:
        data = await request_json(client, "GET", url, limiter=limiter, headers=headers)
    finally:
        if own_client:
            await client.aclose()

    if not data:
        return None
    return map_cert(data)
