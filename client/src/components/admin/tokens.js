// ─────────────────────────────────────────────────────────────────────────────
// Admin design tokens — the single source of truth for the Super Admin console.
// Self-contained (does NOT modify the shared ui/ tokens the org side depends on).
// Everything downstream (Button, Badge, StatCard, DataTable, Panel, pages) reads
// from here so the visual system stays consistent.
//
// Principles: restrained neutral base + ONE accent (violet), semantic status colors,
// 4/8px spacing, 6–8px radius, 1px borders over shadows, tabular figures on numbers.
// ─────────────────────────────────────────────────────────────────────────────
import { cx } from "../../ui/tokens";
export { cx };

// The single confident accent. Used for primary actions + focus rings only.
export const ACCENT = "violet";

// Type scale (px) as ready-to-use className strings. Numeric styles carry tabular-nums
// so metric/table columns align. 12 / 13 / 14 / 16 / 20 / 24 / 32.
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
// 150ms snappy state changes; press feedback; all reduced-motion safe.
export const t150 = "transition-colors duration-150 ease-out motion-reduce:transition-none";
export const tap = "transition duration-150 ease-out active:scale-[0.98] motion-reduce:transition-none motion-reduce:active:scale-100";

// Surfaces — flat + bordered. Shadow is reserved for true overlays (menus/modals).
export const surface = "rounded-lg border border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900/50";
export const overlay = "rounded-lg border border-slate-200 bg-white shadow-lg dark:border-slate-700 dark:bg-slate-900";

// Semantic tones (pill bg + text; dot uses bg-current). success/warning/danger/info/brand/neutral.
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
