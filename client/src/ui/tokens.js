// Design tokens — the single source of truth for the design system.
// Components reference these maps so styling lives in ONE place (no duplication).
// Colors are expressed as Tailwind utility strings (the app's styling primitive),
// grouped semantically. Dark-mode pairs are baked in.

// Canonical surface + text classes. Reuse via <Card>, but exported for edge cases.
export const surface = {
  base: "bg-white dark:bg-slate-900",
  subtle: "bg-slate-50 dark:bg-slate-950",
  border: "border-slate-200 dark:border-slate-800",
  ring: "ring-slate-200 dark:ring-slate-800",
};

export const text = {
  heading: "text-slate-900 dark:text-white",
  body: "text-slate-600 dark:text-slate-400",
  muted: "text-slate-500 dark:text-slate-500",
  strong: "text-slate-800 dark:text-slate-100",
};

// Accent → chip (icon tile), soft text, and border-on-hover. One entry per accent.
export const ACCENT = {
  emerald: {
    chip: "bg-emerald-100 text-emerald-600 dark:bg-emerald-500/15 dark:text-emerald-400",
    text: "text-emerald-700 dark:text-emerald-400",
    hoverBorder: "hover:border-emerald-300 dark:hover:border-emerald-500/40",
    solid: "bg-emerald-600",
  },
  indigo: {
    chip: "bg-indigo-100 text-indigo-600 dark:bg-indigo-500/15 dark:text-indigo-400",
    text: "text-indigo-700 dark:text-indigo-400",
    hoverBorder: "hover:border-indigo-300 dark:hover:border-indigo-500/40",
    solid: "bg-indigo-600",
  },
  violet: {
    chip: "bg-violet-100 text-violet-600 dark:bg-violet-500/15 dark:text-violet-400",
    text: "text-violet-700 dark:text-violet-400",
    hoverBorder: "hover:border-violet-300 dark:hover:border-violet-500/40",
    solid: "bg-violet-600",
  },
  blue: {
    chip: "bg-blue-100 text-blue-600 dark:bg-blue-500/15 dark:text-blue-400",
    text: "text-blue-700 dark:text-blue-400",
    hoverBorder: "hover:border-blue-300 dark:hover:border-blue-500/40",
    solid: "bg-blue-600",
  },
  amber: {
    chip: "bg-amber-100 text-amber-600 dark:bg-amber-500/15 dark:text-amber-400",
    text: "text-amber-700 dark:text-amber-400",
    hoverBorder: "hover:border-amber-300 dark:hover:border-amber-500/40",
    solid: "bg-amber-500",
  },
  rose: {
    chip: "bg-rose-100 text-rose-600 dark:bg-rose-500/15 dark:text-rose-400",
    text: "text-rose-700 dark:text-rose-400",
    hoverBorder: "hover:border-rose-300 dark:hover:border-rose-500/40",
    solid: "bg-rose-600",
  },
};

// Status → pill classes (badges). Consolidates the old ORG_STATUS + ad-hoc pills.
export const STATUS = {
  active: "bg-emerald-100 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-400",
  success: "bg-emerald-100 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-400",
  live: "bg-emerald-100 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-400",
  trial: "bg-amber-100 text-amber-700 dark:bg-amber-500/15 dark:text-amber-400",
  warning: "bg-amber-100 text-amber-700 dark:bg-amber-500/15 dark:text-amber-400",
  pending: "bg-blue-100 text-blue-700 dark:bg-blue-500/15 dark:text-blue-400",
  info: "bg-blue-100 text-blue-700 dark:bg-blue-500/15 dark:text-blue-400",
  suspended: "bg-rose-100 text-rose-700 dark:bg-rose-500/15 dark:text-rose-400",
  error: "bg-rose-100 text-rose-700 dark:bg-rose-500/15 dark:text-rose-400",
  neutral: "bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300",
};

// Non-color tokens — referenced by components and available to consumers.
export const radius = { sm: "rounded-lg", md: "rounded-xl", lg: "rounded-2xl", xl: "rounded-3xl", full: "rounded-full" };
export const shadow = { sm: "shadow-sm", md: "shadow-md", lg: "shadow-lg", xl: "shadow-xl", glow: "shadow-lg shadow-emerald-600/20" };
// Vertical rhythm scale (used by Section) and z-index layers (overlays).
export const spacing = { section: "py-20 sm:py-28", block: "py-12 sm:py-16", stack: "space-y-6" };
export const z = { header: "z-50", overlay: "z-[60]", modal: "z-[70]", toast: "z-[80]" };

// Tiny className joiner (no clsx dependency needed for this).
export const cx = (...parts) => parts.filter(Boolean).join(" ");

// ─────────────────────────────────────────────────────────────────────────────
// Admin console vocabulary — merged here from the former components/admin/tokens.js
// so the whole app has ONE token module. Names that collided with the marketing
// tokens above were resolved: admin's unused ACCENT="violet" string was dropped
// (the ACCENT object above wins); admin's `surface` STRING became `panelSurface`
// (the `surface` OBJECT above keeps its name). TONE is kept DISTINCT from STATUS —
// they use different palettes (STATUS: emerald→violet via the index.css remap;
// TONE: literal green), so merging them would change badge colors.
// ─────────────────────────────────────────────────────────────────────────────

// Type scale (px) as ready-to-use className strings. Numeric styles carry tabular-nums.
export const type = {
  metric: "text-[32px] leading-[36px] font-semibold tabular-nums tracking-tight",
  stat: "text-[26px] leading-8 font-semibold tabular-nums tracking-tight",
  h1: "text-[24px] leading-8 font-semibold tracking-tight",
  h2: "text-[20px] leading-7 font-semibold tracking-tight",
  title: "text-[16px] leading-6 font-semibold",
  body: "text-[14px] leading-5",
  label: "text-[13px] leading-5",
  caption: "text-[12px] leading-4",
  eyebrow: "text-[11px] font-semibold uppercase tracking-wider",
  num: "tabular-nums",
  mono: "font-mono tabular-nums",
};

// Interaction primitives — every interactive element composes these.
export const focusRing =
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500 focus-visible:ring-offset-2 focus-visible:ring-offset-white dark:focus-visible:ring-offset-slate-900";
export const t150 = "transition-colors duration-150 ease-out motion-reduce:transition-none";
export const tap = "transition duration-150 ease-out active:scale-[0.98] motion-reduce:transition-none motion-reduce:active:scale-100";

// Console surfaces — flat + bordered (shadow reserved for true overlays).
// `panelSurface` was admin's `surface` string; `overlay` for menus/modals.
// The dark fill is a TRANSLUCENT wash rather than a fixed grey, so the same token reads
// correctly on the console's true-black page and on the org area's slate page — one
// surface definition, no per-area fork.
export const panelSurface =
  "rounded-lg border border-slate-200 bg-white dark:border-white/10 dark:bg-white/[0.02]";
export const overlay =
  "rounded-lg border border-slate-200 bg-white shadow-lg dark:border-white/10 dark:bg-neutral-950";

// Semantic tones for the admin console (pill bg + text; dot uses bg-current).
// Literal green for success (NOT the remapped emerald), so it reads as green.
export const TONE = {
  success: "bg-green-100 text-green-700 dark:bg-green-500/15 dark:text-green-400",
  warning: "bg-amber-100 text-amber-700 dark:bg-amber-500/15 dark:text-amber-400",
  danger: "bg-rose-100 text-rose-700 dark:bg-rose-500/15 dark:text-rose-400",
  info: "bg-blue-100 text-blue-700 dark:bg-blue-500/15 dark:text-blue-400",
  brand: "bg-violet-100 text-violet-700 dark:bg-violet-500/15 dark:text-violet-300",
  neutral: "bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300",
};

// ─────────────────────────────────────────────────────────────────────────────
// Command Center surfaces (/admin). The console runs a TRUE BLACK dark theme —
// #000 page, panels lifted with a translucent white wash rather than a grey fill, so
// there is exactly one black and every layer above it is derived from it. Light theme
// is the mirror image (slate-50 page, white panels). Every value is a `dark:` pair, so
// nothing here can break theme switching.
//
// Scoped to the console on purpose: the org dashboard, marketing site and watch pages
// keep the slate palette they were designed against.
// ─────────────────────────────────────────────────────────────────────────────
export const CONSOLE = {
  // Shell
  page: "bg-slate-50 dark:bg-black",
  rail: "border-slate-200 bg-white dark:border-white/10 dark:bg-black",
  bar: "border-slate-200 bg-white/85 dark:border-white/10 dark:bg-black/85",
  // Panels — flat, bordered, no shadow (console surface, not a marketing card).
  panel: "rounded-xl border border-slate-200 bg-white dark:border-white/10 dark:bg-white/[0.02]",
  panelHover: "hover:border-slate-300 dark:hover:border-white/20",
  inset: "rounded-lg border border-slate-200 bg-slate-50/80 dark:border-white/10 dark:bg-white/[0.03]",
  divider: "border-slate-200 dark:border-white/10",
  divideY: "divide-slate-100 dark:divide-white/[0.07]",
  // Text ladder
  heading: "text-slate-900 dark:text-white",
  body: "text-slate-600 dark:text-neutral-300",
  muted: "text-slate-500 dark:text-neutral-400",
  faint: "text-slate-400 dark:text-neutral-500",
  // Controls
  control:
    "border-slate-200 bg-white text-slate-700 hover:bg-slate-50 dark:border-white/10 dark:bg-white/[0.03] dark:text-neutral-200 dark:hover:bg-white/[0.07]",
  segment: "bg-slate-100 dark:bg-white/[0.05]",
  segmentOn: "bg-violet-600 text-white shadow-sm",
  segmentOff: "text-slate-600 hover:text-slate-900 dark:text-neutral-400 dark:hover:text-white",
  link: "text-violet-600 hover:text-violet-700 dark:text-violet-400 dark:hover:text-violet-300",

  // Sidebar navigation states. Three levels have to stay tellable apart at a glance:
  //   rest  — quiet, recedes
  //   hover — clearly reactive (the old values were ~4% washes, effectively invisible)
  //   on    — violet-tinted in BOTH themes, matching the active-item treatment in the design
  // Hover is a neutral lift rather than a violet tint so it never reads as "selected".
  navOn: "bg-violet-50 text-violet-700 dark:bg-violet-500/[0.14] dark:text-white",
  navOff: [
    "text-slate-600 dark:text-neutral-400",
    "hover:bg-slate-100 hover:text-slate-900",
    "dark:hover:bg-white/[0.08] dark:hover:text-white",
  ].join(" "),
  // Icons carry the state too — a violet icon on the active row, muted at rest, and
  // brightened on hover so the whole row responds as one target.
  navIconOn: "text-violet-600 dark:text-violet-400",
  navIconOff: "text-slate-400 group-hover:text-slate-600 dark:text-neutral-500 dark:group-hover:text-neutral-200",
};

// Heat cells for the stage × region availability matrix. Thresholds are availability %,
// highest first — the first match wins.
export const HEAT = [
  { min: 99.9, cls: "bg-green-50 text-green-700 dark:bg-green-500/10 dark:text-green-400" },
  { min: 99.0, cls: "bg-green-50/70 text-green-600 dark:bg-green-500/[0.07] dark:text-green-500" },
  { min: 98.0, cls: "bg-amber-50 text-amber-700 dark:bg-amber-500/10 dark:text-amber-400" },
  { min: 0, cls: "bg-rose-50 text-rose-700 dark:bg-rose-500/10 dark:text-rose-400" },
];
export const heatClass = (pct) =>
  pct == null
    ? "bg-slate-50 text-slate-400 dark:bg-white/[0.02] dark:text-neutral-600"
    : (HEAT.find((h) => pct >= h.min) || HEAT[HEAT.length - 1]).cls;

// Severity/verdict pills used across the console's tables and cards.
export const SEVERITY = {
  critical: "bg-rose-100 text-rose-700 dark:bg-rose-500/15 dark:text-rose-400",
  sev1: "bg-rose-100 text-rose-700 dark:bg-rose-500/15 dark:text-rose-400",
  sev2: "bg-rose-100 text-rose-700 dark:bg-rose-500/15 dark:text-rose-400",
  high: "bg-amber-100 text-amber-700 dark:bg-amber-500/15 dark:text-amber-400",
  sev3: "bg-amber-100 text-amber-700 dark:bg-amber-500/15 dark:text-amber-400",
  monitoring: "bg-slate-100 text-slate-600 dark:bg-white/[0.07] dark:text-neutral-300",
  sev4: "bg-slate-100 text-slate-600 dark:bg-white/[0.07] dark:text-neutral-300",
  blocked: "bg-rose-100 text-rose-700 dark:bg-rose-500/15 dark:text-rose-400",
  conditional: "bg-amber-100 text-amber-700 dark:bg-amber-500/15 dark:text-amber-400",
  passed: "bg-green-100 text-green-700 dark:bg-green-500/15 dark:text-green-400",
};

// Lifecycle stage accent — one hue per stage, in rail order. Hex because the rail dots
// and the connecting gradient are inline SVG/CSS, not utility classes.
export const STAGE_COLOR = {
  contribute: "#22d3ee",
  ingest: "#3b82f6",
  produce: "#f59e0b",
  secure: "#8b5cf6",
  deliver: "#f43f5e",
  understand: "#f59e0b",
  preserve: "#10b981",
  platform: "#e5e7eb",
};

// Chart series colors — consistent mapping between charts and their labels/legends.
export const SERIES = {
  brand: "#8b5cf6", // violet — primary
  info: "#3b82f6", // blue
  success: "#22c55e",
  warning: "#f59e0b",
  danger: "#f43f5e",
  muted: "#64748b",
};
