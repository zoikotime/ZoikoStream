import { useEffect, useMemo, useState } from "react";
import { FiAlertTriangle, FiChevronLeft, FiChevronRight } from "react-icons/fi";
import api from "../../api";
import OrganizationErrorState from "../../components/organization/OrganizationErrorState";
import { ConsoleButton as Button } from "../../ui/Button";
import { CONSOLE, cx, focusRing, panelSurface } from "../../ui/tokens";
import { displayStatusOf, statusMeta } from "../../data/events";
import { dayKey, monthKey, overlapping, parseMonth } from "../../data/eventCalendar";

// Events › Calendar (ZST-UX-NAV-001 §5, events.calendar: "Calendar and scheduling conflicts").
// One month of the organization's events, placed on the day they start in YOUR zone, with
// events in valid, overlapping scheduled intervals marked. The badge counts distinct involved
// events, not pairs. The month is in the URL (?month=YYYY-MM) so it survives Back and a reload.
//
// Read-only: a day shows what is scheduled; opening an event goes to its workspace.

const MAX = 100; // /events page_size ceiling — past it the calendar says so rather than hide rows
const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const DOT = {
  brand: "bg-violet-500", info: "bg-blue-500", warning: "bg-amber-500",
  danger: "bg-rose-500", success: "bg-green-500", neutral: "bg-slate-400",
};

export default function EventsCalendar({ month: monthParam, onMonth, onOpen }) {
  const month = parseMonth(monthParam);
  const key = monthKey(month);
  const [state, setState] = useState({ key: null, data: null, error: null });
  const [tick, setTick] = useState(0);

  useEffect(() => {
    let alive = true;
    const from = new Date(month.getFullYear(), month.getMonth(), 1);
    const to = new Date(month.getFullYear(), month.getMonth() + 1, 1);
    api.get("/events", {
      params: {
        date_from: from.toISOString(), date_to: to.toISOString(),
        sort_by: "start_time", order: "asc", page_size: MAX,
      },
    })
      .then((r) => alive && setState({ key, data: r.data, error: null }))
      .catch((e) => alive && setState({ key, data: null, error: e }));
    return () => {
      alive = false;
    };
    // `month` is derived from `key`; the key is the dependency.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, tick]);

  const items = useMemo(() => (state.key === key ? state.data?.items || [] : []), [state, key]);
  const clash = useMemo(() => overlapping(items), [items]);
  const byDay = useMemo(() => {
    const map = {};
    for (const e of items) {
      if (!e.start_time) continue;
      const k = dayKey(new Date(e.start_time));
      (map[k] ||= []).push(e);
    }
    return map;
  }, [items]);

  // Monday-first grid covering the whole month.
  const first = new Date(month.getFullYear(), month.getMonth(), 1);
  const lead = (first.getDay() + 6) % 7;
  const daysInMonth = new Date(month.getFullYear(), month.getMonth() + 1, 0).getDate();
  const cells = Array.from({ length: Math.ceil((lead + daysInMonth) / 7) * 7 }, (_, i) => {
    const day = i - lead + 1;
    return day >= 1 && day <= daysInMonth ? new Date(month.getFullYear(), month.getMonth(), day) : null;
  });
  const today = dayKey(new Date());
  const label = month.toLocaleDateString("en-US", { month: "long", year: "numeric" });
  const shift = (n) => onMonth(monthKey(new Date(month.getFullYear(), month.getMonth() + n, 1)));
  const loading = state.key !== key;

  if (state.error && state.key === key) {
    return <OrganizationErrorState error={state.error} onRetry={() => setTick((t) => t + 1)} title="Couldn't load the calendar" />;
  }

  return (
    <section aria-label={`Event calendar, ${label}`} className={cx(panelSurface, "overflow-hidden")}>
      <div className={cx("flex flex-wrap items-center justify-between gap-3 border-b px-4 py-3", CONSOLE.divider)}>
        <div className="flex items-center gap-2">
          <Button variant="secondary" size="sm" iconOnly leftIcon={FiChevronLeft} aria-label="Previous month" onClick={() => shift(-1)} />
          <h2 className={cx("min-w-[10rem] text-center font-semibold", CONSOLE.heading)} aria-live="polite">{label}</h2>
          <Button variant="secondary" size="sm" iconOnly leftIcon={FiChevronRight} aria-label="Next month" onClick={() => shift(1)} />
          <Button variant="ghost" size="sm" onClick={() => onMonth("")}>Today</Button>
        </div>
        <p className={cx("text-[12px]", CONSOLE.faint)}>
          {loading
            ? "Loading…"
            : `${items.length}${state.data?.total > items.length ? ` of ${state.data.total}` : ""} event${items.length === 1 ? "" : "s"} · times in your zone`}
          {clash.size > 0 && (
            <span className="ml-2 inline-flex items-center gap-1 font-semibold text-amber-700 dark:text-amber-300">
              <FiAlertTriangle aria-hidden="true" /> {clash.size} overlapping events
            </span>
          )}
        </p>
      </div>
      <div className="overflow-x-auto">
        <div className="grid min-w-[720px] grid-cols-7">
          {WEEKDAYS.map((d) => (
            <div key={d} className={cx("border-b px-2 py-1.5 text-[11px] font-semibold uppercase tracking-wider", CONSOLE.divider, CONSOLE.faint)}>
              {d}
            </div>
          ))}
          {cells.map((date, i) => {
            const k = date ? dayKey(date) : `pad-${i}`;
            const list = date ? byDay[k] || [] : [];
            return (
              <div
                key={k}
                className={cx(
                  "min-h-[104px] border-b border-r p-1.5",
                  CONSOLE.divider,
                  !date && "bg-slate-50/60 dark:bg-white/[0.02]"
                )}
              >
                {date && (
                  <>
                    <p
                      className={cx(
                        "mb-1 inline-grid h-6 min-w-6 place-items-center rounded-full px-1 text-[12px] font-semibold",
                        k === today ? "bg-violet-600 text-white" : CONSOLE.muted
                      )}
                      aria-label={date.toLocaleDateString("en-US", { weekday: "long", month: "long", day: "numeric" })}
                    >
                      {date.getDate()}
                    </p>
                    <ul className="space-y-1">
                      {list.slice(0, 3).map((e) => {
                        const st = statusMeta(displayStatusOf(e));
                        const time = new Date(e.start_time).toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" });
                        return (
                          <li key={e.id}>
                            <button
                              type="button"
                              onClick={() => onOpen(e.id)}
                              title={`${e.title || "Untitled event"} · ${st.label}${clash.has(e.id) ? " · overlaps another event" : ""}`}
                              className={cx(
                                "flex w-full items-center gap-1.5 truncate rounded px-1.5 py-1 text-left text-[11px]",
                                clash.has(e.id)
                                  ? "bg-amber-50 ring-1 ring-amber-300 dark:bg-amber-500/10 dark:ring-amber-500/40"
                                  : "bg-slate-100 dark:bg-white/[0.06]",
                                "hover:bg-violet-50 dark:hover:bg-violet-500/15",
                                focusRing
                              )}
                            >
                              <span aria-hidden="true" className={cx("h-1.5 w-1.5 shrink-0 rounded-full", DOT[st.tone] || DOT.neutral)} />
                              <span className={cx("shrink-0 tabular-nums", CONSOLE.faint)}>{time}</span>
                              <span className={cx("truncate font-medium", CONSOLE.heading)}>{e.title || "Untitled event"}</span>
                              <span className="sr-only">
                                , {st.label}{clash.has(e.id) ? ", overlaps another event" : ""}
                              </span>
                            </button>
                          </li>
                        );
                      })}
                      {list.length > 3 && (
                        <li className={cx("px-1.5 text-[11px]", CONSOLE.faint)}>+{list.length - 3} more</li>
                      )}
                    </ul>
                  </>
                )}
              </div>
            );
          })}
        </div>
      </div>
    </section>
  );
}
