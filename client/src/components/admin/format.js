// Shared number formatting for the ops center. Compact for tight table/strip cells,
// money for currency. Deterministic, no locale surprises on the big figures.
export const compact = (n) => {
  if (n >= 1e6) return `${(n / 1e6).toFixed(n >= 1e7 ? 0 : 1)}M`;
  if (n >= 1e3) return `${(n / 1e3).toFixed(n >= 1e4 ? 0 : 1)}k`;
  return `${Math.round(n)}`;
};

export const money = (n) => `$${compact(n)}`;

export const initials = (name = "") =>
  name.trim().split(/\s+/).slice(0, 2).map((w) => w[0]).join("").toUpperCase();
