import { Link } from "react-router-dom";
import { CONSOLE, cx, type } from "../../ui/tokens";
import Panel from "../admin/Panel";

// Upcoming events with their readiness state. The state is computed from the event's own
// configuration by the same gates the platform console evaluates, so the two views can never
// disagree about whether an event is ready to go ahead.
//
// "Not started" is distinct from "Blocked": an event nobody has begun setting up is not the
// same problem as one that is set up and failing a required gate.
const STATE = {
  passed: { label: "Ready", cls: "text-green-600 dark:text-green-400" },
  in_progress: { label: "In progress", cls: "text-blue-600 dark:text-blue-400" },
  conditional: { label: "Conditional", cls: "text-amber-600 dark:text-amber-400" },
  blocked: { label: "Blocked", cls: "text-rose-600 dark:text-rose-400" },
  not_started: { label: "Not started", cls: CONSOLE.faint },
};

// Start time in the event's own timezone — a schedule is read where the audience is.
function when(iso, timezone) {
  if (!iso) return "Not scheduled";
  const d = new Date(iso);
  try {
    return new Intl.DateTimeFormat("en-GB", {
      weekday: "short", day: "numeric", month: "short",
      hour: "2-digit", minute: "2-digit", hour12: false,
      timeZone: timezone || undefined, timeZoneName: "short",
    }).format(d);
  } catch {
    return d.toLocaleString();
  }
}

export default function ManagedEvents({ events = [] }) {
  return (
    <Panel
      title="Managed live events"
      count={events.filter((e) => e.readiness_state === "blocked").length}
      action={
        <Link to="/organization/events" className={cx("text-[12px] font-semibold", CONSOLE.link)}>
          Live Events →
        </Link>
      }
      flush
    >
      {events.length === 0 ? (
        <div className="px-5 py-10 text-center">
          <p className={cx("text-[13px] font-medium", CONSOLE.body)}>No upcoming events</p>
          <p className={cx("mt-1 text-[12px]", CONSOLE.faint)}>
            Scheduled and published events appear here with their readiness state.
          </p>
        </div>
      ) : (
        <ul className={cx("divide-y", CONSOLE.divideY)}>
          {events.map((e) => {
            const st = STATE[e.readiness_state] || STATE.not_started;
            return (
              <li key={e.id} className="flex items-start justify-between gap-3 px-4 py-3 sm:px-5">
                <div className="min-w-0 flex-1">
                  <Link
                    to={`/organization/events/${e.id}`}
                    className={cx("block truncate text-[13px] font-semibold hover:underline", CONSOLE.heading)}
                  >
                    {e.title}
                  </Link>
                  <p className={cx("mt-0.5 truncate text-[11px]", type.mono, CONSOLE.faint)}>
                    {when(e.start_time, e.timezone)}
                    {e.readiness_state === "not_started" && " · readiness not started"}
                    {e.failing?.length > 0 && e.readiness_state !== "not_started" &&
                      ` · ${e.failing.length} gate${e.failing.length === 1 ? "" : "s"} outstanding`}
                  </p>
                </div>
                <span
                  className={cx("shrink-0 text-[12px] font-semibold", st.cls)}
                  title={e.failing?.length ? `Outstanding: ${e.failing.join(", ")}` : "All gates passed"}
                >
                  {st.label}
                </span>
              </li>
            );
          })}
        </ul>
      )}
    </Panel>
  );
}
