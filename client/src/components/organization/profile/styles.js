// Surface + rhythm constants for the Organization & Workspaces page.
//
// The org console runs a true-black dark theme (CONSOLE.page = bg-slate-50 dark:bg-black),
// so panels lift off the page with a translucent white wash rather than a fixed grey —
// same approach as ui/tokens' CONSOLE.panel, restated here with the softer radius and
// shadow this page uses.
//
// Spacing everywhere on the page is a multiple of 8px: gap-2 (8) · gap-4 (16) ·
// gap-6 (24) · gap-8 (32), with p-6 (24) card padding and space-y-8 (32) between sections.

export const CARD =
  "rounded-2xl border border-slate-200 bg-white shadow-[0_1px_2px_rgba(15,23,42,0.04)] dark:border-white/10 dark:bg-white/[0.02] dark:shadow-none";

// Interactive variant — the border warms and the shadow deepens on hover. Motion (the
// lift itself) comes from framer-motion so it respects reduced-motion.
export const CARD_INTERACTIVE =
  "transition-[border-color,box-shadow,background-color] duration-200 ease-out hover:border-violet-300 hover:shadow-[0_8px_24px_-12px_rgba(79,70,229,0.35)] dark:hover:border-violet-400/30 dark:hover:bg-white/[0.04]";

// Inset surface for rows inside a card (timeline rail, breakdown lists).
export const INSET =
  "rounded-xl border border-slate-200 bg-slate-50/70 dark:border-white/10 dark:bg-white/[0.03]";

// Text ladder — mirrors CONSOLE.* so this page reads as part of the console.
export const TXT = {
  heading: "text-slate-900 dark:text-white",
  body: "text-slate-600 dark:text-neutral-300",
  muted: "text-slate-500 dark:text-neutral-400",
  faint: "text-slate-400 dark:text-neutral-500",
};

// Accent → icon chip. Keyed the same as ui/tokens' ACCENT so the vocabulary matches,
// with the softer tint this page's cards use.
export const CHIP = {
  violet: "bg-violet-100 text-violet-600 dark:bg-violet-500/15 dark:text-violet-300",
  indigo: "bg-indigo-100 text-indigo-600 dark:bg-indigo-500/15 dark:text-indigo-300",
  blue: "bg-blue-100 text-blue-600 dark:bg-blue-500/15 dark:text-blue-300",
  emerald: "bg-emerald-100 text-emerald-600 dark:bg-emerald-500/15 dark:text-emerald-300",
  amber: "bg-amber-100 text-amber-600 dark:bg-amber-500/15 dark:text-amber-300",
  rose: "bg-rose-100 text-rose-600 dark:bg-rose-500/15 dark:text-rose-300",
  slate: "bg-slate-100 text-slate-600 dark:bg-white/[0.07] dark:text-neutral-300",
};

// Accent → progress-bar fill.
export const BAR = {
  violet: "bg-gradient-to-r from-violet-500 to-indigo-500",
  emerald: "bg-gradient-to-r from-emerald-500 to-teal-500",
  amber: "bg-gradient-to-r from-amber-400 to-orange-500",
  rose: "bg-gradient-to-r from-rose-500 to-red-500",
};

// Section heading used above every block on the page.
export const SECTION_TITLE = "text-[17px] font-semibold tracking-tight";
export const SECTION_SUB = "mt-1 text-[13px]";
