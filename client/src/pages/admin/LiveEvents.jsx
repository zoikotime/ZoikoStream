import { useMemo, useState } from "react";
import {
  FiSearch, FiRadio, FiUsers, FiTrendingUp, FiVideo, FiFilm,
} from "react-icons/fi";
import { cx } from "../../ui/tokens";
import Card from "../../ui/Card";
import StatsCard from "../../ui/StatsCard";
import HealthDot from "../../components/admin/HealthDot";
import { liveEvents, counts, summary } from "../../data/liveEvents";

// Tabs in the requested order; default to Live (the monitoring focus).
const TABS = [
  { key: "upcoming", label: "Upcoming" },
  { key: "live", label: "Live" },
  { key: "finished", label: "Finished" },
  { key: "failed", label: "Failed" },
];

// Event status pill.
const STATUS = {
  live: { status: "success", label: "Live", pulse: true },
  upcoming: { status: "info", label: "Upcoming" },
  finished: { status: "neutral", label: "Finished" },
  failed: { status: "error", label: "Failed" },
};
// Recording / replay job state → HealthDot badge (reuses the shared status colors).
const REC = {
  recording: { status: "error", label: "REC", pulse: true },
  processing: { status: "warning", label: "Processing" },
  saved: { status: "success", label: "Saved" },
  scheduled: { status: "neutral", label: "Scheduled" },
  failed: { status: "error", label: "Failed" },
};
const REPLAY = {
  ready: { status: "success", label: "Ready" },
  processing: { status: "warning", label: "Processing" },
  queued: { status: "pending", label: "Queued" },
  failed: { status: "error", label: "Failed" },
};

const initials = (name = "") =>
  name.trim().split(/\s+/).slice(0, 2).map((w) => w[0]).join("").toUpperCase();

const th = "px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-slate-400 whitespace-nowrap";
const td = "px-4 py-3 text-sm text-slate-600 dark:text-slate-300 whitespace-nowrap";
const dash = <span className="text-slate-300 dark:text-slate-600">—</span>;

// Badge cell from a state map (recording/replay); em-dash when absent.
const jobCell = (map, key) => {
  const j = key && map[key];
  return j ? <HealthDot badge status={j.status} label={j.label} pulse={j.pulse} /> : dash;
};

// Platform-wide Live Events monitor: KPI cards + status tabs + real-time table.
// ponytail: all mock (data/liveEvents.js); poll a GET or subscribe to a socket later.
export default function LiveEvents() {
  const [tab, setTab] = useState("live");
  const [q, setQ] = useState("");

  const rows = useMemo(() => {
    const query = q.trim().toLowerCase();
    return liveEvents.filter(
      (e) => e.status === tab && (!query || e.title.toLowerCase().includes(query) || e.org.toLowerCase().includes(query))
    );
  }, [tab, q]);

  const kpis = [
    { title: "Events", value: summary.events, icon: FiRadio, accent: "violet", delta: "6.4%", up: true },
    { title: "Viewers", value: summary.viewers, icon: FiUsers, accent: "blue", live: true },
    { title: "Peak Concurrency", value: summary.peak, icon: FiTrendingUp, accent: "indigo", delta: "11%", up: true },
    { title: "Recordings", value: summary.recordings, icon: FiVideo, accent: "emerald", delta: "3.2%", up: true },
    { title: "Replay Jobs", value: summary.replayJobs, icon: FiFilm, accent: "amber" },
  ];

  const latencyColor = (ms) => (ms >= 100 ? "text-rose-600 dark:text-rose-400" : ms >= 70 ? "text-amber-600 dark:text-amber-400" : "text-slate-600 dark:text-slate-300");

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-slate-900 dark:text-white">Live Events</h1>
          <p className="text-sm text-slate-500 dark:text-slate-400">Real-time streaming monitor across every organization</p>
        </div>
        <span className="inline-flex items-center gap-2 rounded-full border border-slate-200 bg-white px-3 py-1.5 text-xs font-medium text-slate-500 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-400">
          <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-green-500" /> Live · auto-refreshing
        </span>
      </div>

      {/* KPI cards */}
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-3 xl:grid-cols-5">
        {kpis.map((k) => <StatsCard key={k.title} {...k} />)}
      </div>

      {/* Monitor */}
      <Card padding="none" className="overflow-hidden">
        {/* Tabs + search */}
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-100 p-4 dark:border-slate-800">
          <div className="inline-flex rounded-xl bg-slate-100 p-1 dark:bg-slate-800">
            {TABS.map((t) => {
              const active = tab === t.key;
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
                  {t.key === "live" && (counts.live || 0) > 0 && <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-green-500" />}
                  {t.label}
                  <span className={cx("rounded-full px-1.5 text-xs tabular-nums", active ? "bg-slate-100 text-slate-600 dark:bg-slate-600 dark:text-slate-200" : "bg-slate-200 text-slate-500 dark:bg-slate-700 dark:text-slate-400")}>
                    {counts[t.key] || 0}
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
        <div className="overflow-x-auto">
          <table className="w-full min-w-[1040px]">
            <thead className="border-b border-slate-100 dark:border-slate-800">
              <tr>
                <th className={th}>Organization</th>
                <th className={th}>Event</th>
                <th className={th}>Status</th>
                <th className={`${th} text-right`}>Bitrate</th>
                <th className={`${th} text-right`}>Viewers</th>
                <th className={`${th} text-right`}>Latency</th>
                <th className={th}>Health</th>
                <th className={th}>Recording</th>
                <th className={th}>Replay</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
              {rows.map((e) => (
                <tr key={e.id} className="hover:bg-slate-50 dark:hover:bg-slate-800/50">
                  <td className={td}>
                    <div className="flex items-center gap-2.5">
                      <span className="grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-gradient-to-br from-violet-500 to-indigo-600 text-[10px] font-semibold text-white">
                        {initials(e.org)}
                      </span>
                      <span className="font-medium text-slate-800 dark:text-slate-100">{e.org}</span>
                    </div>
                  </td>
                  <td className={td}>
                    <p className="font-medium text-slate-800 dark:text-slate-100">{e.title}</p>
                    <p className={cx("text-xs", e.reason ? "text-rose-500 dark:text-rose-400" : "text-slate-400")}>
                      {e.reason || e.time}
                    </p>
                  </td>
                  <td className={td}>
                    <HealthDot badge {...STATUS[e.status]} />
                  </td>
                  <td className={`${td} text-right tabular-nums`}>{e.bitrate ? `${e.bitrate.toFixed(1)} Mbps` : dash}</td>
                  <td className={`${td} text-right tabular-nums`}>{e.viewers != null ? e.viewers.toLocaleString() : dash}</td>
                  <td className={`${td} text-right tabular-nums`}>
                    {e.latency != null ? <span className={cx("font-medium", latencyColor(e.latency))}>{e.latency} ms</span> : dash}
                  </td>
                  <td className={td}>{e.health ? <HealthDot status={e.health} /> : dash}</td>
                  <td className={td}>{jobCell(REC, e.recording)}</td>
                  <td className={td}>{jobCell(REPLAY, e.replay)}</td>
                </tr>
              ))}
              {rows.length === 0 && (
                <tr>
                  <td colSpan={9} className="px-4 py-12 text-center text-sm text-slate-400">
                    No {tab} events{q ? " match your search" : ""}.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}
