"""PSA grading provider — scrape the public PSA cert website for a graded card's
identity + population by cert number.

Why scraping instead of the official API
-----------------------------------------
PSA publishes a sanctioned JSON API
(``GET https://api.psacard.com/publicapi/cert/GetByCertNumber/{cert}``), and it
returns exactly the fields we want. But the account's quota is ~1 call/day —
every subsequent call comes back ``HTTP 429 "API calls quota exceeded! maximum
admitted 1 per Day"`` — so the API path is unusable in practice.

The public cert *website* is NOT quota-limited; it's only Cloudflare-protected
and JavaScript-rendered. A plain ``httpx``/``requests`` GET — and even a
TLS-impersonating ``curl_cffi`` GET — hits Cloudflare's managed JS challenge
(HTTP 403 "Just a moment…"). So we drive a real browser (Playwright + Chromium)
which clears the challenge and renders the page, then we read the data straight
out of the DOM.

**Headed, not headless.** Cloudflare's challenge fingerprints headless Chrome
and refuses to clear it (verified: headless — even with stealth patches — stays
stuck on "Just a moment…"; headed sails through). So we launch a *headed*
browser by default. The window must be genuinely visible on-screen — minimizing
it or moving it off-screen makes Cloudflare treat it as headless and refuse to
clear — so a brief browser window is expected during a scrape. This needs a
desktop/display session. Set ``PSA_HEADLESS=1`` to force headless (useful only
where you've separately defeated Cloudflare, e.g. a residential IP that isn't
challenged).

Two pages are scraped and combined:

  1. ``https://www.psacard.com/cert/{cert}/psa`` — the card's identity. This page
     is PUBLIC. The "Item Information" block is a ``<dl>`` of ``<dt>``/``<dd>``
     label/value rows (Cert Number, Item Grade, Year, Brand/Title, Subject, Card
     Number, Category, Variety/Pedigree). The page also carries a
     ``/spec/psa/{specId}`` link — ``specId`` is PSA's SpecID and the key to the
     population report.

  2. ``https://www.psacard.com/spec/psa/{specId}?p=population`` — the population
     report, a Grade/Amount table. ``TotalPopulation`` = the amount at this
     card's own grade; ``PopulationHigher`` = the summed amount of every
     strictly-higher *numeric* grade ("Auth" and label-only rows are excluded).

     **This page is login-gated** (it redirects to a Collectors/PSA sign-in when
     unauthenticated). So population is only scraped when a *signed-in* browser
     profile is supplied via ``PSA_USER_DATA_DIR`` — a persistent Chrome profile
     you authenticate once via ``python -m ...psa --login`` (you type the
     credentials into the real browser window; this module never handles them).
     Without a signed-in profile, ``population`` / ``population_higher`` come back
     None and the rest of the card is still returned in full.

``map_cert`` is a **pure** function over the scraped dict (identity fields +
population rows), so it is unit-testable from a static fixture without a browser
— mirroring the repo's other ``map_*`` fetchers. The DOM extraction
(``_CERT_JS`` / ``_POP_JS``, run via ``page.evaluate``) is the fragile part: PSA
can change class names / markup at any time and the selectors below would then
need updating.
"""

from __future__ import annotations

import asyncio
import os
import re
from typing import Any, Optional

from .formatters import parse_grade, parse_language

COMPANY = "PSA"

_CERT_URL = "https://www.psacard.com/cert/{cert}/psa"
_POP_URL = "https://www.psacard.com/spec/psa/{spec}?p=population"

# A current, desktop-Chrome UA. Cloudflare fingerprints obviously-automated
# agents; a real browser-like UA (plus Playwright's real Chromium) gets through.
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# How long to wait for a page to clear Cloudflare + render its target element.
_NAV_TIMEOUT_MS = 45_000

# Injected before page scripts run to sand off the most obvious automation tells
# Cloudflare looks at. (Necessary but not sufficient — headless is still blocked;
# see the module docstring. Kept because it hardens the headed path too.)
_STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
window.chrome = window.chrome || {runtime: {}};
Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
"""

# Item-Information <dt> labels → our internal field names.
_LABELS = {
    "Cert Number": "cert_number",
    "Item Grade": "grade_label",
    "Year": "year",
    "Brand/Title": "brand",
    "Subject": "subject",
    "Card Number": "card_number",
    "Category": "category",
    "Variety/Pedigree": "variety",
}

# --- In-page extraction scripts (run via page.evaluate) --------------------

# Pull the Item-Information label/value pairs + the SpecID from the /spec link.
# We read ``textContent`` (not ``innerText``) because the <dd> cells carry a CSS
# ``uppercase`` transform — textContent preserves the true casing (e.g.
# "TCG Cards", not "TCG CARDS").
_CERT_JS = r"""() => {
  const rows = {};
  document.querySelectorAll('dl div').forEach(d => {
    const dt = d.querySelector('dt');
    const dd = d.querySelector('dd');
    if (dt && dd) rows[dt.textContent.trim()] = dd.textContent.trim();
  });
  const link = document.querySelector('a[href*="/spec/psa/"]');
  const m = link ? (link.getAttribute('href') || '').match(/\/spec\/psa\/(\d+)/) : null;
  return { rows, spec_id: m ? m[1] : null };
}"""

# Pull the population report: [{grade, amount}] from the Grade/Amount table.
# Data rows use <td>; the header (Grade/Amount/Qualifier) uses <th>, so filtering
# on <td> drops the header automatically.
_POP_JS = r"""() => {
  const table = document.querySelector('table');
  if (!table) return [];
  const out = [];
  table.querySelectorAll('tr').forEach(tr => {
    const cells = [...tr.querySelectorAll('td')].map(td => td.textContent.trim());
    if (cells.length >= 2) out.push({ grade: cells[0], amount: cells[1] });
  });
  return out;
}"""


def _to_int(value: Optional[str]) -> Optional[int]:
    """Parse a population count like ``"1,600"`` → ``1600``. None on non-numeric."""
    if value is None:
        return None
    digits = re.sub(r"[^\d]", "", str(value))
    return int(digits) if digits else None


def _summarize_population(
    rows: Optional[list[dict[str, Any]]], grade: Optional[float]
) -> tuple[Optional[int], Optional[int]]:
    """From the scraped Grade/Amount rows, compute ``(population, population_higher)``
    for a card graded ``grade``:

      * ``population``       — the amount at this exact numeric grade.
      * ``population_higher``— the summed amount of every strictly-higher numeric
        grade. Non-numeric rows ("Auth") are excluded from both, matching PSA's
        API semantics.

    Returns ``(None, None)`` when the grade is unknown or no rows were scraped.
    ``population`` is None if this grade doesn't appear in the table; when the
    grade is the top of the scale, ``population_higher`` is a legitimate 0.
    """
    if grade is None or not rows:
        return None, None
    at_grade: Optional[int] = None
    higher = 0
    for row in rows:
        row_grade = parse_grade(row.get("grade"))
        amount = _to_int(row.get("amount"))
        if row_grade is None or amount is None:
            continue  # 'Auth' / label-only rows — not part of the numeric ladder
        if row_grade == grade:
            at_grade = (at_grade or 0) + amount
        elif row_grade > grade:
            higher += amount
    return at_grade, higher


def map_cert(scraped: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Scraped PSA cert data → normalized, provider-agnostic grader-identity dict.

    Pure (no I/O), so it's unit-testable from a static fixture. ``scraped`` is the
    shape produced by the browser extraction: the Item-Information fields plus a
    ``population_rows`` list of ``{"grade", "amount"}`` from the pop report::

        {"cert_number", "grade_label", "year", "brand", "subject",
         "card_number", "category", "variety", "spec_id", "population_rows": [...]}

    Returns None if the payload has no cert number (i.e. nothing was found).
    """
    if not scraped or not scraped.get("cert_number"):
        return None

    grade_label = scraped.get("grade_label")
    grade = parse_grade(grade_label)
    population, population_higher = _summarize_population(
        scraped.get("population_rows"), grade
    )
    variety = scraped.get("variety")
    brand = scraped.get("brand")
    subject = scraped.get("subject")

    return {
        "grading_company": COMPANY,
        "cert_id": str(scraped.get("cert_number")),
        "grader_spec_id": str(scraped["spec_id"]) if scraped.get("spec_id") else None,
        "grade": grade,
        "grade_label": grade_label,
        "card_year": scraped.get("year"),
        "card_set": brand,
        "card_category": scraped.get("category"),
        "card_number": scraped.get("card_number"),
        "card_subject": subject,
        "card_variety": variety,
        "card_language": parse_language(variety, brand, subject),
        "population": population,
        "population_higher": population_higher,
    }


# --- Browser I/O -----------------------------------------------------------
#
# Two runtime facts shape this layer:
#   * Cloudflare blocks *headless* Chrome, so we run headed by default.
#   * The cert page is PUBLIC, but the population report (spec page) is behind a
#     PSA sign-in. So identity always works; population only works when a
#     *signed-in* browser profile is supplied via ``PSA_USER_DATA_DIR`` (a
#     persistent Chrome profile you authenticate once with ``--login`` — this
#     module never handles your credentials). Without it, population degrades
#     gracefully to None instead of failing.


def _is_headless() -> bool:
    return os.environ.get("PSA_HEADLESS", "").strip().lower() in ("1", "true", "yes")


def _launch_kwargs() -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "headless": _is_headless(),
        "args": ["--disable-blink-features=AutomationControlled", "--no-sandbox"],
    }
    # Optional real-Chrome channel (recommended when reusing an existing Chrome
    # profile for the authenticated population scrape).
    channel = os.environ.get("PSA_BROWSER_CHANNEL", "").strip()
    if channel:
        kwargs["channel"] = channel
    return kwargs


async def _render(page: Any, url: str, ready_selector: str, *, required: bool) -> bool:
    """Navigate to ``url`` and wait until ``ready_selector`` appears. Cloudflare's
    JS challenge, when present, clears on its own within the timeout (headed) and
    the target element then renders.

    Returns True once the target is present, else False (page rendered but the
    target never showed — e.g. a "not found" cert or the sign-in wall on a
    population page for an unauthenticated session).

    Only when ``required`` is True (the identity page — we can't proceed without
    it) do we distinguish a stuck Cloudflare wall and raise RuntimeError. The
    population page is best-effort (``required=False``): any failure just yields
    False so the caller can fall back to None population.
    """
    from playwright.async_api import TimeoutError as PlaywrightTimeoutError

    await page.goto(url, wait_until="domcontentloaded", timeout=_NAV_TIMEOUT_MS)
    try:
        await page.wait_for_selector(ready_selector, timeout=_NAV_TIMEOUT_MS, state="attached")
        return True
    except PlaywrightTimeoutError:
        if required:
            blob = await page.evaluate(
                "() => ((document.body ? document.body.innerText : '') + ' ' + (document.title || ''))"
            )
            if re.search(r"just a moment|checking your browser|verify you are human", blob, re.I):
                raise RuntimeError(
                    f"PSA Cloudflare challenge was not solved within "
                    f"{_NAV_TIMEOUT_MS // 1000}s (headless is blocked — run headed): {url}"
                ) from None
        return False


async def _scrape_identity(page: Any, cert_number: str) -> Optional[dict[str, Any]]:
    """Scrape the (public) cert page's Item-Information + SpecID. None if the cert
    doesn't exist (the "Oops!" page has no Item-Information ``<dl>``)."""
    if not await _render(page, _CERT_URL.format(cert=cert_number), "dl dt", required=True):
        return None
    data = await page.evaluate(_CERT_JS)
    raw = data.get("rows") or {}
    if not raw.get("Cert Number"):
        return None  # rendered a page, but not a cert record
    scraped = {field: raw.get(label) for label, field in _LABELS.items()}
    scraped["spec_id"] = data.get("spec_id")
    return scraped


async def _scrape_population(page: Any, spec_id: str) -> list[dict[str, Any]]:
    """Scrape the Grade/Amount population table for a SpecID. Empty list if the
    report isn't reachable (no data, or the sign-in wall for an unauthenticated
    session — population is login-gated)."""
    if not await _render(page, _POP_URL.format(spec=spec_id), "table td", required=False):
        return []
    return await page.evaluate(_POP_JS) or []


async def _new_context(pw: Any) -> tuple[Any, Any]:
    """Build a browser context, returning ``(context, closable)``. If
    ``PSA_USER_DATA_DIR`` is set we use a *persistent* context on that profile
    (so a one-time sign-in sticks and the population report becomes scrapable);
    otherwise an ephemeral context (identity only)."""
    profile_dir = os.environ.get("PSA_USER_DATA_DIR", "").strip()
    ctx_opts = dict(
        user_agent=_USER_AGENT, viewport={"width": 1366, "height": 900}, locale="en-US"
    )
    if profile_dir:
        context = await pw.chromium.launch_persistent_context(
            profile_dir, **_launch_kwargs(), **ctx_opts
        )
        closable = context  # closing the persistent context closes its browser
    else:
        browser = await pw.chromium.launch(**_launch_kwargs())
        context = await browser.new_context(**ctx_opts)
        closable = browser
    await context.add_init_script(_STEALTH_JS)
    return context, closable


async def fetch_cert(cert_number: str, **_ignored: Any) -> Optional[dict[str, Any]]:
    """Fetch + normalize a PSA cert by scraping the public PSA website.

    Positional-callable as ``await fetch_cert(cert_id)`` (the collection service
    calls it that way). Extra keyword args are accepted and ignored so legacy
    callers (which used to pass token/client/limiter for the old API path) don't
    break.

    Identity is scraped from the public cert page. Population is scraped from the
    login-gated spec page and is only populated when a signed-in profile is
    configured (``PSA_USER_DATA_DIR``); otherwise ``population`` /
    ``population_higher`` come back None.

    Returns the normalized dict from :func:`map_cert`, or None if the cert
    doesn't exist. Raises RuntimeError on real failures (Playwright missing,
    browser launch failure, Cloudflare never solved on the identity page).
    """
    cert_number = str(cert_number).strip()

    try:
        from playwright.async_api import async_playwright
    except ImportError as err:  # pragma: no cover - environment/setup issue
        raise RuntimeError(
            "Playwright is required to scrape PSA. Install it with "
            "`pip install playwright` and `playwright install chromium`."
        ) from err

    async with async_playwright() as pw:
        try:
            context, closable = await _new_context(pw)
        except Exception as err:  # pragma: no cover - environment/setup issue
            raise RuntimeError(f"Failed to launch a browser for PSA scrape: {err}") from err

        try:
            page = context.pages[0] if context.pages else await context.new_page()

            scraped = await _scrape_identity(page, cert_number)
            if scraped is None:
                return None

            # Population is login-gated; only worth a page load with a persistent
            # (potentially signed-in) profile. Without one it would just hit the
            # sign-in wall, so skip it and leave population None.
            spec_id = scraped.get("spec_id")
            authed = bool(os.environ.get("PSA_USER_DATA_DIR", "").strip())
            scraped["population_rows"] = (
                await _scrape_population(page, spec_id) if (spec_id and authed) else []
            )
            return map_cert(scraped)
        finally:
            # Resource-safe: always tear the browser down, even on error.
            await closable.close()


async def _login() -> None:  # pragma: no cover - interactive helper
    """Open the PSA sign-in page in a persistent, headed profile so the user can
    authenticate ONCE by hand. The session cookie then lives in
    ``PSA_USER_DATA_DIR`` and later ``fetch_cert`` runs can scrape population.
    This helper never sees or handles credentials — the user types them into the
    real browser window."""
    from playwright.async_api import async_playwright

    profile_dir = os.environ.get("PSA_USER_DATA_DIR", "").strip()
    if not profile_dir:
        raise SystemExit("Set PSA_USER_DATA_DIR to a profile directory first.")
    print(f"Opening PSA sign-in using profile: {profile_dir}")
    print("Log in by hand in the browser window, then press Enter here when done.")
    async with async_playwright() as pw:
        kwargs = {**_launch_kwargs(), "headless": False}
        context = await pw.chromium.launch_persistent_context(profile_dir, **kwargs)
        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto("https://www.psacard.com/", wait_until="domcontentloaded")
        await asyncio.get_event_loop().run_in_executor(None, input)
        await context.close()


if __name__ == "__main__":  # pragma: no cover - manual smoke test / login
    import json
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "--login":
        asyncio.run(_login())
    else:
        cert = sys.argv[1] if len(sys.argv) > 1 else "94597302"
        print(json.dumps(asyncio.run(fetch_cert(cert)), indent=2, ensure_ascii=False))
