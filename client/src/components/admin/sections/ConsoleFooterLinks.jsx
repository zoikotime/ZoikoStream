import { Link } from "react-router-dom";
import { CONSOLE, cx, type } from "../../../ui/tokens";

// What is left of three panels, after the Command Center stopped trying to be them.
//
//   Action queues        — four tabs, four item lists, and a "Queue is clear" empty state
//                          the size of a real queue. Support Operations, Trust & Safety and
//                          Commerce each own their own queue and show it properly.
//   Governance exposure  — five rows of standing obligations (legal holds, entitlement
//                          overrides, break-glass grants, single-path overrides, export
//                          delivery). Governance, Usage & Entitlements and Commerce own
//                          those, in the detail they deserve.
//   Privileged activity  — a live audit feed. Audit is a sidebar entry.
//
// None of them answered a Command Center question; all three answered "what is the detail",
// which is the next page's job. What survives is the only part that belongs on an overview:
// whether there is a non-zero number worth walking over to.
//
// A count renders ONLY when it is greater than zero, so a quiet platform shows a quiet
// footer — the rule the sidebar badges already follow. The plain navigation shortcuts always
// render, because a shortcut is not a claim about state.
function Stat({ to, label, value, urgent }) {
  if (!value) return null;
  return (
    <Link
      to={to}
      className={cx(
        "inline-flex items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-[12px] font-medium transition",
        CONSOLE.control
      )}
    >
      <span className={cx("font-semibold tabular-nums", type.num, urgent ? "text-amber-600 dark:text-amber-400" : CONSOLE.heading)}>
        {value}
      </span>
      {/* An explicit space literal. JSX drops the whitespace between sibling elements, which
          made the accessible name read "3queued actions" to a screen reader — and &nbsp;
          would only have swapped that for a non-breaking space, which is not what a reader
          or a test matches on. */}
      {" "}
      <span className={CONSOLE.body}>{label}</span>
    </Link>
  );
}

const SHORTCUTS = [
  ["/admin/status", "System Status"],
  ["/admin/audit", "Audit"],
  ["/admin/event-readiness", "Event Readiness"],
  ["/admin/live-events", "Live Operations"],
];

export default function ConsoleFooterLinks({ queues = [], governance, privilegedCount = 0 }) {
  const queueTotal = queues.reduce((sum, q) => sum + (q.count || 0), 0);

  // The governance rows that represent a human still owing an action. `usage_export_on_time_pct`
  // is deliberately excluded — it is a delivery statistic, not an open obligation.
  const g = governance || {};
  const governanceOpen =
    (g.legal_holds || 0) +
    (g.entitlement_overrides_pending || 0) +
    (g.break_glass_under_review || 0) +
    (g.single_path_overrides_quarter || 0);

  return (
    <div className={cx("flex flex-wrap items-center gap-2 border-t pt-4", CONSOLE.divider)}>
      <Stat to="/admin/support" label="queued actions" value={queueTotal} urgent />
      <Stat to="/admin/governance" label="governance actions" value={governanceOpen} urgent />
      <Stat to="/admin/audit" label="recent privileged actions" value={privilegedCount} />

      <span className="ml-auto flex flex-wrap items-center gap-x-3 gap-y-1">
        {SHORTCUTS.map(([to, label]) => (
          <Link key={to} to={to} className={cx("text-[12px] font-medium", CONSOLE.link)}>
            {label}
          </Link>
        ))}
      </span>
    </div>
  );
}
