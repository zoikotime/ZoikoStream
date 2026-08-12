// client/src/components/moderation/panelTokens.js
// The design system for the live-event utility panel — the surfaces, rows, cards and text
// ladder shared by all six tabs (People, Chat, Q&A, Polls, Stats, Feed).
//
// Used by BOTH consoles: the host console composes these tabs as a right-hand rail
// (components/host/HostPanel) and the moderator console as side-by-side columns
// (pages/moderator/Dashboard). Tokens live here rather than in either page, so changing a
// border or a count badge changes it in exactly one place instead of six.
//
// Separate from Panel.jsx because react-refresh requires a module to export only components
// OR only constants — mixing them silently breaks hot reload for every importer.
//
// COLOUR NOTE: index.css remaps `emerald-*` to violet, so `bg-emerald-600` renders PURPLE.
// Everything here names the colour it actually wants — `violet-*` for the ZoikoStream
// accent, `green-*` for genuinely-green positive/live states, `amber-*` for warnings,
// `rose-*` for destructive only.
//
// Spacing is an 8px scale (px-2/2.5/3, py-1.5/2/2.5, gap-2), radii are two-tier
// (rounded-lg 8px for rows and controls, rounded-xl 12px for panels), and elevation comes
// from borders rather than shadows so a dense control panel stays flat and legible.
import { cx, focusRing } from "../../ui/tokens";

export const PANEL = {
  // A panel surface. One hairline border plus a 1px ambient shadow — enough to separate it
  // from the page without the floating-card look that makes a control panel feel loose.
  surface:
    "rounded-xl border border-slate-200 bg-white shadow-[0_1px_2px_rgba(16,24,40,0.04)] dark:border-slate-800 dark:bg-slate-900",
  divider: "border-slate-100 dark:border-slate-800",
  divideY: "divide-slate-100 dark:divide-slate-800",

  // Count badge beside a panel title. Violet, so the number reads as part of the accent
  // system rather than as another grey chip.
  count:
    "shrink-0 rounded-full bg-violet-50 px-2 py-0.5 text-[11px] font-semibold tabular-nums text-violet-700 dark:bg-violet-500/15 dark:text-violet-300",

  // Small uppercase label above a group of values.
  eyebrow: "text-[11px] font-semibold uppercase tracking-wide text-slate-400 dark:text-slate-500",

  // A list row: transparent at rest so the list reads as one surface, violet on hover,
  // violet + a left accent bar when selected. `rowAccent` needs the row to be `relative`.
  row: "rounded-lg border border-transparent",
  rowHover:
    "hover:border-violet-100 hover:bg-violet-50/70 dark:hover:border-violet-500/20 dark:hover:bg-violet-500/[0.07]",
  rowActive: "border-violet-200 bg-violet-50 dark:border-violet-500/30 dark:bg-violet-500/10",
  rowAccent:
    "before:absolute before:inset-y-1 before:left-0 before:w-0.5 before:rounded-full before:bg-violet-600 dark:before:bg-violet-400",

  // A bordered card inside a scrolling body (a poll, a question, a message).
  card: "rounded-lg border border-slate-200 dark:border-slate-800",
  cardHover: "hover:border-slate-300 dark:hover:border-slate-700",
  // A recessed tile for a metric or a distribution row.
  inset: "rounded-lg border border-slate-200 bg-slate-50/70 dark:border-slate-800 dark:bg-slate-800/40",

  // Text ladder.
  heading: "text-slate-900 dark:text-white",
  body: "text-slate-700 dark:text-slate-200",
  muted: "text-slate-500 dark:text-slate-400",
  faint: "text-slate-400 dark:text-slate-500",

  t150: "transition-colors duration-150 ease-out motion-reduce:transition-none",
};

// The one premium control per panel header (e.g. "+ New").
export const PANEL_PRIMARY = cx(
  "inline-flex h-7 shrink-0 items-center gap-1 rounded-lg bg-violet-600 px-2.5 text-[11px] font-semibold text-white",
  "hover:bg-violet-500 disabled:cursor-not-allowed disabled:opacity-45",
  PANEL.t150,
  focusRing
);

// A compact secondary/outline control sitting beside the primary one.
export const PANEL_SECONDARY = cx(
  "inline-flex h-7 shrink-0 items-center gap-1 rounded-lg border border-slate-200 px-2.5 text-[11px] font-semibold text-slate-600",
  "hover:border-slate-300 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-45",
  "dark:border-slate-700 dark:text-slate-300 dark:hover:border-slate-600 dark:hover:bg-slate-800",
  PANEL.t150,
  focusRing
);

// Semantic tones for a status/alert strip. Healthy is literal green, never the remapped
// emerald, so "ok" cannot read as brand purple.
export const ALERT = {
  ok: "border-green-300 bg-green-50 text-green-800 dark:border-green-500/30 dark:bg-green-500/10 dark:text-green-300",
  warn: "border-amber-300 bg-amber-50 text-amber-900 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-300",
  down: "border-rose-300 bg-rose-50 text-rose-900 dark:border-rose-500/30 dark:bg-rose-500/10 dark:text-rose-300",
};
export const ALERT_DOT = {
  ok: "bg-green-500",
  warn: "bg-amber-500",
  down: "bg-rose-500",
};
