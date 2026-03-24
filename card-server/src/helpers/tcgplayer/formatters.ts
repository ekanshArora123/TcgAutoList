/**
 * Formatters for converting internal DB values to TCGplayer API format.
 *
 * Internal DB uses short codes (NM, LP, Holo, Reverse-Holo).
 * TCGplayer API uses full strings (Near Mint, Holofoil, Reverse Holofoil).
 *
 * Ported from QuickCollectionValueFinder.py reFormatCondition / reFormatFinish.
 */

// ─── Condition Formatting ────────────────────────────────────

/**
 * Convert internal condition code(s) to TCGplayer API condition strings.
 *
 * In-between conditions (LP-NM, HP-MP, etc.) return BOTH neighbors
 * since TCGplayer doesn't support in-between grades.
 * The API accepts an array of conditions and returns listings for all of them.
 *
 * Mapping:
 *   MINT, NM     → ["Near Mint"]
 *   LP-NM        → ["Lightly Played", "Near Mint"]
 *   LP           → ["Lightly Played"]
 *   MP-LP        → ["Moderately Played", "Lightly Played"]
 *   MP           → ["Moderately Played"]
 *   HP-MP        → ["Heavily Played", "Moderately Played"]
 *   HP           → ["Heavily Played"]
 *   DM, DMG      → ["Damaged"]
 *   DM-HP        → ["Damaged", "Heavily Played"]
 */
export function formatConditionForApi(condition: string): string[] {
  const map: Record<string, string[]> = {
    'MINT':  ['Near Mint'],
    'NM':    ['Near Mint'],
    'LP-NM': ['Lightly Played', 'Near Mint'],
    'LP':    ['Lightly Played'],
    'MP-LP': ['Moderately Played', 'Lightly Played'],
    'MP':    ['Moderately Played'],
    'HP-MP': ['Heavily Played', 'Moderately Played'],
    'HP':    ['Heavily Played'],
    'DM':    ['Damaged'],
    'DMG':   ['Damaged'],
    'DM-HP': ['Damaged', 'Heavily Played'],
  };
  return map[condition] ?? ['Damaged'];
}

// ─── Finish Formatting ───────────────────────────────────────

/**
 * WOTC-era sets that use "Unlimited" / "Unlimited Holofoil" finish naming
 * instead of "Normal" / "Holofoil" on TCGplayer.
 *
 * These sets had print runs distinguished by edition (1st Edition vs Unlimited),
 * so TCGplayer uses different finish names for them.
 */
const UNLIMITED_FINISH_SETS = new Set([
  'Base Set (Shadowless)',
  'Jungle',
  'Fossil',
  'Gym Challenge',
  'Gym Heroes',
  'Team Rocket',
  'Neo Genesis',
  'Neo Discovery',
  'Neo Revelation',
  'Neo Destiny',
]);

/**
 * Convert internal finish + specialty + set name to TCGplayer API "printing" value.
 *
 * TCGplayer's "printing" field is a combination of edition + finish:
 *
 *   1st Edition + Holo    → "1st Edition Holofoil"
 *   1st Edition + Regular → "1st Edition"
 *   WOTC set + Holo       → "Unlimited Holofoil"
 *   WOTC set + Regular    → "Unlimited"
 *   Reverse-Holo          → "Reverse Holofoil"
 *   Holo                  → "Holofoil"
 *   Regular               → "Normal"
 *
 * @param finish       - Internal finish code: "Regular", "Holo", "Reverse-Holo"
 * @param specialtyOne - Internal specialty: "First Edition", "None", etc.
 * @param setName      - Card's set name, used to detect WOTC-era Unlimited naming.
 */
export function formatFinishForApi(
  finish: string,
  specialtyOne: string,
  setName: string,
): string {
  // 1st Edition overrides
  if (specialtyOne === 'First Edition') {
    return finish === 'Holo' ? '1st Edition Holofoil' : '1st Edition';
  }

  // WOTC-era Unlimited sets
  if (UNLIMITED_FINISH_SETS.has(setName)) {
    return finish === 'Holo' ? 'Unlimited Holofoil' : 'Unlimited';
  }

  // Standard modern finishes
  if (finish === 'Reverse-Holo') return 'Reverse Holofoil';
  if (finish === 'Holo') return 'Holofoil';
  return 'Normal';
}

/**
 * Reverse mapping: convert TCGplayer API finish back to internal format.
 * Useful when parsing API responses.
 */
export function parseFinishFromApi(printing: string): { finish: string; specialtyOne: string } {
  if (printing === '1st Edition Holofoil') return { finish: 'Holo', specialtyOne: 'First Edition' };
  if (printing === '1st Edition') return { finish: 'Regular', specialtyOne: 'First Edition' };
  if (printing === 'Unlimited Holofoil') return { finish: 'Holo', specialtyOne: 'None' };
  if (printing === 'Unlimited') return { finish: 'Regular', specialtyOne: 'None' };
  if (printing === 'Reverse Holofoil') return { finish: 'Reverse-Holo', specialtyOne: 'None' };
  if (printing === 'Holofoil') return { finish: 'Holo', specialtyOne: 'None' };
  return { finish: 'Regular', specialtyOne: 'None' };
}

/**
 * Reverse mapping: convert TCGplayer API condition back to internal format.
 */
export function parseConditionFromApi(apiCondition: string): string {
  const map: Record<string, string> = {
    'Near Mint': 'NM',
    'Lightly Played': 'LP',
    'Moderately Played': 'MP',
    'Heavily Played': 'HP',
    'Damaged': 'DMG',
  };
  return map[apiCondition] ?? 'DMG';
}
