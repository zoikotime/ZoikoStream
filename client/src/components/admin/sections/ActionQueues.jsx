import { useState } from "react";
import { Link } from "react-router-dom";
import { CONSOLE, cx, focusRing } from "../../../ui/tokens";
import Panel from "../Panel";
import { timeAgo } from "../format";

// Outstanding work, split by who owns it. Each queue is assembled server-side from the rows
// that actually represent the work — open incidents and blocked events (Operational),
// security incidents and break-glass reviews (Security), support tickets (Customer),
// entitlement overrides and expiring subscriptions (Commercial) — so a count here is a
// count of real records, and every item links to the page where it gets resolved.
const DOT = {
  danger: "bg-rose-500",
  warning: "bg-amber-500",
  info: "bg-blue-500",
  neutral: "bg-slate-400",
};

export default function ActionQueues({ queues = [] }) {
  const [active, setActive] = useState(queues[0]?.key || "operational");
  const total = queues.reduce((sum, q) => sum + q.count, 0);
  const current = queues.find((q) => q.key === active) || queues[0];

  return (
    <Panel
      title="Action queues"
      count={total}
      action={
        <Link to="/admin/support" className={cx("text-[12px] font-semibold", CONSOLE.link)}>
          View all →
        </Link>
      }
      flush
    >
      {/* Tabs */}
      <div className="px-4 pt-3 sm:px-5">
        <div
          role="tablist"
          aria-label="Action queues"
          className={cx("flex gap-1 overflow-x-auto rounded-lg p-1", CONSOLE.segment)}
        >
          {queues.map((q) => (
            <button
              key={q.key}
              role="tab"
              aria-selected={active === q.key}
              onClick={() => setActive(q.key)}
              className={cx(
                "flex shrink-0 items-center gap-1.5 rounded-md px-3 py-1.5 text-[12px] font-semibold transition",
                focusRing,
                active === q.key ? CONSOLE.segmentOn : CONSOLE.segmentOff
              )}
            >
              {q.label}
              <span className="opacity-70">·</span>
              <span className="tabular-nums">{q.count}</span>
            </button>
          ))}
        </div>
      </div>

      {/* Items */}
      {!current || current.items.length === 0 ? (
        <div className="px-5 py-9 text-center">
          <p className={cx("text-[13px] font-medium", CONSOLE.body)}>Queue is clear</p>
          <p className={cx("mt-1 text-[12px]", CONSOLE.faint)}>
            Nothing is waiting on the {current?.label?.toLowerCase() || "this"} desk.
          </p>
        </div>
      ) : (
        <ul className={cx("mt-2 divide-y", CONSOLE.divideY)}>
          {current.items.map((it) => (
            <li key={it.id}>
              <Link
                to={it.to}
                className="flex items-start gap-2.5 px-4 py-2.5 transition hover:bg-slate-50 sm:px-5 dark:hover:bg-white/[0.03]"
              >
                <span
                  className={cx("mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full", DOT[it.tone] || DOT.neutral)}
                  aria-hidden="true"
                />
                <span className="min-w-0 flex-1">
                  <span className={cx("block truncate text-[13px] font-semibold", CONSOLE.heading)}>
                    {it.title}
                  </span>
                  <span className={cx("block truncate text-[11px]", CONSOLE.faint)}>{it.detail}</span>
                </span>
                <span className={cx("shrink-0 text-[11px]", CONSOLE.faint)}>{timeAgo(it.at)}</span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}
