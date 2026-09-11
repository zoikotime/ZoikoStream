import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { FiActivity, FiRefreshCw, FiExternalLink, FiUsers } from "react-icons/fi";
import api from "../../api";
import useApi from "../../hooks/useApi";
import useInterval from "../../hooks/useInterval";
import { CONSOLE, cx, type } from "../../ui/tokens";
import { ConsoleButton } from "../../ui/Button";
import Badge from "../../ui/Badge";
import DataTable from "../../components/admin/DataTable";
import Panel from "../../components/admin/Panel";
import StatCard from "../../components/admin/StatCard";
import FactGrid from "../../components/organization/FactGrid";
import { timeAgo } from "../../components/admin/format";
import OrganizationPageHeader from "../../components/organization/OrganizationPageHeader";
import OrganizationErrorState from "../../components/organization/OrganizationErrorState";

// Streaming Sessions — every broadcast this organization has run, live first.
//
// Reads the `sessions` block of /organization/overview, which the Overview page already polls;
// no new API surface. Two things the payload deliberately withholds are surfaced as such
// rather than faked:
//   • `room` (ingest endpoint/region) is not recorded on a session, so the column would be
//     empty for every row — it is a posture line with its reason instead of a dead column;
//   • self-service vs managed is not modelled (payload carries `breakdown_note` saying so).
//
// `items` is capped at six by the service (limit=6), so the table says what it is showing
// instead of implying it is the complete history — a silent cap reads as "that's all of them".
const REFRESH_MS = 20_000;
const RANGES = [
  ["24h", "24 hours"],
  ["7d", "7 days"],
  ["30d", "30 days"],
];

const MODE_TONE = { live: "success", test: "info", paused: "warning", ended: "neutral" };
const STATE_TONE = { Healthy: "success", Paused: "warning", Ended: "neutral", Archived: "neutral" };

const duration = (from, to) => {
  if (!from) return null;
  const ms = (to ? new Date(to) : new Date()).getTime() - new Date(from).getTime();
  if (Number.isNaN(ms) || ms < 0) return null;
  const mins = Math.round(ms / 60_000);
  if (mins < 60) return `${mins}m`;
  return `${Math.floor(mins / 60)}h ${String(mins % 60).padStart(2, "0")}m`;
};

export default function StreamingSessions() {
  const [range, setRange] = useState("24h");
  const { data, loading, error, reload } = useApi(() =>
    api.get("/organization/overview", { params: { range } }).then((r) => r.data)
  );
  useInterval(reload, REFRESH_MS);

  const sessions = useMemo(() => data?.sessions || {}, [data]);
  const items = useMemo(() => sessions.items || [], [sessions]);

  const columns = [
    {
      key: "title",
      header: "Session",
      sortable: true,
      render: (s) => (
        <div className="min-w-0">
          <p className={cx("truncate text-[13px] font-semibold", CONSOLE.heading)}>{s.title}</p>
          <p className={cx("truncate text-[11px]", CONSOLE.faint)}>
            {s.started_at ? `Started ${timeAgo(s.started_at)}` : "Not started"}
            {s.ended_at ? ` · ended ${timeAgo(s.ended_at)}` : ""}
          </p>
        </div>
      ),
    },
    {
      key: "mode",
      header: "Mode",
      sortable: true,
      render: (s) => (
        <Badge tone={MODE_TONE[s.mode] || "neutral"} dot={s.mode === "live"}>
          {s.mode === "test" ? "Test" : s.mode.charAt(0).toUpperCase() + s.mode.slice(1)}
        </Badge>
      ),
    },
    {
      key: "state",
      header: "State",
      sortable: true,
      render: (s) => <Badge tone={STATE_TONE[s.state] || "neutral"}>{s.state}</Badge>,
    },
    {
      key: "duration",
      header: "Duration",
      align: "right",
      sortValue: (s) => new Date(s.started_at || 0).getTime(),
      sortable: true,
      render: (s) => (
        <span className={cx("text-[13px]", type.mono, CONSOLE.body)}>
          {duration(s.started_at, s.ended_at) || "—"}
        </span>
      ),
    },
    {
      key: "peak_viewers",
      header: "Peak audience",
      align: "right",
      sortable: true,
      render: (s) => (
        <span className={cx("text-[13px]", type.mono, s.peak_viewers ? CONSOLE.heading : CONSOLE.faint)}>
          {s.peak_viewers || "—"}
        </span>
      ),
    },
  ];

  return (
    <div className="space-y-6">
      <OrganizationPageHeader
        title="Streaming Sessions"
        subtitle="Broadcasts this organization has run, with their live state and audience peaks."
        actions={
          <>
            {/* Range segment — same control vocabulary as the console's other windowed pages. */}
            <div
              className={cx("inline-flex rounded-lg p-0.5", CONSOLE.segment)}
              role="group"
              aria-label="Time range"
            >
              {RANGES.map(([value, label]) => (
                <button
                  key={value}
                  type="button"
                  onClick={() => {
                    setRange(value);
                    reload();
                  }}
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
            <ConsoleButton variant="secondary" leftIcon={FiRefreshCw} onClick={reload} disabled={loading}>
              Refresh
            </ConsoleButton>
          </>
        }
      />

      {error && <OrganizationErrorState error={error} onRetry={reload} title="Couldn't load sessions" />}

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <StatCard label="Active now" value={sessions.active ?? 0} loading={loading} />
        <StatCard label="Live" value={sessions.live ?? 0} loading={loading} />
        <StatCard label="Starting within 30 min" value={sessions.starting_soon ?? 0} loading={loading} />
        <StatCard
          label="Current audience"
          value={sessions.current_audience ?? "—"}
          loading={loading}
        />
      </div>

      <Panel
          title="Recent sessions"
          description={
            items.length
              ? `Showing the ${items.length} most recent — the overview payload caps this list.`
              : undefined
          }
          action={
            <Link to="/organization/analytics" className={cx("text-[12px] font-semibold", CONSOLE.link)}>
              Full analytics →
            </Link>
          }
          flush
        >
          <DataTable
            columns={columns}
            rows={items}
            rowKey={(s) => s.id}
            loading={loading}
            initialSort={{ key: "duration", dir: "desc" }}
            minWidth={720}
            rowActions={(s) => (
              <ConsoleButton
                variant="ghost"
                size="sm"
                iconOnly
                aria-label={`Open the event behind ${s.title}`}
                leftIcon={FiExternalLink}
                href={`/organization/events/${s.event_id}`}
              />
            )}
            empty={{
              icon: FiActivity,
              title: "No sessions in this window",
              description:
                "Nothing has been broadcast in the selected range. Widen the range, or start an event from Live Events.",
              action: (
                <ConsoleButton href="/organization/events" size="sm">
                  Live Events
                </ConsoleButton>
              ),
            }}
          />
      </Panel>

      <Panel title="Audience">
        <FactGrid
          columns={3}
          facts={[
            {
              label: "Current across live sessions",
              value: sessions.current_audience ?? null,
              reason: "No live session is being sampled right now",
            },
            {
              label: "All-time peak",
              value: sessions.peak_audience ?? null,
              reason: "No session has recorded a peak yet",
            },
            {
              label: "Unique viewers this window",
              value: null,
              reason: "Windowed unique-viewer metering is not integrated",
            },
          ]}
        />
      </Panel>

      <Panel title="Not measured here">
        <FactGrid
          columns={3}
          facts={[
            {
              label: "Ingest protocol",
              value: null,
              reason: "Protocol is not recorded on a session (documented gap)",
            },
            {
              label: "Ingest region",
              value: null,
              reason: "Region is not recorded on a session (documented gap)",
            },
            {
              label: "Self-service vs managed",
              value: null,
              reason: sessions.breakdown_note || "Not modelled in the schema",
            },
          ]}
        />
        <p className={cx("mt-4 border-t pt-3 text-[12px] leading-snug", CONSOLE.divider, CONSOLE.faint)}>
          These read “—” because nothing produces them, not because the value is zero.
        </p>
      </Panel>

      <Panel title="Where to go next">
        {/* Three across on desktop, stacking on mobile — a grid inside the section, not a
            rail beside the page. */}
        <ul className="grid gap-1 sm:grid-cols-2 lg:grid-cols-3">
          {[
            ["Live Events", "/organization/events", "Schedule, staff and run a broadcast."],
            ["Playback & Access", "/organization/playback", "Who may watch, and on what link."],
            ["Media & Replay", "/organization/recordings", "What each session left behind."],
          ].map(([label, to, desc]) => (
            <li key={to}>
              <Link
                to={to}
                className={cx(
                  "flex h-full items-start gap-2.5 rounded-lg px-2 py-2 transition-colors duration-150 motion-reduce:transition-none",
                  "hover:bg-slate-100 dark:hover:bg-white/[0.06]"
                )}
              >
                <FiUsers className={cx("mt-0.5 shrink-0 text-[14px]", CONSOLE.faint)} aria-hidden="true" />
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
