import { FiAlertTriangle, FiRefreshCw } from "react-icons/fi";
import { CONSOLE, cx, focusRing } from "../../ui/tokens";
import { ageOf, secondsSince } from "../../data/eventTime";

// DataFreshnessBadge (ZST-ENG-DASH-001 §3.1): when a reading was taken, and whether it is
// still current. Past `staleAfter` seconds — or when the last refresh failed — it says STALE
// in words and colour both, so an old green reading can never pass for a current one (§7.3,
// EVT-07 QA H). Purely a readout: refreshing reads again and changes nothing.
export default function Freshness({ asOf, staleAfter = 60, failed = false, onRefresh, refreshing = false, now }) {
  const stale = failed || secondsSince(asOf, now) > staleAfter;
  const age = ageOf(asOf, now);
  return (
    <span className="inline-flex flex-wrap items-center gap-2 text-[11px]" role="status" aria-live="polite">
      {stale ? (
        <span className="inline-flex items-center gap-1 rounded-full bg-amber-100 px-2 py-0.5 font-semibold text-amber-700 dark:bg-amber-500/15 dark:text-amber-300">
          <FiAlertTriangle aria-hidden="true" />
          {failed ? "Stale — last refresh failed" : "Stale"}
        </span>
      ) : (
        <span className="inline-flex items-center gap-1 rounded-full bg-slate-100 px-2 py-0.5 font-semibold text-slate-600 dark:bg-white/[0.08] dark:text-neutral-300">
          Current
        </span>
      )}
      {/* Body, not faint: this readout sits on tinted strips too (rose-50, violet-50), where
          slate-500 measured 4.34–4.42:1. */}
      <span className={CONSOLE.body}>{age ? `As of ${age}` : "Not yet read"}</span>
      {onRefresh && (
        <button
          type="button"
          onClick={onRefresh}
          disabled={refreshing}
          className={cx("inline-flex items-center gap-1 rounded px-1 font-semibold disabled:opacity-60", CONSOLE.link, focusRing)}
        >
          <FiRefreshCw aria-hidden="true" className={cx(refreshing && "animate-spin motion-reduce:animate-none")} />
          Refresh
        </button>
      )}
    </span>
  );
}
