/* eslint-disable react-refresh/only-export-components -- HealthDot component + healthColor helper share this module by design */
import { cx } from "../../ui/tokens";

// Traffic-light status indicator. The app remaps emerald->violet, so healthy states
// use TRUE green/amber/rose (untouched scales) to read as real health, not brand color.
const TONE = {
  ok: { dot: "bg-green-500", text: "text-green-600 dark:text-green-400", bg: "bg-green-100 dark:bg-green-500/15", label: "Operational" },
  success: { dot: "bg-green-500", text: "text-green-600 dark:text-green-400", bg: "bg-green-100 dark:bg-green-500/15", label: "Healthy" },
  warn: { dot: "bg-amber-500", text: "text-amber-600 dark:text-amber-400", bg: "bg-amber-100 dark:bg-amber-500/15", label: "Degraded" },
  warning: { dot: "bg-amber-500", text: "text-amber-600 dark:text-amber-400", bg: "bg-amber-100 dark:bg-amber-500/15", label: "Warning" },
  down: { dot: "bg-rose-500", text: "text-rose-600 dark:text-rose-400", bg: "bg-rose-100 dark:bg-rose-500/15", label: "Down" },
  error: { dot: "bg-rose-500", text: "text-rose-600 dark:text-rose-400", bg: "bg-rose-100 dark:bg-rose-500/15", label: "Critical" },
  critical: { dot: "bg-rose-500", text: "text-rose-600 dark:text-rose-400", bg: "bg-rose-100 dark:bg-rose-500/15", label: "Critical" },
  info: { dot: "bg-blue-500", text: "text-blue-600 dark:text-blue-400", bg: "bg-blue-100 dark:bg-blue-500/15", label: "Info" },
  pending: { dot: "bg-blue-500", text: "text-blue-600 dark:text-blue-400", bg: "bg-blue-100 dark:bg-blue-500/15", label: "Pending" },
  neutral: { dot: "bg-slate-400", text: "text-slate-500 dark:text-slate-400", bg: "bg-slate-100 dark:bg-slate-800", label: "Unknown" },
};

// Hex per status — for recharts (sparklines/trends) which need a color value, not a class.
const HEX = { ok: "#22c55e", success: "#22c55e", warn: "#f59e0b", warning: "#f59e0b", down: "#f43f5e", error: "#f43f5e", critical: "#f43f5e", info: "#3b82f6", pending: "#3b82f6", neutral: "#94a3b8" };
export const healthColor = (status) => HEX[status] || HEX.neutral;

// `status` keys into TONE. `label` overrides the default text; omit `children` for dot-only.
// `badge` renders a filled pill; `pulse` adds an animated ring (the "live" status effect).
export default function HealthDot({ status = "neutral", label, pulse, badge = false, className = "", children }) {
  const t = TONE[status] || TONE.neutral;
  const showText = children ?? label ?? t.label;
  return (
    <span
      className={cx(
        "inline-flex items-center gap-1.5 font-medium",
        t.text,
        badge ? cx("rounded-full px-2.5 py-1 text-xs font-semibold", t.bg) : "text-xs",
        className
      )}
    >
      <span className="relative flex h-2 w-2">
        {pulse && <span className={cx("absolute inline-flex h-full w-full rounded-full opacity-60 zk-pulse-ring", t.dot)} />}
        <span className={cx("relative inline-flex h-2 w-2 rounded-full", t.dot)} />
      </span>
      {showText && <span>{showText}</span>}
    </span>
  );
}
