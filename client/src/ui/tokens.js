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
export const panelSurface = "rounded-lg border border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900/50";
export const overlay = "rounded-lg border border-slate-200 bg-white shadow-lg dark:border-slate-700 dark:bg-slate-900";

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

// Chart series colors — consistent mapping between charts and their labels/legends.
export const SERIES = {
  brand: "#8b5cf6", // violet — primary
  info: "#3b82f6", // blue
  success: "#22c55e",
  warning: "#f59e0b",
  danger: "#f43f5e",
  muted: "#64748b",
};
