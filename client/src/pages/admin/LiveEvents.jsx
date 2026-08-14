import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { FiSearch, FiRadio, FiGrid, FiClock } from "react-icons/fi";
import api, { diagnoseLoadError } from "../../api";
import useApi from "../../hooks/useApi";
import { cx } from "../../ui/tokens";
import Card from "../../ui/Card";
import StatsCard from "../../ui/StatsCard";
import HealthDot from "../../components/admin/HealthDot";
import DataTable from "../../components/admin/DataTable";
import { initials, timeAgo } from "../../components/admin/format";

const TABS = [
  { key: "live", label: "Live" },
  { key: "recent", label: "Recently Ended" },
];

const dash = <span className="text-slate-300 dark:text-slate-600">—</span>;

// Real broadcast duration from start/end timestamps (live = up to now).
function duration(startedAt, endedAt) {
  if (!startedAt) return dash;
  const start = new Date(startedAt).getTime();
  const end = endedAt ? new Date(endedAt).getTime() : Date.now();
  const mins = Math.max(0, Math.round((end - start) / 60000));
  if (mins < 60) return `${mins}m`;
  return `${Math.floor(mins / 60)}h ${mins % 60}m`;
}

function useLiveEventsData() {
  return useApi(() =>
    Promise.all([
      api.get("/admin/live-events", { params: { state: "live" } }).then((r) => r.data),
      api.get("/admin/live-events", { params: { state: "recent" } }).then((r) => r.data),
    ]).then(([live, recent]) => ({ live, recent }))
  );
}

// Platform-wide Live Events monitor. Every row comes from GET /admin/live-events, which is
// derived straight from broadcast_sessions joined to their event — no fabricated
// viewers/bitrate/recording state; fields the platform doesn't measure yet (LiveKit room
// stats) render as "—", not a number.
export default function LiveEvents() {
  const navigate = useNavigate();
  const { data, loading, error } = useLiveEventsData();
  const [tab, setTab] = useState("live");
  const [q, setQ] = useState("");

  const live = data?.live || [];
  const recent = data?.recent || [];
  const rowsForTab = tab === "live" ? live : recent;

  const rows = useMemo(() => {
    const query = q.trim().toLowerCase();
    if (!query) return rowsForTab;
    return rowsForTab.filter(
      (e) => (e.title || "").toLowerCase().includes(query) || (e.organization || "").toLowerCase().includes(query)
    );
  }, [rowsForTab, q]);

  const orgCount = useMemo(() => new Set(live.map((e) => e.organization).filter(Boolean)).size, [live]);

  const kpis = [
    { title: "Live Now", value: live.length, icon: FiRadio, accent: "violet", live: live.length > 0 },
    { title: "Organizations Streaming", value: orgCount, icon: FiGrid, accent: "blue" },
    { title: "Recently Ended", value: recent.length, icon: FiClock, accent: "amber" },
  ];

  const columns = [
    { key: "organization", header: "Organization", className: "whitespace-nowrap", render: (e) => (
      <div className="flex items-center gap-2.5">
        <span className="grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-gradient-to-br from-violet-500 to-indigo-600 text-[10px] font-semibold text-white">
          {initials(e.organization || "?")}
        </span>
        <span className="font-medium text-slate-800 dark:text-slate-100">{e.organization || "—"}</span>
      </div>
    ) },
    { key: "title", header: "Event", className: "whitespace-nowrap", render: (e) => (
      <p className="font-medium text-slate-800 dark:text-slate-100">{e.title}</p>
    ) },
    { key: "region", header: "Region", className: "whitespace-nowrap", render: (e) => e.region || dash },
    { key: "started_at", header: tab === "live" ? "Started" : "Started", align: "right", className: "whitespace-nowrap", render: (e) => (e.started_at ? timeAgo(e.started_at) : dash) },
    ...(tab === "recent"
      ? [{ key: "ended_at", header: "Ended", align: "right", className: "whitespace-nowrap", render: (e) => (e.ended_at ? timeAgo(e.ended_at) : dash) }]
      : []),
    { key: "duration", header: "Duration", align: "right", className: "whitespace-nowrap", render: (e) => duration(e.started_at, e.ended_at) },
    { key: "viewers", header: "Viewers", align: "right", className: "whitespace-nowrap", render: (e) => (e.viewers != null ? e.viewers.toLocaleString() : dash) },
    ...(tab === "live"
      ? [{ key: "health", header: "Health", className: "whitespace-nowrap", render: (e) => (e.health ? <HealthDot status={e.health} /> : dash) }]
      : []),
  ];

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-slate-900 dark:text-white">Live Events</h1>
          <p className="text-sm text-slate-500 dark:text-slate-400">Real-time streaming monitor across every organization</p>
        </div>
        <span className="inline-flex items-center gap-2 rounded-full border border-slate-200 bg-white px-3 py-1.5 text-xs font-medium text-slate-500 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-400">
          <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-green-500" /> Live · from broadcast sessions
        </span>
      </div>

      {error ? (
        <div className="rounded-xl border border-rose-200 bg-rose-50 px-5 py-4 text-sm text-rose-700 dark:border-rose-500/20 dark:bg-rose-500/10 dark:text-rose-300">
          {diagnoseLoadError(error, "/admin/live-events")}
        </div>
      ) : (
        <>
          {/* KPI cards */}
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
            {kpis.map((k) => <StatsCard key={k.title} {...k} />)}
          </div>

          {/* Monitor */}
          <Card padding="none" className="overflow-hidden">
            {/* Tabs + search */}
            <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-100 p-4 dark:border-slate-800">
              <div className="inline-flex rounded-xl bg-slate-100 p-1 dark:bg-slate-800">
                {TABS.map((t) => {
                  const active = tab === t.key;
                  const count = t.key === "live" ? live.length : recent.length;
                  return (
                    <button
                      key={t.key}
                      onClick={() => setTab(t.key)}
                      className={cx(
                        "inline-flex items-center gap-2 rounded-lg px-3 py-1.5 text-sm font-medium transition",
                        active
                          ? "bg-white text-slate-900 shadow-sm dark:bg-slate-700 dark:text-white"
                          : "text-slate-500 hover:text-slate-800 dark:text-slate-400 dark:hover:text-slate-100"
                      )}
                    >
                      {t.key === "live" && count > 0 && <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-green-500" />}
                      {t.label}
                      <span className={cx("rounded-full px-1.5 text-xs tabular-nums", active ? "bg-slate-100 text-slate-600 dark:bg-slate-600 dark:text-slate-200" : "bg-slate-200 text-slate-500 dark:bg-slate-700 dark:text-slate-400")}>
                        {count}
                      </span>
                    </button>
                  );
                })}
              </div>

              <div className="relative min-w-0 flex-1 sm:max-w-xs">
                <FiSearch className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
                <input
                  value={q}
                  onChange={(e) => setQ(e.target.value)}
                  placeholder="Search event or organization…"
                  className="w-full rounded-lg border border-slate-200 bg-white py-2 pl-10 pr-4 text-sm text-slate-800 outline-none focus:border-violet-500 focus:ring-2 focus:ring-violet-100 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-100 dark:focus:ring-violet-500/20"
                />
              </div>
            </div>

            {/* Table */}
            <DataTable
              columns={columns}
              rows={rows}
              rowKey={(e) => e.id}
              loading={loading}
              minWidth={900}
              onRowClick={(e) => e.event_id && navigate(`/admin/live-events/${e.event_id}`)}
              empty={{ title: `No ${tab === "live" ? "live" : "recently ended"} events${q ? " match your search" : ""}.` }}
            />
          </Card>
        </>
      )}
    </div>
  );
}
