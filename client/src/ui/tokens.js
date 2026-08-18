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

// ── Brand gradient ───────────────────────────────────────────────────────────
// The ZoikoStream mark runs blue → violet → magenta (the "Z" sweep and the STREAM wordmark
// are both that ramp), so every primary action in the console paints with THIS string and
// the buttons finally match the logo above them.
//
// One definition on purpose: the old violet→indigo fill was spelled out separately in the
// console Button, the org rail's active row, the org topbar CTA and four avatar chips, so
// re-tinting the product meant finding five copies.
//
// Dark mode lifts every stop one step (600 → 500, hover 500 → 400). On the console's true
// black a 600-weight ramp reads muddy; 500 keeps it legible without becoming neon.
export const brand = {
  fill: "bg-gradient-to-r from-blue-600 via-violet-600 to-fuchsia-600 dark:from-blue-500 dark:via-violet-500 dark:to-fuchsia-500",
  fillHover:
    "hover:from-blue-500 hover:via-violet-500 hover:to-fuchsia-500 dark:hover:from-blue-400 dark:hover:via-violet-400 dark:hover:to-fuchsia-400",
  glow: "shadow-sm shadow-violet-600/25 hover:shadow-md hover:shadow-fuchsia-600/35 dark:shadow-fuchsia-500/20 dark:hover:shadow-fuchsia-400/30",
  // Identity chips (avatars, workspace initials) — same ramp on the diagonal.
  chip: "bg-gradient-to-br from-blue-600 via-violet-600 to-fuchsia-600 dark:from-blue-500 dark:via-violet-500 dark:to-fuchsia-500",
};
export const brandButton = `${brand.fill} ${brand.fillHover} ${brand.glow} text-white`;

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
// Shared skin for every console filter-bar field (input, select, textarea).
//
// Hover is a BORDER lift with a barely-there wash, never a solid fill: a filled field reads as
// selected or disabled, and these are inputs that happen to be idle. The transition is
// colors-only so a native select chevron never shifts, and `motion-reduce` opts out.
const FIELD_BASE = [
  "rounded-lg border border-slate-200 bg-white text-sm text-slate-800 placeholder:text-slate-400",
  "transition-colors duration-150 motion-reduce:transition-none",
  "hover:border-slate-300 hover:bg-slate-50/60",
  "focus-visible:border-violet-500 focus-visible:bg-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500",
  "dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100",
  "dark:hover:border-slate-600 dark:hover:bg-slate-800/60 dark:focus-visible:bg-slate-900",
].join(" ");

// Dark-mode note: the page stays true black (one black, everything above it derived), but
// every layer ON it was pitched too low to read — a 2% panel fill on #000 is almost the page
// itself, and neutral-500 body copy on black is grey mush. Each dark value below is lifted
// one step: fills ~2× brighter, borders 10% → 16%, and the text ladder up one shade.
export const CONSOLE = {
  // Shell
  page: "bg-slate-50 dark:bg-black",
  rail: "border-slate-200 bg-white dark:border-white/12 dark:bg-black",
  bar: "border-slate-200 bg-white/85 dark:border-white/12 dark:bg-black/85",
  // Panels — flat, bordered, no shadow (console surface, not a marketing card).
  panel: "rounded-xl border border-slate-200 bg-white dark:border-white/[0.14] dark:bg-white/[0.05]",
  panelHover: "hover:border-slate-300 dark:hover:border-white/25",
  inset: "rounded-lg border border-slate-200 bg-slate-50/80 dark:border-white/[0.14] dark:bg-white/[0.07]",
  divider: "border-slate-200 dark:border-white/[0.14]",
  divideY: "divide-slate-100 dark:divide-white/[0.10]",
  // Text ladder
  heading: "text-slate-900 dark:text-white",
  body: "text-slate-600 dark:text-neutral-200",
  muted: "text-slate-500 dark:text-neutral-300",
  faint: "text-slate-400 dark:text-neutral-400",
  // Controls
  control:
    "border-slate-200 bg-white text-slate-700 hover:bg-slate-50 dark:border-white/[0.16] dark:bg-white/[0.07] dark:text-neutral-100 dark:hover:bg-white/[0.12]",
  segment: "bg-slate-100 dark:bg-white/[0.08]",
  // The selected segment carries the brand ramp, so a tab strip and the primary button in the
  // same toolbar read as one system.
  segmentOn: `${brand.fill} text-white shadow-sm`,
  // An inactive segment/tab now lifts on hover instead of only darkening its text. In light mode
  // a text-only change on a grey track is nearly invisible, which made every tab strip read as
  // static labels rather than as controls.
  segmentOff:
    "text-slate-600 hover:bg-white hover:text-slate-900 hover:shadow-sm dark:text-neutral-300 dark:hover:bg-white/12 dark:hover:text-white",
  link: "text-violet-600 hover:text-violet-700 dark:text-violet-300 dark:hover:text-violet-200",

  // ── Filter-bar fields ──────────────────────────────────────────────────────
  // Ready-made shapes built from FIELD_BASE (defined above). Thirteen admin pages were each
  // declaring their own copy of these strings, so adding a hover state meant editing it thirteen
  // times to see it once.
  field: `h-9 w-full px-3 ${FIELD_BASE}`,
  // Search input with a leading icon — the icon occupies the left padding.
  search: `h-9 w-full pl-9 pr-3 ${FIELD_BASE}`,
  // Width is left to the caller so a filter row can size selects to their content.
  select: `h-9 cursor-pointer px-3 text-slate-700 dark:text-slate-200 ${FIELD_BASE}`,
  textarea: `w-full px-3 py-2 ${FIELD_BASE}`,
  // Checkbox with a hover ring, so a toggle in a filter row is discoverable before it is clicked.
  checkbox:
    "h-4 w-4 shrink-0 cursor-pointer rounded border-slate-300 accent-violet-600 transition-shadow duration-150 hover:ring-2 hover:ring-violet-500/25 motion-reduce:transition-none dark:border-slate-600",
  // Wrapper for a checkbox + its label, so the whole pair is one hoverable target.
  checkboxRow:
    "inline-flex cursor-pointer items-center gap-2 rounded-lg px-2 py-1.5 transition-colors duration-150 hover:bg-slate-100 motion-reduce:transition-none dark:hover:bg-white/[0.06]",

  // Sidebar navigation states. Three levels have to stay tellable apart at a glance:
  //   rest  — quiet, recedes
  //   hover — clearly reactive (the old values were ~4% washes, effectively invisible)
  //   on    — the brand ramp, filled, in BOTH themes and BOTH consoles
  // Hover is a neutral lift rather than a brand tint so it never reads as "selected".
  //
  // The active row used to be a pale violet tint here (admin rail) while the org rail drew its
  // own violet→indigo gradient. Same product, two answers — this is now the one treatment, and
  // the org rail's private copy is gone.
  navOn: `${brand.fill} text-white shadow-[0_2px_10px_-2px_rgba(124,58,237,0.5)]`,
  navOff: [
    "text-slate-600 dark:text-neutral-300",
    "hover:bg-slate-100 hover:text-slate-900",
    "dark:hover:bg-white/[0.10] dark:hover:text-white",
  ].join(" "),
  // On a filled active row the icon inherits white; at rest it stays muted and brightens with
  // the row so the whole target responds as one.
  navIconOn: "text-white",
  navIconOff: "text-slate-400 group-hover:text-slate-600 dark:text-neutral-400 dark:group-hover:text-neutral-100",
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
