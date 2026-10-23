import { cx } from "../../../ui/tokens";

// The console card surface (ZST-ENG-DASH-001 frames): white, softly bordered, 16px radius.
export const CARD = cx(
  "rounded-2xl border border-slate-200/80 bg-white shadow-[0_1px_2px_rgba(15,23,42,0.04)]",
  "dark:border-white/[0.12] dark:bg-white/[0.04] dark:shadow-none"
);
