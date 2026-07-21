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
