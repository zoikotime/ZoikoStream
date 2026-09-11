import { Link } from "react-router-dom";
import { FiCalendar } from "react-icons/fi";
import { CONSOLE, cx, focusRing } from "../../../ui/tokens";
import DashboardPanel from "./DashboardPanel";
import Badge from "../../../ui/Badge";
import { ConsoleButton } from "../../../ui/Button";
import { fmtDateTime, statusMeta, visLabel } from "../../../data/events";
import EmptyState from "./EmptyState";

// A stable colour block per event, so a list of events is scannable by shape as well as by
// title. Derived from the id, NOT from the row's position — otherwise every event's tile
// would change colour as soon as one was added above it.
const GRADIENTS = [
  "from-violet-500 to-indigo-600",
  "from-blue-500 to-cyan-600",
  "from-emerald-500 to-teal-600",
  "from-amber-500 to-orange-600",
  "from-rose-500 to-pink-600",
  "from-slate-600 to-slate-800",
];

function gradientFor(id = "") {
  let sum = 0;
  for (let i = 0; i < id.length; i += 1) sum += id.charCodeAt(i);
  return GRADIENTS[sum % GRADIENTS.length];
}

const initialsOf = (title = "") =>
  title.trim().split(/\s+/).slice(0, 2).map((w) => w[0] || "").join("").toUpperCase() || "EV";

function EventRow({ event }) {
  const status = statusMeta(event.status);
  return (
    <li className="flex items-center gap-3.5 px-6 py-3.5">
      {/* The event's own thumbnail when it has one; a deterministic colour block when it
          doesn't, rather than a grey placeholder that makes every row look unfinished. */}
      {event.thumbnail ? (
        <img
          src={event.thumbnail}
          alt=""
          className="h-11 w-11 shrink-0 rounded-lg object-cover"
        />
      ) : (
        <span
          aria-hidden="true"
          className={cx(
            "grid h-11 w-11 shrink-0 place-items-center rounded-lg bg-gradient-to-br text-[12px] font-bold text-white",
            gradientFor(event.id)
          )}
        >
          {initialsOf(event.title)}
        </span>
      )}

      <div className="min-w-0 flex-1">
        <p className={cx("truncate text-[14px] font-semibold", CONSOLE.heading)}>
          {event.title || "Untitled event"}
        </p>
        <p className={cx("mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-[13px]", CONSOLE.faint)}>
          <span>{fmtDateTime(event.start_time)}</span>
          <span aria-hidden="true">·</span>
          <span>{visLabel(event.visibility)}</span>
        </p>
      </div>

      <div className="flex shrink-0 flex-col items-end gap-1">
        <Badge tone={status.tone} dot={status.pulse}>
          {status.label}
        </Badge>
        {/* `expected_audience` is the organizer's own stated figure on the event — it is NOT
            a registration count, and there is no registered/attendee total on this payload,
            so it is labelled for what it is rather than dressed up as one. */}
        {event.expected_audience != null && (
          <span className="text-right leading-tight">
            <span className={cx("block text-[15px] font-semibold tabular-nums", CONSOLE.heading)}>
              {event.expected_audience.toLocaleString()}
            </span>
            <span className={cx("block text-[11px]", CONSOLE.faint)}>Expected</span>
          </span>
        )}
      </div>
    </li>
  );
}

export default function UpcomingEvents({ events = [], total = null }) {
  // `total` is the server's own count of everything upcoming; `events` is only the page of
  // them this panel shows. Saying so is the difference between a list that looks complete
  // and one the reader knows to click through.
  const more = total != null && total > events.length ? total - events.length : 0;

  return (
    <DashboardPanel
      title="Upcoming Events"
      action={
        <Link
          to="/organization/events"
          className={cx(
            "rounded-lg border border-slate-200 px-3 py-1.5 text-[13px] font-medium",
            "text-slate-600 hover:bg-slate-50 hover:text-slate-900",
            "dark:border-white/[0.14] dark:text-neutral-300 dark:hover:bg-white/[0.06] dark:hover:text-white",
            focusRing
          )}
        >
          View all
        </Link>
      }
      bodyClass="pb-2"
    >
      {events.length === 0 ? (
        // Compact on purpose. A full-height empty state in a panel this wide reads as a
        // broken page; a short one reads as "nothing scheduled yet, here is the way in".
        <EmptyState
          icon={FiCalendar}
          title="No upcoming events"
          description="Schedule an event and it will appear here."
          className="px-6 py-8"
          action={
            <ConsoleButton href="/organization/events" variant="secondary" size="sm">
              Go to events
            </ConsoleButton>
          }
        />
      ) : (
        <>
          <ul className={cx("divide-y", CONSOLE.divideY)}>
            {events.map((e) => (
              <EventRow key={e.id} event={e} />
            ))}
          </ul>
          {more > 0 && (
            <div className={cx("border-t px-6 py-3", CONSOLE.divider)}>
              <Link
                to="/organization/events"
                className={cx("text-[13px] font-medium", CONSOLE.link, focusRing)}
              >
                {more} more upcoming {more === 1 ? "event" : "events"} →
              </Link>
            </div>
          )}
        </>
      )}
    </DashboardPanel>
  );
}
