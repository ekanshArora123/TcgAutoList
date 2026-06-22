// Card conditions ordered best -> worst (mirrors PRIMARY_CONDITIONS + the
// in-between grades on the backend). Used to sort grades consistently in the UI.
export const CONDITION_ORDER = ["MINT", "NM", "LP-NM", "LP", "MP-LP", "MP", "HP-MP", "HP", "DMG"];

export const conditionRank = (c: string): number => {
  const i = CONDITION_ORDER.indexOf((c || "").toUpperCase());
  return i === -1 ? CONDITION_ORDER.length : i; // unknown grades sort last
};
