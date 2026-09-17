import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import {
  FiUserCheck, FiRefreshCw, FiUsers, FiCalendar, FiTrendingUp, FiDownload,
} from "react-icons/fi";
import api from "../../api";
import useApi from "../../hooks/useApi";
import { CONSOLE, cx, type } from "../../ui/tokens";
import { ConsoleButton } from "../../ui/Button";
import Badge from "../../ui/Badge";
import DataTable from "../../components/admin/DataTable";
import Panel from "../../components/admin/Panel";
import StatCard from "../../components/admin/StatCard";
import FactGrid from "../../components/organization/FactGrid";
import OrganizationPageHeader from "../../components/organization/OrganizationPageHeader";
import OrganizationErrorState from "../../components/organization/OrganizationErrorState";
import { fmtDateTime, visLabel } from "../../data/events";
import { downloadCsv } from "../../utils/export";

// Audience Access — who has registered, who may watch, and how full each event is.
//
// Registration is the only audience fact this platform stores per person, and it is stored per
// EVENT, so the page is organised that way: capacity and gating per event, with the attendee
// analytics the analytics service does produce alongside it.
//
// The per-event registration counts come from GET /events, which now resolves them for the
// whole page in one grouped query over event_registrations (crud/event.registration_counts).
// The comment here used to claim that was already true; it was not — no `registered_count`
// existed anywhere in the backend, so every event printed an em dash and the Registrations
// KPI printed one too, for an organization that really did have registrations.
//
// The KPI row does NOT add up the rows on screen. It reads GET /organization/audience-summary,
// which counts over the whole selected window in SQL — otherwise the cards would describe the
// current page ("3 events") rather than the organization.
//
// The Attendance panel on the right reads GET /organization/audience-attendance
// (services/org.py::audience_attendance). Not every figure there is a true measurement —
// see that function's own docstring — `unique_attendees_estimated`/`avg_watch_minutes_estimated`
// flag which ones are derived from 15s concurrent-viewer sampling rather than counted, and
// `show_rate_basis` says the show-rate is private/invited events only. The panel below
// renders those caveats rather than hiding them, same as the "Not measured" panel beside it.
const RANGES = [
  ["7d", "7 days"],
  ["30d", "30 days"],
  ["90d", "90 days"],
];

const fillTone = (pct) => {
  if (pct == null) return "neutral";
  if (pct >= 95) return "danger";
  if (pct >= 80) return "warning";
  return "success";
};

// Days per range key, so the events list can be filtered by the SAME window the KPI row and
// the attendance panel use. The range buttons used to reload only the attendance panel, which
// left the table and the four cards showing every event regardless of the selection.
const RANGE_DAYS = { "7d": 7, "30d": 30, "90d": 90 };
const PAGE_SIZE = 20;               // inside the API's le=100 cap

// DataTable names a column by the row field it renders; the API names sortable fields in its
// own vocabulary (crud/event._EVENT_SORTS) and falls back to created_at for anything else.
// Sending a column key straight through would silently sort by creation date while the header
// claimed otherwise — so only mapped columns are marked sortable.
const SORT_FIELD = {
  title: "title",
  visibility: "visibility",
  registered: "registered",
  pct: "registration_limit",
};

export default function AudienceAccess() {
  const [range, setRange] = useState("30d");
  const [qInput, setQInput] = useState("");
  const [q, setQ] = useState("");
  const [page, setPage] = useState(1);
  const [sort, setSort] = useState({ key: "start_time", dir: "desc" });
  const navigate = useNavigate();

  // Debounced, so a request isn't fired per keystroke — same shape the Members and Identity
  // & Access consoles use.
  useEffect(() => {
    const t = setTimeout(() => setQ(qInput.trim()), 300);
    return () => clearTimeout(t);
  }, [qInput]);

  const sinceIso = useMemo(() => {
    const d = new Date();
    d.setDate(d.getDate() - (RANGE_DAYS[range] ?? 30));
    return d.toISOString();
  }, [range]);

  // Server-side throughout: the window, the search and the page are all decided by the API,
  // so the table is never a client-side slice of an unbounded fetch.
  const events = useApi(() =>
    api
      .get("/events", {
        params: {
          page,
          page_size: PAGE_SIZE,
          q: q || undefined,
          date_from: sinceIso,
          sort_by: (sort && SORT_FIELD[sort.key]) || "start_time",
          order: sort?.dir || "desc",
        },
      })
      .then((r) => r.data)
  );
  // Dataset-wide counters for the KPI row, counted in SQL over the same window.
  const summary = useApi(() =>
    api.get("/organization/audience-summary", { params: { range } }).then((r) => r.data)
  );
  const attendance = useApi(() =>
    api.get("/organization/audience-attendance", { params: { range } }).then((r) => r.data)
  );
  const a = attendance.data || {};

  // useApi fetches on mount and on reload() only, so every server-decided input refetches
  // explicitly. Guarded-render refetch is this repo's idiom in place of an effect.
  const inputs = `${range}|${q}|${page}|${sort?.key}|${sort?.dir}`;
  const [lastInputs, setLastInputs] = useState(inputs);
  if (inputs !== lastInputs) {
    setLastInputs(inputs);
    events.reload();
    if (!lastInputs.startsWith(range)) { summary.reload(); attendance.reload(); }
  }

  // Narrowing the result must return to its first page.
  const resetPage = (apply) => { setPage(1); apply(); };

  const rows = useMemo(() => {
    const list = Array.isArray(events.data) ? events.data : events.data?.items || [];
    return list.map((ev) => {
      // ?? not ||: a counted 0 is a real answer and must not fall through to null. Only a
      // response that omits the field entirely (an older API build) is "unknown".
      const registered = ev.registered_count ?? null;
      const limit = ev.registration_limit ?? null;
      const pct = registered != null && limit ? Math.round((registered / limit) * 100) : null;
      return { ...ev, registered, limit, pct };
    });
  }, [events.data]);

  const total = events.data?.total ?? 0;
  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));

  // Counted server-side over the whole window. `null` when the summary call failed — the
  // cards then render an em dash rather than a zero, because a failed request is not a
  // measurement of nothing.
  const s = summary.data;
  const totals = {
    events: s ? s.events : null,
    gated: s ? s.registration_required : null,
    registrations: s ? s.registrations : null,
    atCapacity: s ? s.at_capacity : null,
    capped: s ? s.events_with_capacity : null,
  };
  const UNKNOWN = "—";
  const kpi = (v) => (v == null ? UNKNOWN : v);


  const columns = [
    {
      key: "title",
      header: "Event",
      sortable: true,
      render: (ev) => (
        <div className="min-w-0">
          <p className={cx("truncate text-[13px] font-semibold", CONSOLE.heading)}>
            {ev.title || "Untitled event"}
          </p>
          <p className={cx("truncate text-[11px]", CONSOLE.faint)}>
            {ev.start_time ? fmtDateTime(ev.start_time) : "Unscheduled"}
          </p>
        </div>
      ),
    },
    {
      key: "visibility",
      header: "Audience",
      sortable: true,
      render: (ev) => <Badge tone="brand">{visLabel(ev.visibility)}</Badge>,
    },
    {
      // Not sortable: the API has no ordering for this flag, and a header that reordered
      // nothing (or reordered by creation date) would be worse than no header control.
      key: "registration_required",
      header: "Registration",
      render: (ev) =>
        ev.registration_required ? (
          <Badge tone="info">Required</Badge>
        ) : (
          <span className={cx("text-[12px]", CONSOLE.faint)}>Open</span>
        ),
    },
    {
      key: "registered",
      header: "Registered",
      align: "right",
      sortable: true,
      sortValue: (ev) => ev.registered ?? -1,
      render: (ev) => (
        <span className={cx("text-[13px]", type.mono, ev.registered ? CONSOLE.heading : CONSOLE.faint)}>
          {ev.registered ?? "—"}
          {ev.limit ? <span className={CONSOLE.faint}> / {ev.limit}</span> : null}
        </span>
      ),
    },
    {
      key: "pct",
      header: "Capacity",
      align: "right",
      sortable: true,
      sortValue: (ev) => ev.pct ?? -1,
      render: (ev) =>
        ev.limit == null ? (
          // Genuinely uncapped: registration_limit is NULL on the event. Not a stand-in for
          // a figure we failed to fetch — an event with a limit always shows the number.
          <span className={cx("text-[12px]", CONSOLE.faint)} title="No capacity limit set">
            Uncapped
          </span>
        ) : ev.pct == null ? (
          // Capped, but this response did not carry a registered count, so the fill is
          // unknown. The configured capacity is still a fact and is still shown.
          <span className={cx("text-[13px]", type.mono, CONSOLE.heading)}>{ev.limit}</span>
        ) : (
          <span className="inline-flex items-center gap-2">
            {/* Bar carries the same verdict as the number, for anyone scanning rather than
                reading. aria-hidden because the adjacent text already states it. */}
            <span
              className="h-1.5 w-16 overflow-hidden rounded-full bg-slate-200 dark:bg-white/10"
              aria-hidden="true"
            >
              <span
                className={cx(
                  "block h-full rounded-full",
                  ev.pct >= 95 ? "bg-rose-500" : ev.pct >= 80 ? "bg-amber-500" : "bg-green-500"
                )}
                style={{ width: `${Math.min(ev.pct, 100)}%` }}
              />
            </span>
            <Badge tone={fillTone(ev.pct)} size="sm">
              {ev.pct}%
            </Badge>
            {/* The configured capacity itself, so the column answers "how many?" and not
                only "how full?". */}
            <span className={cx("text-[12px]", type.mono, CONSOLE.faint)}>of {ev.limit}</span>
          </span>
        ),
    },
  ];

  const exportRows = () =>
    downloadCsv("audience-access.csv", rows, [
      ["Event", (r) => r.title || ""],
      ["Starts", (r) => r.start_time || ""],
      ["Visibility", (r) => visLabel(r.visibility)],
      ["Registration required", (r) => (r.registration_required ? "yes" : "no")],
      ["Registered", (r) => r.registered ?? ""],
      ["Capacity", (r) => r.limit ?? ""],
    ]);

  return (
    <div className="space-y-6">
      <OrganizationPageHeader
        title="Audience Access"
        subtitle="Who may attend each event, how many have registered, and how close each one is to capacity."
        actions={
          <>
            <div
              className={cx("inline-flex rounded-lg p-0.5", CONSOLE.segment)}
              role="group"
              aria-label="Analytics range"
            >
              {RANGES.map(([value, label]) => (
                <button
                  key={value}
                  type="button"
                  onClick={() => resetPage(() => setRange(value))}
                  aria-pressed={range === value}
                  className={cx(
                    "rounded-md px-2.5 py-1 text-[12px] font-medium transition-colors duration-150 motion-reduce:transition-none",
                    range === value ? CONSOLE.segmentOn : CONSOLE.segmentOff
                  )}
                >
                  {label}
                </button>
              ))}
            </div>
            <ConsoleButton
              variant="secondary"
              leftIcon={FiDownload}
              onClick={exportRows}
              disabled={rows.length === 0}
            >
              Export
            </ConsoleButton>
            <ConsoleButton
              variant="secondary"
              leftIcon={FiRefreshCw}
              onClick={events.reload}
            >
              Refresh
            </ConsoleButton>
          </>
        }
      />

      {events.error && (
        <OrganizationErrorState error={events.error} onRetry={events.reload} title="Couldn't load events" />
      )}

      {summary.error && (
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-amber-200 bg-amber-50 px-4 py-2.5 text-sm text-amber-800 dark:border-amber-500/25 dark:bg-amber-500/10 dark:text-amber-200">
          {/* A failed summary must not read as "zero registrations". */}
          <span>Couldn&apos;t load the audience totals. The table below is unaffected.</span>
          <ConsoleButton size="sm" variant="secondary" onClick={summary.reload}>Retry</ConsoleButton>
        </div>
      )}

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {/* All four are counted in SQL across the selected window — not summed from the page
            of rows below, which would describe the page instead of the organization. */}
        <StatCard label="Events" value={kpi(totals.events)} loading={summary.loading} />
        <StatCard label="Registration required" value={kpi(totals.gated)} loading={summary.loading} />
        <StatCard label="Registrations" value={kpi(totals.registrations)} loading={summary.loading} />
        <StatCard
          label="At capacity"
          value={kpi(totals.atCapacity)}
          loading={summary.loading}
          // 0 of 0 capped events is a different fact from 0 of 12, and the card alone cannot
          // tell them apart. Only shown once the count is actually known.
          hint={totals.capped === 0 ? "No event has a capacity set" : undefined}
        />
      </div>

      {/* The "Playback gates →" action that sat here pointed at /organization/playback, which
          was deliberately removed from the Organization rail. Leaving a link into a surface
          the console no longer presents as its own would reintroduce it through a side door.
          The ROUTE and the page are untouched — this is only the entry point. */}
      <Panel
          title="Access by event"
          // No `count`: Panel renders it inline with the title, and the table's own
          // "Showing 1–20 of N" footer already states the total — from the same number.
          flush
        >
          <div className="px-4 pt-3">
            <input
              value={qInput}
              onChange={(e) => resetPage(() => setQInput(e.target.value))}
              placeholder="Search events…"
              aria-label="Search events"
              className={CONSOLE.search}
            />
          </div>
          <DataTable
            columns={columns}
            rows={rows}
            rowKey={(ev) => ev.id}
            loading={events.loading}
            minWidth={760}
            // Server-driven: DataTable's own search/sort/paging would only ever act on the
            // page the API already returned, which is how "oldest" comes to mean "oldest of
            // the twenty on screen".
            pageSize={PAGE_SIZE}
            serverSort={sort}
            onSortChange={(next) => resetPage(() => setSort(next || { key: "start_time", dir: "desc" }))}
            serverPage={page}
            serverPageCount={pageCount}
            serverTotal={total}
            onPageChange={setPage}
            onRowClick={(ev) => navigate(`/organization/events/${ev.id}`)}
            empty={{
              icon: FiUserCheck,
              title: "No events yet",
              description:
                "Audience access is defined per event. Create one to set its visibility, registration rule and capacity.",
              action: (
                <ConsoleButton href="/organization/events" size="sm">
                  Go to Live Events
                </ConsoleButton>
              ),
            }}
          />
      </Panel>

      <Panel title="Attendance" eyebrow={`Last ${range}`}>
        <FactGrid
          columns={4}
          facts={[
            {
              // NOT "Unique attendees": the figure is claimed private-registration emails
              // plus the sum of each event's PEAK concurrency, so one person at two events
              // counts twice. The footnote below already said the method was estimated
              // while the label still claimed uniqueness — this is the same correction
              // analytics() made when "Total Viewers" became peak_viewers_summed.
              label: "Estimated attendees",
              value: attendance.loading ? null : a.unique_attendees ?? null,
              reason: "Loading",
            },
            {
              label: "Returning attendees",
              value: attendance.loading ? null : a.returning ?? null,
              reason: "Loading",
            },
            {
              label: "Average watch time",
              value:
                attendance.loading || a.avg_watch_minutes == null ? null : `${a.avg_watch_minutes}m`,
              reason: "No sampled viewer data for this window",
            },
            {
              label: "Registration → attendance",
              value: attendance.loading || a.show_rate == null ? null : `${a.show_rate}%`,
              reason: "No private-event registrations in this window",
            },
          ]}
        />
        {/* Honest, not hidden: unique_attendees/avg_watch_minutes are derived from 15s
            concurrent-viewer sampling, not counted per person — see
            services/org.py::audience_attendance's docstring for exactly why no true
            per-person duration exists in this stack. */}
        {!attendance.loading && (a.unique_attendees_estimated || a.avg_watch_minutes_estimated) && (
          <p className={cx("mt-4 border-t pt-3 text-[12px] leading-relaxed", CONSOLE.divider, CONSOLE.faint)}>
            Attendees and watch time are estimated from sampled concurrent viewers, not counted
            per person. Show rate only covers private, invite-only events.
          </p>
        )}
      </Panel>

      <Panel title="Not measured">
        <FactGrid
          columns={3}
          facts={[
            { label: "Audience geography", value: null, reason: "Per-viewer location is not collected" },
            { label: "Device and player mix", value: null, reason: "Client-side playback telemetry is not ingested" },
            { label: "Blocked join attempts", value: null, reason: "Refused playback attempts are not recorded" },
          ]}
        />
        <p className={cx("mt-4 border-t pt-3 text-[12px] leading-snug", CONSOLE.divider, CONSOLE.faint)}>
          Deliberate: this platform records registration and presence, not per-person
          profiling. These stay “—” until something is actually collected.
        </p>
      </Panel>

      <Panel title="Controls that shape the audience">
        {/* Two columns INSIDE the section — a grid where it helps reading, which is a
            different thing from the page-level rail this replaced. */}
        <ul className="grid gap-1 sm:grid-cols-2">
          {[
            [FiUsers, "Members & Access", "/organization/users", "Who inside the organization can operate events."],
            // Playback & Access is not listed: it was removed from the Organization rail, and
            // the gates it reads are SET on the event, which is where "Live Events" below
            // already points. Its route and page are untouched.
            [FiCalendar, "Live Events", "/organization/events", "Capacity, schedule and the watch window."],
            [FiTrendingUp, "Analytics", "/organization/analytics", "Engagement once they are in the room."],
          ].map(([Icon, label, to, desc]) => (
            <li key={to}>
              <Link
                to={to}
                className={cx(
                  "flex items-start gap-2.5 rounded-lg px-2 py-2 transition-colors duration-150 motion-reduce:transition-none",
                  "hover:bg-slate-100 dark:hover:bg-white/[0.06]"
                )}
              >
                <Icon className={cx("mt-0.5 shrink-0 text-[14px]", CONSOLE.faint)} aria-hidden="true" />
                <span className="min-w-0">
                  <span className={cx("block text-[13px] font-semibold", CONSOLE.heading)}>{label}</span>
                  <span className={cx("block text-[12px]", CONSOLE.faint)}>{desc}</span>
                </span>
              </Link>
            </li>
          ))}
        </ul>
      </Panel>
    </div>
  );
}
