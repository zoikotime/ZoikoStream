import { Link } from "react-router-dom";
import { FiAlertTriangle, FiClock, FiRadio, FiUsers } from "react-icons/fi";
import { CONSOLE, cx, focusRing, type } from "../../ui/tokens";
import { compact } from "../admin/format";

// What is happening in this organization RIGHT NOW — the band at the top of the Overview.
//
// It replaced the media lifecycle rail, which reported platform stage availability. That rail
// was real but degenerate (with no Incident rows every stage reads exactly 100.00% forever) and
// it answered the super admin's question, not the org admin's — and it answered it for the third
// time on one screen, after the topbar's health pill and the Service health tile. Platform health
// for an org still lives on Support & Status, where the same component is mounted.
//
// Every figure here comes from `sessions` and `attention` in the payload the page already
// fetches: no new endpoint, no derived guesses. A cell with nothing to report says so rather
// than showing a zero that reads like a measurement.

// tone: how the value should read when it is non-zero. `live` pulses, because something being
// on air is the one state on this page that changes under you.
const CELLS = [
  {
    key: "live",
    label: "On air now",
    icon: FiRadio,
    to: "/organization/sessions",
    value: (s) => s.sessions.live || 0,
    note: (s) =>
      s.sessions.live
        ? `${s.sessions.paused ? `${s.sessions.paused} paused · ` : ""}${
            s.sessions.current_audience != null
              ? `${compact(s.sessions.current_audience)} watching`
              : "audience not sampled yet"
          }`
        : "Nothing broadcasting",
    tone: "live",
  },
  {
    key: "starting",
    label: "Starting soon",
    icon: FiClock,
    to: "/organization/events",
    value: (s) => s.sessions.starting_soon || 0,
    note: (s) => (s.sessions.starting_soon ? "Scheduled within 30 minutes" : "Nothing in the next 30 minutes"),
    tone: "warn",
  },
  {
    key: "audience",
    label: "Audience",
    icon: FiUsers,
    to: "/organization/analytics",
    // Current watchers against the all-time peak: the pairing is the point, so one cell
    // carries both rather than two cells carrying half an answer each.
    value: (s) => (s.sessions.current_audience != null ? compact(s.sessions.current_audience) : null),
    // peak_audience is now the WINDOWED peak (the range control moves it), so the "all time"
    // claim has to read the all-time field or it would mislabel a 24-hour figure as a record.
    note: (s) =>
      s.sessions.peak_audience_all_time != null
        ? `Peak ${compact(s.sessions.peak_audience_all_time)} all time`
        : "No audience recorded yet",
    tone: "neutral",
  },
  {
    key: "attention",
    label: "Needs attention",
    icon: FiAlertTriangle,
    to: "/organization/dashboard",
    value: (s) => s.attention.length,
    note: (s) => {
      if (!s.attention.length) return "Nothing waiting on you";
      const critical = s.attention.filter((a) => a.severity === "critical").length;
      return critical ? `${critical} critical · act first` : "Review when you can";
    },
    tone: "danger",
  },
];

const TONE = {
  live: "text-green-600 dark:text-green-400",
  warn: "text-amber-600 dark:text-amber-400",
  danger: "text-rose-600 dark:text-rose-400",
  neutral: CONSOLE.heading,
};

export default function LiveStatusBand({ sessions, attention = [], age }) {
  const state = { sessions: sessions || {}, attention };

  return (
    <section className={cx(CONSOLE.panel, "overflow-hidden")}>
      <div className="flex items-center justify-between gap-3 px-4 pt-3.5 sm:px-5">
        <p className={cx("text-[10px] font-semibold uppercase tracking-[0.14em]", type.mono, CONSOLE.faint)}>
          Right now
        </p>
        {age != null && <span className={cx("text-[11px]", type.mono, CONSOLE.faint)}>{age}</span>}
      </div>

      <div className={cx("grid grid-cols-2 gap-px pb-1 lg:grid-cols-4", CONSOLE.segment)}>
        {CELLS.map((cell) => {
          const raw = cell.value(state);
          // 0 is a real answer here ("nothing on air"), so only null means unmeasured.
          const hasValue = raw != null;
          const active = hasValue && raw !== 0;
          return (
            <Link
              key={cell.key}
              to={cell.to}
              className={cx(
                "group flex items-start gap-3 bg-white px-4 py-4 transition-colors duration-150 hover:bg-slate-50 motion-reduce:transition-none sm:px-5 dark:bg-black dark:hover:bg-white/[0.06]",
                focusRing
              )}
            >
              <span
                aria-hidden="true"
                className={cx(
                  "mt-0.5 grid h-8 w-8 shrink-0 place-items-center rounded-lg transition-colors duration-150",
                  active
                    ? "bg-violet-100 text-violet-700 dark:bg-violet-500/20 dark:text-violet-300"
                    : cx(CONSOLE.segment, CONSOLE.faint)
                )}
              >
                <cell.icon className="text-[15px]" />
              </span>

              <span className="min-w-0">
                <span className={cx("block text-[12px] font-medium", CONSOLE.muted)}>{cell.label}</span>
                <span className="mt-0.5 flex items-baseline gap-1.5">
                  <span
                    className={cx(
                      "text-[26px] font-semibold leading-none tracking-tight tabular-nums",
                      hasValue ? (active ? TONE[cell.tone] : CONSOLE.heading) : CONSOLE.faint
                    )}
                  >
                    {hasValue ? raw : "—"}
                  </span>
                  {/* A live broadcast is the one thing here that moves on its own. */}
                  {cell.key === "live" && active && (
                    <span className="relative flex h-2 w-2" aria-hidden="true">
                      <span className="zk-pulse-ring absolute inline-flex h-full w-full rounded-full bg-green-500" />
                      <span className="relative inline-flex h-2 w-2 rounded-full bg-green-500" />
                    </span>
                  )}
                </span>
                <span className={cx("mt-1 block text-[11px] leading-snug", CONSOLE.faint)}>{cell.note(state)}</span>
              </span>
            </Link>
          );
        })}
      </div>
    </section>
  );
}
