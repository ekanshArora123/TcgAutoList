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

Warm browser (why one browser is kept alive across calls)
---------------------------------------------------------
Launching a browser and clearing Cloudflare's "Just a moment…" challenge costs
~10–40s. Doing that on *every* cert lookup is the dominant cost. So instead of
launch→scrape→close per call, we keep **one** browser/context alive and reuse it:
Cloudflare is cleared once, its ``cf_clearance`` cookie lives on the shared
context, and later navigations sail straight through in a few seconds.

The trap is **event-loop affinity**. Callers reach us through Flask's
``asyncio.run(add_graded_by_cert(...))`` — a *fresh* event loop per request.
Playwright's async browser/context/page objects are bound to the loop that
created them; you cannot create a browser under one ``asyncio.run`` loop and then
drive it from a *later* ``asyncio.run`` loop (it raises/hangs). So a naive
module-level cached browser dies on the 2nd request.

Fix (see :class:`_WarmBrowser`): a dedicated **background thread runs its own
persistent asyncio loop** for the whole process. The Playwright browser is
created once *on that loop* and never migrates. Each ``fetch_cert`` — itself an
``async def`` awaited from whatever request loop is live — dispatches the scrape
coroutine onto the persistent loop with :func:`asyncio.run_coroutine_threadsafe`
and awaits the cross-thread result via ``run_in_executor`` (so the request loop
is never blocked). A lock serializes scrapes (one at a time on the shared
context), the browser is probed for liveness each call and transparently
re-inited if it died, and an ``atexit`` hook tears it down cleanly.

Two-tier scrape (speed + consistency; see :meth:`_WarmBrowser._scrape`)
-----------------------------------------------------------------------
Cloudflare clears once (the cold navigation) and its ``cf_clearance`` cookie then
lives on the warm context. Subsequent lookups take the **fast path**: an in-page
*same-origin* ``fetch`` of the cert page (``_FETCH_JS``) that carries the cookie
WITHOUT re-triggering the top-level "Just a moment…" interstitial — the cert
identity comes back parsed in a fraction of a second, and if Cloudflare decides to
re-challenge the fetch it returns HTTP 403 *immediately* (no multi-second wedge).
Only then do we fall back to the **reliable path**: a real top-level navigation on
a fresh page, which solves the interstitial, reads the DOM, and re-warms the
clearance. This targets the (public) cert page only; the login-gated population
report is deliberately skipped for speed, so ``population`` /
``population_higher`` come back None.

For maximum warmth across *process* restarts too, point ``PSA_BROWSER_CHANNEL``
at real ``chrome`` and ``PSA_USER_DATA_DIR`` at a **dedicated** persistent
profile (the one ``--login`` creates) so the ``cf_clearance`` cookie and PSA
sign-in survive restarts. Playwright launches its *own* Chrome on that profile —
it cannot attach to an already-running Chrome, and the profile dir cannot be
shared with a running Chrome (profile lock), so do NOT use your everyday profile.
"""

from __future__ import annotations

import asyncio
import atexit
import os
import re
import threading
from typing import Any, Optional

from .formatters import parse_grade, parse_language

COMPANY = "PSA"

_SITE = "psacard.com"  # origin marker: fetch fast path only runs on a page here
_CERT_URL = "https://www.psacard.com/cert/{cert}/psa"
_POP_URL = "https://www.psacard.com/spec/psa/{spec}?p=population"

# A current, desktop-Chrome UA. Cloudflare fingerprints obviously-automated
# agents; a real browser-like UA (plus Playwright's real Chromium) gets through.
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# How long to wait for a page to clear Cloudflare + render its target element.
# A Cloudflare "wedge" *never* clears (verified: a wedged tab sat stuck for 90s),
# whereas a good clear lands in ~1-3s. Cap a single attempt at 15s: a genuine
# clear finishes well inside that, and a wedge fails fast instead of hanging the
# dashboard (owner's call — fail on any try longer than 15s).
_NAV_TIMEOUT_MS = 15_000

# Single attempt, no retry loop: when Cloudflare wedges (needs a foregrounded
# window it can't get), retrying just burns another 15s for the same result, so
# we surface the failure immediately. (A warm clearance makes the common case the
# ~0.4s fetch fast path anyway.)
_CF_RETRIES = 1
_CF_RETRY_DELAY_S = 1.5  # unused at _CF_RETRIES=1; kept for easy re-tuning

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

# FAST PATH: fetch a cert page *from within* an already-Cloudflare-cleared
# psacard.com page, and parse the returned HTML with DOMParser. Because this is a
# same-origin ``fetch`` (not a top-level navigation) it carries the context's
# cf_clearance cookie WITHOUT triggering Cloudflare's "Just a moment…" interstitial
# — so it returns the fully server-rendered cert HTML in a fraction of a second and,
# crucially, *fails fast* (an HTTP 403 in ~0.1s) if Cloudflare decides to
# re-challenge, instead of a real navigation's multi-second (sometimes 45s+) wedge.
# The extraction mirrors ``_CERT_JS`` exactly, just against the parsed document.
# On a challenge it returns ``{challenged: true}`` and the caller falls back to a
# real navigation (which solves the interstitial and re-warms the clearance).
_FETCH_JS = r"""async (cert) => {
  let r;
  try {
    r = await fetch('/cert/' + cert + '/psa', {credentials: 'include', redirect: 'follow'});
  } catch (e) {
    return { challenged: true };
  }
  const html = await r.text();
  if (r.status !== 200 || /just a moment|checking your browser|verify you are human/i.test(html))
    return { challenged: true };
  const doc = new DOMParser().parseFromString(html, 'text/html');
  const rows = {};
  doc.querySelectorAll('dl div').forEach(d => {
    const dt = d.querySelector('dt');
    const dd = d.querySelector('dd');
    if (dt && dd) rows[dt.textContent.trim()] = dd.textContent.trim();
  });
  const link = doc.querySelector('a[href*="/spec/psa/"]');
  const m = link ? (link.getAttribute('href') || '').match(/\/spec\/psa\/(\d+)/) : null;
  return { challenged: false, rows, spec_id: m ? m[1] : null };
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


def _cdp_url() -> str:
    """Endpoint of an already-running Chrome to ATTACH to over the DevTools
    protocol (from ``PSA_CDP_URL`` e.g. ``http://localhost:9222``, or
    ``PSA_CDP_PORT``). Empty string = launch our own browser instead."""
    url = os.environ.get("PSA_CDP_URL", "").strip()
    if url:
        return url
    port = os.environ.get("PSA_CDP_PORT", "").strip()
    return f"http://localhost:{port}" if port else ""


async def _new_context(pw: Any) -> tuple[Any, Any, bool]:
    """Build a browser context, returning ``(context, closable, is_cdp)``.

    Three modes, best-first:
      * **CDP attach** (``PSA_CDP_URL`` / ``PSA_CDP_PORT`` set) — connect to an
        already-running Chrome and reuse its live context. This is the most
        reliable + fastest option: your real, foregrounded, logged-in Chrome has
        already cleared Cloudflare, so there's *no challenge to solve*. Start it
        once with e.g. ``chrome.exe --remote-debugging-port=9222``. We only ever
        open OUR OWN tab and, on teardown, just DISCONNECT — we never close your
        Chrome or touch your existing tabs.
      * **Persistent profile** (``PSA_USER_DATA_DIR`` set) — launch our own Chrome
        on that profile (a one-time sign-in sticks).
      * **Ephemeral** (neither) — a throwaway launched browser.
    """
    cdp = _cdp_url()
    if cdp:
        browser = await pw.chromium.connect_over_cdp(cdp)
        # Reuse the running Chrome's live context (its cookies / cf_clearance).
        context = browser.contexts[0] if browser.contexts else await browser.new_context()
        return context, browser, True  # closing a CDP browser only disconnects it

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
    return context, closable, False


def _identity_from_rows(
    rows: Optional[dict[str, Any]], spec_id: Optional[str]
) -> Optional[dict[str, Any]]:
    """Normalize scraped Item-Information ``rows`` (+ SpecID) into the :func:`map_cert`
    dict. Shared by the fetch fast path and the navigation path so the mapping lives
    in one place. Population is intentionally left empty here — the fast path targets
    the (public) cert page only; population is login-gated and deliberately skipped
    for speed/consistency (``population`` / ``population_higher`` come back None).
    Returns None if the payload carries no cert number (cert not found)."""
    if not rows or not rows.get("Cert Number"):
        return None
    scraped = {field: rows.get(label) for label, field in _LABELS.items()}
    scraped["spec_id"] = spec_id
    scraped["population_rows"] = []
    return map_cert(scraped)


async def _nav_scrape_cert(page: Any, cert_number: str) -> Optional[dict[str, Any]]:
    """Reliable path: a real top-level navigation that *solves* Cloudflare's
    interstitial, then reads the identity straight from the live DOM. Returns the
    :func:`map_cert` dict, None if the cert doesn't exist, or raises RuntimeError if
    Cloudflare never cleared (see :func:`_render`)."""
    scraped = await _scrape_identity(page, cert_number)
    if scraped is None:
        return None
    return _identity_from_rows(
        {label: scraped.get(field) for label, field in _LABELS.items()},
        scraped.get("spec_id"),
    )


class _WarmBrowser:
    """A persistent Playwright browser kept alive on its own event loop.

    Loop affinity (see the module docstring): Playwright objects are bound to the
    loop that created them, but our callers hand us a fresh ``asyncio.run`` loop
    per request. So we own a dedicated background thread running a single
    ``run_forever`` loop; the browser is created *on that loop* and every scrape
    is dispatched onto it via :func:`asyncio.run_coroutine_threadsafe`. That way
    the browser never migrates loops, no matter which request loop calls in.

    Everything prefixed with the loop (``_ensure_browser``, ``_scrape``,
    ``_teardown_inner``) MUST run on the persistent loop only. ``run_scrape`` and
    ``_ensure_loop`` are the thread-safe entry points callers use.
    """

    def __init__(self) -> None:
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._start_lock = threading.Lock()  # guards loop/thread creation
        # These live on (and are only touched from) the persistent loop:
        self._pw: Any = None            # the started async_playwright object
        self._context: Any = None       # BrowserContext (warm; holds cf_clearance)
        self._closable: Any = None      # what to .close() (context, or the CDP browser = disconnect)
        self._is_cdp: bool = False       # attached to a running Chrome (don't touch its tabs)
        self._page: Any = None          # the single long-lived, reused scrape tab
        self._lock: Optional[asyncio.Lock] = None  # serializes scrapes on the loop

    # --- thread-safe entry points (called from any request/executor thread) ---

    def _ensure_loop(self) -> None:
        """Lazily start the background thread + its persistent event loop."""
        with self._start_lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._loop = asyncio.new_event_loop()
            self._thread = threading.Thread(
                target=self._run_loop, name="psa-warm-browser", daemon=True
            )
            self._thread.start()

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def run_scrape(self, cert_number: str) -> Optional[dict[str, Any]]:
        """Blocking: run one scrape on the persistent loop and return its result.

        Called from a thread-pool worker (via ``run_in_executor``) so blocking on
        the cross-loop future here never stalls the caller's request loop."""
        self._ensure_loop()
        assert self._loop is not None
        future = asyncio.run_coroutine_threadsafe(self._scrape(cert_number), self._loop)
        return future.result()

    def shutdown(self) -> None:
        """atexit hook: close the warm browser and stop the loop, best-effort."""
        if self._loop is None or not (self._thread and self._thread.is_alive()):
            return
        try:
            asyncio.run_coroutine_threadsafe(
                self._teardown_inner(), self._loop
            ).result(timeout=10)
        except Exception:
            pass
        try:
            self._loop.call_soon_threadsafe(self._loop.stop)
        except Exception:
            pass

    # --- persistent-loop-only coroutines --------------------------------------

    async def _open_fresh_page(self) -> Any:
        """Open and return a *fresh*, foregrounded page on the warm context,
        transparently (re)launching the browser if it died. Runs on the persistent
        loop only. Used for the navigation (Cloudflare-solving) path and for
        discarding a wedged tab on retry.

        Two hard-won rules encoded here:

        * **A fresh page for a real navigation, not a reused tab.** The context
          (and its ``cf_clearance`` cookie) stays warm across calls, but a *top-level
          navigation* re-uses a clean page: re-navigating one tab carries stale
          Cloudflare JS state that intermittently wedges on the "Just a moment…"
          interstitial, whereas a clean page on the same warm context clears in a
          couple of seconds (verified).
        * **Open the new page before closing the old.** A persistent context's
          browser exits when its *last* page closes, so we must never drop to zero
          pages. We swap in the new tab, then close the previous one.

        ``bring_to_front`` keeps the tab the *active* one — Cloudflare's Turnstile
        only runs on a visible, foregrounded tab; a backgrounded tab is treated like
        headless and wedges."""
        # Re-init at most once if the context is found dead mid-open.
        for _ in (False, True):
            if self._context is None:
                from playwright.async_api import async_playwright

                self._pw = await async_playwright().start()
                try:
                    self._context, self._closable, self._is_cdp = await _new_context(self._pw)
                    # Adopt the context's initial blank tab as the "previous" page,
                    # so the first swap below closes it instead of leaking it. In CDP
                    # mode we must NOT adopt — those are the user's real tabs — so we
                    # start with no "previous" and only ever manage tabs we open.
                    self._page = (
                        None if self._is_cdp
                        else (self._context.pages[0] if self._context.pages else None)
                    )
                except Exception as err:
                    await self._teardown_inner()
                    raise RuntimeError(f"Failed to launch a warm browser for PSA scrape: {err}") from err

            try:
                page = await self._context.new_page()
            except Exception:
                await self._teardown_inner()  # context/browser dead → loop re-inits
                continue

            try:
                await page.bring_to_front()
            except Exception:
                pass  # best-effort; not fatal if the tab can't be raised

            old, self._page = self._page, page
            if old is not None:
                try:
                    if not old.is_closed():
                        await old.close()
                except Exception:
                    pass
            return page

        raise RuntimeError("Failed to open a page for PSA scrape (browser kept dying)")

    async def _live_page(self) -> Any:
        """Reuse the current warm page if it's alive — so the fetch fast path can run
        against its already-Cloudflare-cleared psacard.com origin — else open a fresh
        one. Runs on the persistent loop only."""
        if self._context is not None and self._page is not None and not self._page.is_closed():
            try:
                await self._page.bring_to_front()
                return self._page
            except Exception:
                await self._teardown_inner()  # browser died → re-init
        return await self._open_fresh_page()

    async def _scrape(self, cert_number: str) -> Optional[dict[str, Any]]:
        # Lazily bind the lock to the persistent loop (safe: this runs only on
        # that single-threaded loop, so the check-then-set can't race).
        if self._lock is None:
            self._lock = asyncio.Lock()
        async with self._lock:  # one scrape at a time on the shared context
            # Two-tier strategy, tuned for speed + consistency (cert page only):
            #   1. FAST PATH — if the warm page is already on psacard.com (Cloudflare
            #      cleared), pull the cert with an in-page same-origin fetch (~0.4s,
            #      no interstitial). If Cloudflare re-challenges the fetch it fails
            #      fast (~0.1s), so we lose almost nothing before falling back.
            #   2. RELIABLE PATH — a real top-level navigation on a fresh page solves
            #      the interstitial and reads the DOM (also re-warms cf_clearance for
            #      later fetches). A wedge here raises RuntimeError; we retry on a
            #      fresh page. A not-found cert returns None (no retry).
            last_err: Optional[RuntimeError] = None
            for attempt in range(_CF_RETRIES):
                if attempt:
                    await asyncio.sleep(_CF_RETRY_DELAY_S)  # let CF settle
                try:
                    page = await self._live_page()
                    if _SITE in (page.url or ""):
                        res = await page.evaluate(_FETCH_JS, cert_number)
                        if not res.get("challenged"):
                            return _identity_from_rows(res.get("rows"), res.get("spec_id"))
                        # Stale clearance for fetch → re-solve with a real navigation.
                        page = await self._open_fresh_page()
                    return await _nav_scrape_cert(page, cert_number)
                except RuntimeError as err:
                    last_err = err
                    await self._open_fresh_page()  # discard wedged tab; retry fresh
            raise last_err  # type: ignore[misc]  # set on every failing iteration

    async def _teardown_inner(self) -> None:
        """Close the browser + stop Playwright, swallowing errors. Runs on the
        persistent loop only. Leaves the instance ready to re-init on next use.

        For a CDP-attached Chrome, ``.close()`` only DISCONNECTS Playwright (the
        user's Chrome keeps running); so first we close just OUR tab, then
        disconnect — never killing their browser or their tabs."""
        try:
            if self._is_cdp and self._page is not None and not self._page.is_closed():
                await self._page.close()  # close only our own tab
        except Exception:
            pass
        try:
            if self._closable is not None:
                await self._closable.close()  # CDP: disconnect; else: close browser/context
        except Exception:
            pass
        try:
            if self._pw is not None:
                await self._pw.stop()
        except Exception:
            pass
        self._page = self._context = self._closable = self._pw = None
        self._is_cdp = False


# Process-wide warm browser, created on first use and torn down at exit.
_WARM: Optional[_WarmBrowser] = None
_WARM_INIT_LOCK = threading.Lock()


def _warm_browser() -> _WarmBrowser:
    global _WARM
    if _WARM is None:
        with _WARM_INIT_LOCK:
            if _WARM is None:
                warm = _WarmBrowser()
                atexit.register(warm.shutdown)
                _WARM = warm
    return _WARM


async def fetch_cert(cert_number: str, **_ignored: Any) -> Optional[dict[str, Any]]:
    """Fetch + normalize a PSA cert by scraping the public PSA website.

    Positional-callable as ``await fetch_cert(cert_id)`` (the collection service
    calls it that way). Extra keyword args are accepted and ignored so legacy
    callers (which used to pass token/client/limiter for the old API path) don't
    break.

    **Warm reuse:** the first call lazily launches one browser and clears
    Cloudflare (~10–40s); every later call reuses that same warm browser/context
    (the ``cf_clearance`` cookie skips the challenge), so it's just navigate+parse
    — a few seconds. The browser lives on a dedicated background loop to survive
    the per-request ``asyncio.run`` loops Flask hands us; see :class:`_WarmBrowser`
    and the module docstring for the loop-affinity rationale.

    Only the public cert (identity) page is scraped — fast and consistent. The
    login-gated population report is intentionally skipped, so ``population`` /
    ``population_higher`` always come back None (the pop pure-math in
    :func:`map_cert` / :func:`_summarize_population` stays for the fixtures/tests
    and any future authed path).

    Returns the normalized dict from :func:`map_cert`, or None if the cert
    doesn't exist. Raises RuntimeError on real failures (Playwright missing,
    browser launch failure, Cloudflare never solved on the identity page).
    """
    cert_number = str(cert_number).strip()

    try:
        import playwright.async_api  # noqa: F401  (presence check)
    except ImportError as err:  # pragma: no cover - environment/setup issue
        raise RuntimeError(
            "Playwright is required to scrape PSA. Install it with "
            "`pip install playwright` and `playwright install chromium`."
        ) from err

    # Dispatch the scrape onto the warm browser's persistent loop. We block a
    # thread-pool worker (not this request loop) on the cross-loop result, so
    # concurrent request loops stay responsive and Playwright never sees a
    # foreign loop. Access is serialized inside ``_scrape`` (one page, one nav).
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _warm_browser().run_scrape, cert_number)


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
