import { useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import {
  FiUserCheck, FiRefreshCw, FiUsers, FiSlash, FiCalendar, FiTrendingUp, FiDownload,
} from "react-icons/fi";
import api from "../../api";
import useApi from "../../hooks/useApi";
import { CONSOLE, cx, type } from "../../ui/tokens";
import { ConsoleButton } from "../../ui/Button";
import Badge from "../../ui/Badge";
import DataTable from "../../components/admin/DataTable";
import Panel from "../../components/admin/Panel";
import StatCard from "../../components/admin/StatCard";
import StatRow from "../../components/admin/StatRow";
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
// The per-event registration counts on the left are REAL — they come from the events list
// itself (registered_count is part of the dashboard enrichment).
//
// The Attendance panel on the right is STATIC. It used to read /analytics/attendees, which no
// longer exists in this backend (there is no analytics router), so the call could only 404 and
// paint an error banner across a page whose main table works. Until an aggregate endpoint
// exists, these four figures are illustrative placeholders — see ATTENDANCE_SAMPLE below.
const RANGES = [
  ["7d", "7 days"],
  ["30d", "30 days"],
  ["90d", "90 days"],
];

// Illustrative attendance aggregates, one set per range. Static because no analytics endpoint
// produces them — the figures grow with the window and the show rate drifts down, so the
// numbers behave the way real ones would instead of being three copies of each other.
const ATTENDANCE_SAMPLE = {
  "7d": { unique_attendees: 412, returning: 168, avg_watch_minutes: 27, show_rate: 68 },
  "30d": { unique_attendees: 1846, returning: 731, avg_watch_minutes: 24, show_rate: 61 },
  "90d": { unique_attendees: 5093, returning: 2287, avg_watch_minutes: 22, show_rate: 57 },
};

const fillTone = (pct) => {
  if (pct == null) return "neutral";
  if (pct >= 95) return "danger";
  if (pct >= 80) return "warning";
  return "success";
};

export default function AudienceAccess() {
  const [range, setRange] = useState("30d");
  const navigate = useNavigate();

  const events = useApi(() =>
    api
      .get("/events", { params: { page: 1, page_size: 100, sort_by: "start_time", order: "desc" } })
      .then((r) => r.data)
  );
  // ponytail: static, per the note above — no analytics endpoint to call. Keyed by range so
  // the segmented control still changes something; swap for a fetch when one lands.
  const a = ATTENDANCE_SAMPLE[range] || ATTENDANCE_SAMPLE["30d"];

  const rows = useMemo(() => {
    const list = Array.isArray(events.data) ? events.data : events.data?.items || [];
    return list.map((ev) => {
      const registered = ev.registered_count ?? ev.registrations ?? null;
      const limit = ev.registration_limit ?? null;
      const pct = registered != null && limit ? Math.round((registered / limit) * 100) : null;
      return { ...ev, registered, limit, pct };
    });
  }, [events.data]);

  const totals = useMemo(() => {
    const withReg = rows.filter((r) => r.registered != null);
    return {
      events: rows.length,
      gated: rows.filter((r) => r.registration_required).length,
      registrations: withReg.length ? withReg.reduce((n, r) => n + r.registered, 0) : null,
      atCapacity: rows.filter((r) => r.pct != null && r.pct >= 100).length,
    };
  }, [rows]);


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
        ev.pct == null ? (
          <span className={cx("text-[12px]", CONSOLE.faint)} title="No capacity limit set">
            Uncapped
          </span>
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
                  onClick={() => setRange(value)}
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

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <StatCard label="Events" value={totals.events} loading={events.loading} />
        <StatCard label="Registration required" value={totals.gated} loading={events.loading} />
        <StatCard
          label="Registrations"
          value={totals.registrations ?? "—"}
          loading={events.loading}
        />
        <StatCard label="At capacity" value={totals.atCapacity} loading={events.loading} />
      </div>

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_320px]">
        <Panel
          title="Access by event"
          count={totals.atCapacity}
          action={
            <Link to="/organization/playback" className={cx("text-[12px] font-semibold", CONSOLE.link)}>
              Playback gates →
            </Link>
          }
          flush
        >
          <DataTable
            columns={columns}
            rows={rows}
            rowKey={(ev) => ev.id}
            loading={events.loading}
            searchable
            searchKeys={["title"]}
            searchPlaceholder="Search events…"
            pageSize={12}
            minWidth={760}
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

        <div className="space-y-4">
          <Panel title="Attendance" eyebrow={`Last ${range}`}>
            <StatRow
              label="Unique attendees"
              value={a.unique_attendees ?? a.total ?? null}
              reason="Not reported for this window"
            />
            <StatRow
              label="Returning attendees"
              value={a.returning ?? null}
              reason="Not reported for this window"
            />
            <StatRow
              label="Average watch time"
              value={a.avg_watch_minutes != null ? `${a.avg_watch_minutes}m` : null}
              reason="Not reported for this window"
            />
            <StatRow
              label="Registration → attendance"
              value={a.show_rate != null ? `${a.show_rate}%` : null}
              reason="Not reported for this window"
            />
          </Panel>

          <Panel title="Not measured">
            <StatRow
              label="Audience geography"
              value={null}
              reason="Per-viewer location is not collected"
            />
            <StatRow
              label="Device and player mix"
              value={null}
              reason="Client-side playback telemetry is not ingested"
            />
            <StatRow
              label="Blocked join attempts"
              value={null}
              reason="Refused playback attempts are not recorded"
            />
            <p className={cx("mt-3 border-t pt-3 text-[12px] leading-snug", CONSOLE.divider, CONSOLE.faint)}>
              Deliberate: this platform records registration and presence, not per-person
              profiling. These stay “—” until something is actually collected.
            </p>
          </Panel>

          <Panel title="Controls that shape the audience">
            <ul className="space-y-2">
              {[
                [FiUsers, "Members & Access", "/organization/users", "Who inside the organization can operate events."],
                [FiSlash, "Playback & Access", "/organization/playback", "Visibility, passphrase and registration gates."],
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
      </div>
    </div>
  );
}
