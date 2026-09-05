// Card conditions ordered best -> worst, as the dashboard DISPLAYS them.
//
// Card-show browse mode: MINT is excluded from the customer-facing views and the
// `DM` alias is shown as DMG, so neither appears here. The backend does the same
// mapping in reporting_service (_CONDITION_DISPLAY / _DISPLAY_CONDITION_ORDER) —
// keep the two in step.
export const CONDITION_ORDER = ["NM", "LP-NM", "LP", "MP-LP", "MP", "HP-MP", "HP", "DM-HP", "DMG"];

export const conditionRank = (c: string): number => {
  const i = CONDITION_ORDER.indexOf((c || "").toUpperCase());
  return i === -1 ? CONDITION_ORDER.length : i; // unknown grades sort last
};
