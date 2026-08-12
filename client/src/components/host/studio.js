// client/src/components/host/studio.js
// Visual primitives for the Producer Console — pages/host/Dashboard.jsx and the
// components/host/* it composes. Page-scoped ON PURPOSE: ui/tokens.js is shared by the
// marketing site, the org dashboard and the admin console, so the studio's denser
// operator styling lives here instead of widening a global token that four other areas
// would inherit.
//
// ── COLOUR NOTE — read this before adding a class ────────────────────────────────────
// index.css remaps the `emerald-*` scale to violet and `teal-*` to pink (the brand
// palette), so `bg-emerald-600` renders PURPLE, not green. Every class below therefore
// names the colour it actually wants:
//
//   violet-*   brand accent — active state, primary action, focus ring
//   green-*    healthy / connected / sending      (literal green, never emerald-*)
//   rose-*     on-air / destructive
//   amber-*    paused / degraded / needs attention
//   slate-*    surfaces and text
//
// ── SYSTEM ───────────────────────────────────────────────────────────────────────────
// Spacing follows an 8px grid (px-2/3/4, gap-2/3/4, h-8/9/10). Radius is deliberately
// two-tier: `rounded-lg` (8px) for controls, `rounded-xl` (12px) for cards and panels.
// Elevation comes from borders, not shadows — shadow is reserved for true overlays
// (modals, popovers), so a dense dashboard stays flat and legible.

// ── surfaces ─────────────────────────────────────────────────────────────────────────
// Dark mode is deep navy/slate (slate-950 page, slate-900 chrome), light mode is
// white chrome on a slate-100 page so panel edges read without needing shadows.
export const STUDIO = {
  page: "bg-slate-100 dark:bg-slate-950",
  // Header and control deck: the fixed chrome that frames the workspace.
  chrome: "border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900",
  // A card/panel sitting on the page.
  card: "rounded-xl border border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900",
  // Hover for an interactive card — a border lift, never a translate or a shadow.
  cardHover:
    "transition-colors duration-150 hover:border-slate-300 dark:hover:border-slate-700 motion-reduce:transition-none",
  // A recessed group inside a card/chrome (metric clusters, stat tiles, filter tracks).
  inset: "rounded-lg border border-slate-200 bg-slate-50 dark:border-slate-800 dark:bg-slate-800/40",
  divider: "border-slate-200 dark:border-slate-800",
  divideX: "divide-slate-200 dark:divide-slate-800",

  // ── text ladder ────────────────────────────────────────────────────────────────────
  heading: "text-slate-900 dark:text-white",
  body: "text-slate-700 dark:text-slate-200",
  muted: "text-slate-500 dark:text-slate-400",
  faint: "text-slate-400 dark:text-slate-500",
  // Small uppercase label above a value. 11px is the floor for legibility at this weight.
  eyebrow: "text-[11px] font-semibold uppercase tracking-wide",

  // ── the video stage ────────────────────────────────────────────────────────────────
  // Flat deep slate rather than a gradient: a broadcast monitor should not tint the
  // picture it is monitoring. The vignette is a single low-opacity inset shadow.
  stage: "bg-slate-950",
  stageVignette: "shadow-[inset_0_0_120px_rgba(0,0,0,0.55)]",
  // Overlay chip on top of video. Solid-ish black so it stays legible over any frame.
  chip:
    "inline-flex items-center gap-1.5 rounded-md bg-slate-950/70 px-2 py-1 text-[11px] font-semibold leading-none text-white backdrop-blur-sm ring-1 ring-white/10",
  // Scrims behind the top/bottom overlay rows, so chips never fight the picture.
  scrimTop: "bg-gradient-to-b from-slate-950/70 via-slate-950/25 to-transparent",
  scrimBottom: "bg-gradient-to-t from-slate-950/75 via-slate-950/25 to-transparent",
};

// ── interaction ──────────────────────────────────────────────────────────────────────
// Focus is always visible and always the brand accent. Offset colour is set per surface
// so the ring reads on white chrome, on the slate page and on the black stage.
export const focus =
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500 focus-visible:ring-offset-2 focus-visible:ring-offset-white dark:focus-visible:ring-offset-slate-900";
export const focusOnStage =
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-400 focus-visible:ring-offset-2 focus-visible:ring-offset-slate-950";

// Colours-only transition: nothing in the deck should shift position on hover, or a
// wrapping control row jitters as the pointer crosses it.
export const t150 = "transition-colors duration-150 ease-out motion-reduce:transition-none";

// ── semantic state → text/icon colour ────────────────────────────────────────────────
// Used by every readout that reports a live measurement. `neutral` means "no signal",
// which is distinct from "healthy" and must never render green.
export const SIGNAL = {
  good: "text-green-600 dark:text-green-400",
  warn: "text-amber-600 dark:text-amber-400",
  bad: "text-rose-600 dark:text-rose-400",
  brand: "text-violet-600 dark:text-violet-400",
  neutral: "text-slate-400 dark:text-slate-500",
};

// Accent per KPI tile. Only the icon and the live-dot take the accent — the number itself
// stays in the heading colour, so six tiles side by side read as one row of data rather
// than six competing colour blocks.
export const KPI_ACCENT = {
  brand: "text-violet-600 dark:text-violet-400",
  blue: "text-blue-600 dark:text-blue-400",
  green: "text-green-600 dark:text-green-400",
  amber: "text-amber-600 dark:text-amber-400",
  rose: "text-rose-600 dark:text-rose-400",
  slate: "text-slate-500 dark:text-slate-400",
};

// ── control-deck button skins ────────────────────────────────────────────────────────
// A deck button has three visual states beyond disabled: at rest (quiet, bordered),
// engaged (filled, so "my mic is off" is unmistakable at a glance) and destructive.
// `active` fills are keyed to what the state MEANS, not to the button's position.
export const DECK = {
  rest: "border border-slate-200 bg-white text-slate-600 hover:border-slate-300 hover:bg-slate-50 hover:text-slate-900 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300 dark:hover:border-slate-600 dark:hover:bg-slate-800 dark:hover:text-white",
  // Engaged states.
  brand: "border border-violet-600 bg-violet-600 text-white hover:bg-violet-500 hover:border-violet-500",
  danger: "border border-rose-600 bg-rose-600 text-white hover:bg-rose-500 hover:border-rose-500",
  warn: "border border-amber-500 bg-amber-500 text-white hover:bg-amber-400 hover:border-amber-400",
  good: "border border-green-600 bg-green-600 text-white hover:bg-green-500 hover:border-green-500",
};

// Transport buttons (Go Live / Pause / Resume / End). Larger, labelled, and the only
// place in the deck that carries weight — an operator must find these without looking.
export const TRANSPORT = {
  primary: "bg-violet-600 text-white hover:bg-violet-500",
  hold: "bg-amber-500 text-white hover:bg-amber-400",
  danger: "bg-rose-600 text-white hover:bg-rose-500",
  dangerArmed: "bg-rose-700 text-white ring-2 ring-rose-400 dark:ring-rose-500/50",
};

export const disabled = "disabled:cursor-not-allowed disabled:opacity-45";
