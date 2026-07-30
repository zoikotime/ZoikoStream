/* eslint-disable react-refresh/only-export-components -- useLiveValue hook + LiveStat component share this module by design */
import { useEffect, useState } from "react";
import { cx } from "../../ui/tokens";
import { prefersReducedMotion } from "../../ui/motion";

// Realtime-feel value that drifts around `base` via a deterministic sine of a ticking
// clock (no Math.random, matching the data house style). Freezes on reduced-motion.
// ponytail: swap the interval for a socket.io "platform:activity" subscription later.
export function useLiveValue(base, amplitude = base * 0.02, periodMs = 2000) {
  const reduced = prefersReducedMotion();
  const [tick, setTick] = useState(0);
  useEffect(() => {
    if (reduced) return;
    const id = setInterval(() => setTick((t) => t + 1), periodMs);
    return () => clearInterval(id);
  }, [reduced, periodMs]);
  return base + amplitude * Math.sin(tick / 1.7) + (amplitude / 2) * Math.sin(tick / 0.9);
}

// Compact live metric: pulsing dot + label + ticking value. Renders the value directly
// (not via Counter) so it eases between ticks instead of resetting to zero each update.
export function LiveStat({ label, base, amplitude, prefix = "", suffix = "", decimals = 0, className = "" }) {
  const value = useLiveValue(base, amplitude);
  const text = value.toLocaleString(undefined, { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
  return (
    <div className={cx("rounded-xl border border-slate-200 bg-white/60 px-4 py-3 dark:border-slate-800 dark:bg-slate-900/40", className)}>
      <p className="flex items-center gap-1.5 text-xs font-medium text-slate-500 dark:text-slate-400">
        <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-green-500" /> {label}
      </p>
      <p className="mt-1 text-2xl font-bold tabular-nums text-slate-900 transition-all duration-500 dark:text-white">
        {prefix}{text}{suffix}
      </p>
    </div>
  );
}
