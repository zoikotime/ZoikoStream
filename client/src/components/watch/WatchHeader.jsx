// client/src/components/watch/WatchHeader.jsx
// Viewer Portal top section: event banner with title, host, live status and
// a live viewer count. Gradient keyed to the event accent (like the reg page).
import { FiCalendar, FiClock, FiUsers } from "react-icons/fi";
import { cx } from "../../ui/tokens";
import { fmtDate } from "../../data/events";
import { initials } from "../../data/watch";

// Literal gradient per accent — Tailwind JIT can't compile interpolated names.
const BANNER = {
  violet: "from-violet-600 via-indigo-700 to-slate-900",
  emerald: "from-emerald-600 via-teal-700 to-slate-900",
  blue: "from-blue-600 via-sky-700 to-slate-900",
  amber: "from-amber-500 via-orange-600 to-slate-900",
  indigo: "from-indigo-600 via-violet-700 to-slate-900",
  rose: "from-rose-600 via-pink-700 to-slate-900",
};

function StatusPill({ status }) {
  if (status === "Live")
    return (
      <span className="inline-flex items-center gap-1.5 rounded-full bg-rose-600 px-3 py-1 text-xs font-bold uppercase tracking-wide text-white">
        <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-white" /> Live
      </span>
    );
  if (status === "Completed")
    return <span className="rounded-full bg-white/15 px-3 py-1 text-xs font-semibold text-white backdrop-blur">Ended · Replay</span>;
  return <span className="rounded-full bg-white/15 px-3 py-1 text-xs font-semibold text-white backdrop-blur">Starting soon</span>;
}

export default function WatchHeader({ event, viewers }) {
  const live = event.status === "Live";

  return (
    <div className={cx("relative overflow-hidden bg-gradient-to-br", BANNER[event.accent] || BANNER.emerald)}>
      <div className="absolute inset-0 bg-[radial-gradient(circle_at_top_right,rgba(255,255,255,0.15),transparent_55%)]" />
      <div className="relative mx-auto max-w-7xl px-4 py-8 sm:px-6 sm:py-10">
        <div className="flex flex-wrap items-center gap-3">
          <StatusPill status={event.status} />
          <span className="rounded-full bg-white/15 px-3 py-1 text-xs font-semibold text-white backdrop-blur">{event.category}</span>
          {(live || event.viewers != null) && (
            <span className="inline-flex items-center gap-1.5 rounded-full bg-black/25 px-3 py-1 text-xs font-semibold text-white backdrop-blur">
              <FiUsers /> {viewers.toLocaleString()} {live ? "watching" : "views"}
            </span>
          )}
        </div>

        <h1 className="mt-4 max-w-4xl text-2xl font-bold tracking-tight text-white sm:text-4xl">{event.name}</h1>

        <div className="mt-4 flex flex-wrap items-center gap-x-6 gap-y-3 text-sm text-white/90">
          <span className="inline-flex items-center gap-2">
            <span className="grid h-8 w-8 place-items-center rounded-full bg-white/20 text-xs font-semibold text-white">
              {initials(event.host)}
            </span>
            Hosted by <span className="font-semibold text-white">{event.host}</span>
          </span>
          <span className="inline-flex items-center gap-2"><FiCalendar /> {fmtDate(event.date)}</span>
          <span className="inline-flex items-center gap-2"><FiClock /> {event.start}–{event.end}</span>
        </div>
      </div>
    </div>
  );
}
