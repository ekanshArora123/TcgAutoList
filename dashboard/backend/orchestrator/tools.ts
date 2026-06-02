/**
 * Tool Bridge — Tool definitions for Claude API (Tier 2/3).
 *
 * PSEUDOCODE — Not yet implemented.
 *
 * Bridges MCP server tools into Claude API tool format.
 * Tier 2 gets a scoped subset, Tier 3 gets everything.
 */

/**
 * PSEUDOCODE: Tier 2 tools — pricing and research only.
 *
 * The scoped tool set for Tier 2 cards. The LLM can:
 * - Fetch more active listings for different conditions
 * - Fetch sold listings for comparison
 * - Fetch card info for context
 * - Compare prices across conditions
 *
 * The LLM CANNOT:
 * - Modify cards, inventory, or prices
 * - Access eBay tools
 * - Mark cards as listed/sold/skipped
 */
export function getTier2Tools(): unknown[] {
  // PSEUDOCODE:
  // return [
  //   {
  //     name: 'fetch_active_listings',
  //     description: 'Fetch active TCGplayer listings for a card.',
  //     input_schema: { ... tcgplayerId, condition?, finish? }
  //   },
  //   {
  //     name: 'fetch_sold_listings',
  //     description: 'Fetch recent sold prices from TCGplayer.',
  //     input_schema: { ... tcgplayerId, condition?, finish? }
  //   },
  //   {
  //     name: 'get_card_info',
  //     description: 'Get card metadata from TCGplayer.',
  //     input_schema: { ... tcgplayerId }
  //   },
  //   {
  //     name: 'compare_conditions',
  //     description: 'Compare prices across all conditions for a card.',
  //     input_schema: { ... cardId }
  //   },
  //   {
  //     name: 'set_price_decision',
  //     description: 'Submit final pricing decision.',
  //     input_schema: { price: number, action: 'list' | 'skip' | 'hold' | 'escalate', reasoning: string }
  //   },
  // ];
  return [];
}

/**
 * PSEUDOCODE: Tier 3 tools — full access.
 *
 * Everything from Tier 2 plus:
 * - Full card-server tool access (search, update, etc.)
 * - eBay research tools (search solds, active listings)
 * - Ability to request manual review via Telegram
 */
export function getTier3Tools(): unknown[] {
  // PSEUDOCODE:
  // return [
  //   ...getTier2Tools(),
  //   {
  //     name: 'search_cards',
  //     description: 'Search for similar cards in the collection.',
  //     input_schema: { ... query, set_name?, rarity? }
  //   },
  //   {
  //     name: 'search_ebay_solds',
  //     description: 'Search eBay completed listings for comparable sales.',
  //     input_schema: { ... query, condition?, min_price?, max_price? }
  //   },
  //   {
  //     name: 'request_manual_review',
  //     description: 'Flag this card for human review with a reason.',
  //     input_schema: { reason: string }
  //   },
  //   {
  //     name: 'ask_user',
  //     description: 'Ask the user a question via Telegram.',
  //     input_schema: { question: string }
  //   },
  // ];
  return [];
}

/**
 * PSEUDOCODE: Execute a tool call from the LLM.
 *
 * Routes the tool call to the appropriate MCP server or handler.
 */
export async function executeTool(
  _toolName: string,
  _toolInput: unknown,
): Promise<unknown> {
  // PSEUDOCODE:
  //
  // switch (toolName) {
  //   case 'fetch_active_listings':
  //     return pricingService.fetchActiveListingsData(input.tcgplayerId, input.condition, input.finish);
  //   case 'fetch_sold_listings':
  //     return pricingService.fetchSoldListingsData(input.tcgplayerId, input.condition, input.finish);
  //   case 'search_ebay_solds':
  //     // Route to ebay-mcp server via MCP client
  //     return mcpClient.callTool('ebay-mcp', 'searchCompletedItems', input);
  //   case 'ask_user':
  //     // Send question to Telegram, wait for response
  //     await bot.sendMessage(input.question);
  //     return new Promise(resolve => bot.once('event', e => {
  //       if (e.type === 'text') resolve(e.message);
  //     }));
  //   default:
  //     throw new Error(`Unknown tool: ${toolName}`);
  // }

  return { error: 'Tool execution not implemented' };
}
