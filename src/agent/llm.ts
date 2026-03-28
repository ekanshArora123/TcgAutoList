/**
 * LLM Client — Claude API integration for Tier 2/3 cards.
 *
 * PSEUDOCODE — Not yet implemented. This file outlines the intended
 * architecture for spawning per-card LLM conversations.
 *
 * ARCHITECTURE:
 * - Each card gets a fresh Claude conversation (no history accumulation).
 * - Tier 2: scoped context (pricing data only), scoped tools (pricing/research).
 * - Tier 3: full context (all card data), full tools (all card-server tools).
 * - The orchestrator relays Telegram free-text into the active conversation.
 * - When the card is done, the conversation is discarded.
 *
 * ESTIMATED TOKEN USAGE:
 * - Tier 2: ~5,000 tokens per card
 * - Tier 3: ~10,000-15,000 tokens per card
 */

import type { InventoryDetail, Price } from '../types.js';
// import Anthropic from '@anthropic-ai/sdk';  // Will use when implemented

/** Active LLM conversation state. */
interface ConversationState {
  inventoryId: number;
  tier: 2 | 3;
  messages: Array<{ role: 'user' | 'assistant'; content: string }>;
  /** Tool calls the LLM has made this turn. */
  pendingToolCalls: unknown[];
}

/**
 * PSEUDOCODE: Create a Tier 2 conversation.
 *
 * Tier 2 gets:
 * - The pricing data already gathered (active listings, sold listings, algorithm reasoning)
 * - A scoped set of tools (fetch more pricing data, but NOT modify cards/inventory)
 * - A system prompt focused on pricing decisions
 *
 * The LLM decides:
 * - Final price (may adjust from algorithm suggestion)
 * - Whether to list at all (or skip/hold)
 * - Whether to escalate to Tier 3 (needs more data)
 */
export async function createTier2Conversation(
  detail: InventoryDetail,
  price: Price,
  _algorithmReasoning: string,
): Promise<void> {
  // PSEUDOCODE:
  //
  // const client = new Anthropic({ apiKey: process.env.ANTHROPIC_API_KEY });
  //
  // const systemPrompt = buildTier2SystemPrompt(detail, price, algorithmReasoning);
  // const tools = getTier2Tools();  // pricing/research tools only
  //
  // const response = await client.messages.create({
  //   model: 'claude-sonnet-4-20250514',  // Cost-effective for Tier 2
  //   max_tokens: 2048,
  //   system: systemPrompt,
  //   messages: [{ role: 'user', content: 'Review this card pricing and decide.' }],
  //   tools,
  // });
  //
  // // Process tool calls, relay to MCP servers
  // // Loop until the LLM returns a final decision (not a tool call)
  // // Return: { finalPrice, action: 'list' | 'skip' | 'hold' | 'escalate' }

  console.log(`[LLM STUB] Tier 2 conversation for ${detail.card_name} — not implemented`);
}

/**
 * PSEUDOCODE: Create a Tier 3 conversation.
 *
 * Tier 3 gets:
 * - Full card data, all pricing data, inventory details
 * - Full tool set (all card-server tools + eBay research)
 * - A comprehensive system prompt with decision authority
 *
 * The LLM can:
 * - Research comparable cards on eBay and TCGplayer
 * - Analyze price history and market trends
 * - Make a judgment call on pricing, listing, or holding
 * - Recommend the card for manual human review
 * - Ask the user questions via Telegram relay
 */
export async function createTier3Conversation(
  detail: InventoryDetail,
  price: Price | null,
): Promise<void> {
  // PSEUDOCODE:
  //
  // const client = new Anthropic({ apiKey: process.env.ANTHROPIC_API_KEY });
  //
  // const systemPrompt = buildTier3SystemPrompt(detail, price);
  // const tools = getTier3Tools();  // all card-server + ebay tools
  //
  // // Tier 3 may use a more capable model for complex reasoning
  // const response = await client.messages.create({
  //   model: 'claude-sonnet-4-20250514',  // or opus for very rare cards
  //   max_tokens: 4096,
  //   system: systemPrompt,
  //   messages: [{ role: 'user', content: 'Analyze this card and decide on listing.' }],
  //   tools,
  // });
  //
  // // Agentic loop:
  // // 1. Process tool calls (fetch eBay solds, research comps, etc.)
  // // 2. Send results back to Claude
  // // 3. If Claude asks a question, relay to user via Telegram
  // // 4. Wait for user response, inject into conversation
  // // 5. Repeat until Claude returns a final decision
  // //
  // // Return: { finalPrice, action, reasoning }

  console.log(`[LLM STUB] Tier 3 conversation for ${detail.card_name} — not implemented`);
}

/**
 * PSEUDOCODE: Relay user free-text from Telegram into the active LLM conversation.
 *
 * When the user sends a text message during a Tier 2/3 card,
 * this injects it as a user turn in the active conversation.
 */
export async function relayUserMessage(
  _conversation: ConversationState,
  _message: string,
): Promise<string> {
  // PSEUDOCODE:
  //
  // conversation.messages.push({ role: 'user', content: message });
  //
  // const response = await client.messages.create({
  //   model: conversation.tier === 2 ? 'claude-sonnet-4-20250514' : 'claude-sonnet-4-20250514',
  //   max_tokens: 2048,
  //   messages: conversation.messages,
  //   tools: conversation.tier === 2 ? getTier2Tools() : getTier3Tools(),
  // });
  //
  // // Process response, execute any tool calls, return assistant text
  // return response.content[0].text;

  return '[LLM relay not implemented]';
}
