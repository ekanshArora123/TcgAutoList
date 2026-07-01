# Seller Listing Pipeline — local notes

Capability-specific detail for the seller listing pipeline (Telegram intake +
orchestrator + eBay). This is a **local doc** — narrower and more churn-prone
than the root `CLAUDE.md`; trust the code first if they disagree.

## Workflow

`/next` (via Telegram) → pick next unlisted card → fetch price → route by tier →
request photo → build listing → post to eBay → mark done → wait for the next
command. The orchestrator is a coded state machine; durable state (including
per-card `status`, e.g. `photo_requested`) lives in SQLite.

## Pipeline-specific decisions

- **Manual trigger only.** `/next` starts each card; no auto-advance.
- **One photo at a time.** Request a photo, wait for it, then continue.
- **Context resets per card.** Each card that escalates to an LLM (Tier 2/3)
  gets a fresh conversation — no history accumulation, so token cost stays
  predictable.

(Platform-wide decisions — all-state-in-SQLite, idempotency, service ownership,
coded-path-vs-MCP — are in the root `CLAUDE.md`.)

## Tiered escalation model

LLM involvement scales with pricing difficulty. Most cards run the coded,
no-LLM path; harder cards escalate. Thresholds are configurable in
`dashboard/backend/orchestrator/types.py` (`DEFAULT_TIER_CONFIG`); pricing
constants are in `services/card_server/helpers/pricing/config.py`.

| Tier | Criteria | LLM? | Tokens | % of Cards |
|------|----------|------|--------|------------|
| **1** | Confidence >= 80%, price < $50, no specialties | No | 0 | ~80% |
| **2** | Confidence 40-79%, OR $50-200, OR manual flag | Scoped context + pricing tools | ~5K | ~15% |
| **3** | Confidence < 40%, OR > $200, OR graded/errors, OR no data | Full context + all tools | ~12K | ~5% |

Rough cost for 10,000 cards: **~9.75M tokens**. Tier 2/3 LLM pricing itself is
still pseudocode (`llm.py`, `tools.py`, `system_prompt.py`); see the root
**Status & Roadmap**.

## Related future work

- **eBay posting** — real Sell API (currently a stub returning a fake ID).
- **Price override** — apply a human's numeric reply mid-flow (currently detected
  but not applied).
- **In-between condition choice** — when a card is e.g. LP-NM, prompt to list as
  LP or NM.
