import { Link } from "react-router-dom";
import { CONSOLE, cx, type } from "../../ui/tokens";
import Panel from "../admin/Panel";

// Usage against the subscribed plan's real limits.
//
// Only the limits the Plan model actually carries get a bar. A bar needs a denominator, and
// an invented ceiling would make the fill meaningless — so an org on no plan sees its real
// usage with "no limit set" instead of a reassuring 12%-full bar.
const fill = (pct) =>
  pct == null ? "bg-slate-300 dark:bg-white/20"
  : pct >= 100 ? "bg-rose-500"
  : pct >= 80 ? "bg-amber-500"
  : "bg-violet-500";

const fmt = (n) =>
  typeof n === "number" ? (Number.isInteger(n) ? n.toLocaleString() : n.toFixed(1)) : n;

export default function EntitlementBars({ entitlements }) {
  const e = entitlements || {};
  const items = e.items || [];

  return (
    <Panel
      title="Usage & entitlements"
      description={
        e.plan
          ? `${e.plan}${e.status ? ` · ${e.status}` : ""}`
          : "No active subscription — usage is tracked without a limit"
      }
      action={
        <Link to="/organization/billing" className={cx("text-[12px] font-semibold", CONSOLE.link)}>
          Usage & Entitlements →
        </Link>
      }
    >
      <div className="grid gap-x-8 gap-y-4 sm:grid-cols-2 lg:grid-cols-3">
        {items.map((it) => (
          <div key={it.label}>
            <div className="flex items-baseline justify-between gap-2">
              <p className={cx("text-[12px] font-medium", CONSOLE.body)}>{it.label}</p>
              <p className={cx("text-[11px]", type.mono, CONSOLE.faint)}>
                {fmt(it.used)}
                {it.limit != null ? ` / ${fmt(it.limit)} ${it.unit}` : ` ${it.unit} · no limit set`}
              </p>
            </div>
            <div
              className="mt-1.5 h-1.5 w-full overflow-hidden rounded-full bg-slate-200 dark:bg-white/10"
              role="progressbar"
              aria-valuenow={it.percent ?? undefined}
              aria-valuemin={0}
              aria-valuemax={100}
              aria-label={`${it.label} usage`}
            >
              {/* No limit means no meaningful fill — the track stays empty rather than
                  implying a proportion we can't compute. */}
              {it.percent != null && (
                <span
                  className={cx("block h-full rounded-full transition-all", fill(it.percent))}
                  style={{ width: `${Math.min(it.percent, 100)}%` }}
                />
              )}
            </div>
            {it.percent != null && (
              <p
                className={cx(
                  "mt-1 text-[11px] font-semibold",
                  it.percent >= 100 ? "text-rose-600 dark:text-rose-400"
                  : it.percent >= 80 ? "text-amber-600 dark:text-amber-400"
                  : CONSOLE.faint
                )}
              >
                {it.percent}% of allotment
              </p>
            )}
          </div>
        ))}
      </div>

      {/* Delivery volume is a lifetime counter on the org row; there is no metering pipeline
          producing a windowed figure, so it is labelled for what it is. */}
      <div className={cx("mt-4 flex flex-wrap items-baseline gap-x-2 border-t pt-3", CONSOLE.divider)}>
        <p className={cx("text-[12px]", CONSOLE.body)}>Delivery volume to date</p>
        <p className={cx("text-[13px] font-semibold", type.mono, CONSOLE.heading)}>
          {e.delivery_gb_total != null ? `${fmt(e.delivery_gb_total)} GB` : "—"}
        </p>
        {e.delivery_windowed == null && e.delivery_note && (
          <p className={cx("basis-full text-[11px]", CONSOLE.faint)}>{e.delivery_note}</p>
        )}
      </div>
    </Panel>
  );
}
