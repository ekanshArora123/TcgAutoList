// Card conditions ordered best -> worst, as the dashboard DISPLAYS them.
//
// Card-show browse mode: MINT is excluded, the `DM` alias shows as DMG, and the
// in-between grades round UP to their better neighbour (LP-NM -> NM, ...), so none
// of those appear here. reporting_service does the same mapping in SQL
// (_CONDITION_DISPLAY / _DISPLAY_CONDITION_ORDER) — keep the two in step.
export const CONDITION_ORDER = ["NM", "LP", "MP", "HP", "DMG"];

export const conditionRank = (c: string): number => {
  const i = CONDITION_ORDER.indexOf((c || "").toUpperCase());
  return i === -1 ? CONDITION_ORDER.length : i; // unknown grades sort last
};
