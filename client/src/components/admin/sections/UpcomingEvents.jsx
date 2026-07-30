import { Link } from "react-router-dom";
import { CONSOLE, SEVERITY, cx, type } from "../../../ui/tokens";
import Panel from "../Panel";

// Upcoming high-impact events with their readiness verdict.
//
// The verdict is computed, not stored: every gate reads one real field on the event or its
// assignments (title, start time, host, moderator, recording, account standing, redundancy).
// A gate that is mandatory for the event's impact class BLOCKS it; a non-mandatory failure
// makes it CONDITIONAL. Hovering a card lists exactly which gates failed, so "Blocked" is
// never a dead end.
const VERDICT_LABEL = { blocked: "Blocked", conditional: "Conditional", passed: "Passed" };

const IMPACT = {
  unrepeatable: {
    label: "Unrepeatable",
    cls: "bg-violet-100 text-violet-700 dark:bg-violet-500/15 dark:text-violet-300",
    dot: true,
  },
  high: {
    label: "High impact",
    cls: "bg-slate-100 text-slate-600 dark:bg-white/[0.07] dark:text-neutral-300",
    dot: false,
  },
  standard: {
    label: "Standard",
    cls: "bg-slate-100 text-slate-500 dark:bg-white/[0.05] dark:text-neutral-400",
    dot: false,
  },
};

// Start time in the event's own timezone — an operator reads a schedule in the timezone the
// audience is in, not theirs.
function startLabel(iso, timezone) {
  if (!iso) return "—";
  try {
    return new Intl.DateTimeFormat("en-GB", {
      hour: "2-digit", minute: "2-digit", hour12: false,
      timeZone: timezone || undefined, timeZoneName: "short",
    }).format(new Date(iso));
  } catch {
    // An invalid stored timezone must not break the card.
    return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
  }
}

export default function UpcomingEvents({ events = [] }) {
  return (
    <Panel
      title="Upcoming high-impact events"
      count={events.filter((e) => e.verdict === "blocked").length}
      action={
        <Link to="/admin/event-readiness" className={cx("text-[12px] font-semibold", CONSOLE.link)}>
          Event Readiness →
        </Link>
      }
    >
      {events.length === 0 ? (
        <div className="py-6 text-center">
          <p className={cx("text-[13px] font-medium", CONSOLE.body)}>No high-impact events scheduled</p>
          <p className={cx("mt-1 text-[12px]", CONSOLE.faint)}>
            Events classified high or unrepeatable appear here once scheduled.
          </p>
        </div>
      ) : (
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          {events.map((e) => {
            const impact = IMPACT[e.impact] || IMPACT.standard;
            return (
              <div
                key={e.id}
                className={cx(
                  CONSOLE.inset,
                  "flex flex-col p-3.5 transition",
                  e.verdict === "blocked" && "ring-1 ring-rose-300 dark:ring-rose-500/30"
                )}
              >
                <span
                  className={cx(
                    "inline-flex w-fit items-center gap-1.5 rounded px-2 py-0.5 text-[11px] font-semibold",
                    impact.cls
                  )}
                >
                  {impact.dot && <span className="h-1.5 w-1.5 rounded-full bg-current" aria-hidden="true" />}
                  {impact.label}
                </span>

                <p className={cx("mt-2.5 text-[14px] font-semibold leading-snug", CONSOLE.heading)}>
                  {e.title}
                </p>
                <p className={cx("mt-0.5 truncate text-[11px]", CONSOLE.faint)}>{e.organization || "—"}</p>

                <div className={cx("mt-3 flex items-center justify-between gap-2 border-t pt-2.5", CONSOLE.divider)}>
                  <span className={cx("text-[12px]", type.mono, CONSOLE.body)}>
                    {startLabel(e.start_time, e.timezone)}
                  </span>
                  <span
                    className={cx(
                      "rounded px-1.5 py-0.5 text-[11px] font-bold",
                      SEVERITY[e.verdict] || SEVERITY.monitoring
                    )}
                    title={e.failing?.length ? `Failing: ${e.failing.join(", ")}` : "All gates passed"}
                  >
                    {VERDICT_LABEL[e.verdict] || e.verdict}
                  </span>
                </div>

                {e.failing?.length > 0 && (
                  <p className={cx("mt-1.5 text-[11px] leading-snug", CONSOLE.faint)}>
                    {e.failing.slice(0, 2).join(" · ")}
                    {e.failing.length > 2 ? ` +${e.failing.length - 2}` : ""}
                  </p>
                )}
              </div>
            );
          })}
        </div>
      )}
    </Panel>
  );
}
