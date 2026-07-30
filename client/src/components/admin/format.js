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

// Relative time for real timestamps ("4m ago", "3h ago", "2d ago"). Falls back to a
// plain date once it's far enough back that "Nd ago" stops being useful.
export const timeAgo = (iso) => {
  if (!iso) return "—";
  const diffMs = Date.now() - new Date(iso).getTime();
  const min = Math.floor(diffMs / 60000);
  if (min < 1) return "just now";
  if (min < 60) return `${min}m ago`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr}h ago`;
  const day = Math.floor(hr / 24);
  if (day < 30) return `${day}d ago`;
  return new Date(iso).toLocaleDateString();
};

// % change between the last two points of a [{label,value}] series. Null when there
// isn't enough history yet — callers should render that as "—", never a fake number.
export const seriesDelta = (series) => {
  if (!Array.isArray(series) || series.length < 2) return null;
  const prev = series[series.length - 2].value;
  const last = series[series.length - 1].value;
  if (!prev) return null;
  const pct = ((last - prev) / prev) * 100;
  return { pct: Math.round(Math.abs(pct) * 10) / 10, up: pct >= 0 };
};
