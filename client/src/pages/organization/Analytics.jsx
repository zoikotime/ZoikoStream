// client/src/pages/organization/Analytics.jsx
// Analytics Dashboard — event performance for an organization.
// Route: /organization/analytics. Rendered inside OrganizationLayout.
// Backed by GET /organization/analytics (services/org.py analytics()) — real numbers from
// Event + BroadcastSession + AnalyticsSnapshot. Device/location/traffic-source breakdowns
// aren't shown here: nothing in this stack persists them historically (see the endpoint's
// own `breakdowns_note`), so a bar chart for them would be invented, not measured.
import { useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import {
  FiCalendar, FiCheck, FiChevronDown, FiDownload, FiUsers, FiClock, FiTrendingUp, FiActivity, FiGlobe,
  FiInfo, FiLock, FiPlay,
} from "react-icons/fi";
import { CONSOLE, cx, focusRing } from "../../ui/tokens";
import api from "../../api";
import useApi from "../../hooks/useApi";
import Card from "../../ui/Card";
import Button, { ConsoleButton } from "../../ui/Button";
import StatsCard from "../../ui/StatsCard";
import Spinner from "../../ui/Spinner";
import { notify } from "../../ui/Toast";
import {
  Area,
  Bar,
  CartesianGrid,
  ComposedChart,
  Legend,
  Line,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  XAxis,
  YAxis,
} from "recharts";
import { ChartCard, ChartTooltip, PieChart } from "../../ui/charts";
import DataTable from "../../components/admin/DataTable";
import ViewingSessionsPanel from "../../components/organization/ViewingSessionsPanel";
import { fmtDate } from "../../data/events";
import { formatEngagement, formatHoursTick, formatWatchTime } from "../../utils/analyticsFormat";
import OrganizationPageHeader from "../../components/organization/OrganizationPageHeader";
import {
  Banner, FactList, Footnote, KpiRow, KpiTile, Panel, PanelLink, Tag,
} from "../../components/organization/console/ConsoleUI";
import { useHashScroll } from "./event/workspace";
import { exportTextFile } from "../../utils/export";
import AskGuideButton from "../../operator-guide/AskGuideButton";

// One toast slot for the export, so a retry replaces the last outcome instead of stacking.
const EXPORT_TOAST = "analytics-export";
// What went wrong SAVING, by exportTextFile's reason. Generating the CSV fails separately.
const EXPORT_FAILED = {
  unsupported: "Exporting isn't available in the app yet. Export this report from ZoikoStream on the web.",
  write: "Couldn't save the report file on this device.",
  share: "Couldn't open the share sheet to save the report.",
  download: "Couldn't download the report file.",
  empty: "The report file came out empty, so nothing was saved.",
};

const CHART ={ violet: "#7c3aed", blue: "#3b82f6", emerald: "#10b981", amber: "#f59e0b", rose: "#f43f5e" };

const RANGES = [
  { key: "7d", label: "Last 7 days" },
  { key: "30d", label: "Last 30 days" },
  { key: "90d", label: "Last 90 days" },
  { key: "12m", label: "Last 12 months" },
];

const engTone = (v) =>
  v >= 70 ? CHART.emerald : v >= 55 ? CHART.amber : CHART.rose;

// The ranking metric switcher. Each entry knows its own label and number format so the
// panel header and bar readouts stay correct without a second lookup table.
const METRICS = [
  { value: "viewers", label: "Viewers", rankLabel: "peak viewers", format: (v) => v.toLocaleString() },
  { value: "watch_hours", label: "Watch time", rankLabel: "hours watched", format: (v) => formatWatchTime(v) },
  { value: "engagement", label: "Engagement", rankLabel: "engagement score", format: (v) => `${v}%` },
];

// DataTable already ships sorting, search and pagination; this table opted into none of them,
// so a 30-event window was a fixed wall of rows. Every column is sortable now, with the
// numeric ones sorting on their value rather than their formatted string.
const reportColumns = [
  // The event opens its own Insights section — the per-event view of these same figures.
  { key: "event", header: "Event", sortable: true, className: "whitespace-nowrap", render: (r) => (
    <Link
      to={`/organization/events/${r.id}/insights`}
      className={cx("rounded font-medium text-slate-800 hover:text-violet-600 dark:text-slate-100 dark:hover:text-violet-300", focusRing)}
    >
      {r.event}
    </Link>
  ) },
  { key: "date", header: "Date", sortable: true, sortValue: (r) => (r.date ? new Date(r.date).getTime() : 0), className: "whitespace-nowrap", render: (r) => (r.date ? fmtDate(r.date) : "—") },
  { key: "viewers", header: "Peak Viewers", align: "right", sortable: true, className: "whitespace-nowrap", render: (r) => r.viewers.toLocaleString() },
  { key: "watch_hours", header: "Watch Time", align: "right", sortable: true, className: "whitespace-nowrap", render: (r) => formatWatchTime(r.watch_hours) },
  // A measured score — 0 included — is a number; null means there was nothing to measure it
  // against (no telemetry, or no audience: the score is interactions PER viewer). This cell
  // used to interpolate the raw value, so a null rendered a bare "%" and an invalid
  // `width: null%`, which the browser dropped — leaving a full-width bar, coloured rose
  // because engTone(null) fell through to "low". Sorting stays on the raw value (DataTable:
  // numbers numerically, nulls last), never on this text.
  { key: "engagement", header: "Engagement", sortable: true, className: "whitespace-nowrap", render: (r) => {
    const measured = r.engagement != null;
    return (
      <div className="flex items-center gap-2">
        <div aria-hidden="true" className="h-1.5 w-24 overflow-hidden rounded-full bg-slate-100 dark:bg-slate-800">
          {measured && (
            <div
              className="h-full rounded-full"
              style={{ width: `${Math.min(100, Math.max(0, r.engagement))}%`, background: engTone(r.engagement) }}
            />
          )}
        </div>
        <span
          className={cx("tabular-nums font-medium", measured ? "text-slate-700 dark:text-slate-200" : CONSOLE.faint)}
          title={measured ? undefined : "Not measured"}
        >
          {formatEngagement(r.engagement)}
        </span>
      </div>
    );
  } },
];

// Segmented control — the page's one interaction primitive, used for the chart type and the
// ranking metric. Both only re-read data already on the client, so switching is instant.
function Segmented({ value, onChange, options }) {
  return (
    <div
      role="group"
      className={cx("inline-flex rounded-lg p-0.5", CONSOLE.segment)}
    >
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          onClick={() => onChange(o.value)}
          aria-pressed={value === o.value}
          className={cx(
            "rounded-[6px] px-2.5 py-1 text-xs font-semibold transition-colors duration-150 motion-reduce:transition-none",
            value === o.value ? CONSOLE.segmentOn : CONSOLE.segmentOff
          )}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

function RankedBars({ title, subtitle, items, color, format, action, emptyText }) {
  const max = Math.max(...items.map((i) => i.value)) || 1;
  return (
    <Card padding="md">
      {/* Wraps rather than squeezes. One unwrapping row let the title column shrink below its
          longest word on a phone — "Performing" painted over the metric switch — while the
          switch was squeezed until "Watch time" broke in two. The title now starts from
          10rem, so the switch drops below it when both cannot fit side by side. */}
      <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 flex-1 basis-40">
          <h2 className="font-semibold text-slate-900 dark:text-white">{title}</h2>
          {subtitle && <p className="text-sm text-slate-500 dark:text-slate-400">{subtitle}</p>}
        </div>
        {action}
      </div>
      {items.length === 0 ? (
        <p className={cx("py-6 text-center text-sm", CONSOLE.faint)}>
          {emptyText || "No events with viewers in this window yet."}
        </p>
      ) : (
        <ul className="space-y-3.5">
          {items.map((i) => (
            <li key={i.label} className="group -mx-2 rounded-lg px-2 py-1 transition-colors hover:bg-slate-50 motion-reduce:transition-none dark:hover:bg-white/[0.03]">
              <div className="mb-1 flex items-center justify-between gap-2 text-sm">
                {/* Truncated to one line; the full name is the tooltip (and is what a screen reader reads). */}
                <span className="truncate pr-2 text-slate-700 dark:text-slate-200" title={i.label}>{i.label}</span>
                <span className="shrink-0 font-medium tabular-nums text-slate-500 dark:text-slate-400">{format(i.value)}</span>
              </div>
              <div className="h-2 w-full overflow-hidden rounded-full bg-slate-100 dark:bg-slate-800">
                {/* Width transitions, so switching the ranking metric animates rather than
                    snapping to a different set of lengths. */}
                <div
                  className="h-full rounded-full transition-[width] duration-500 ease-out motion-reduce:transition-none"
                  style={{ width: `${(i.value / max) * 100}%`, background: color }}
                />
              </div>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

// Player reports (services/playback_qoe) are summarised over 24h / 7d / 30d / 90d / all — there
// is no 12-month window, so that range shows the figure as not available rather than borrowing
// a different window's numbers.
const QOE_WINDOW = { "7d": "7d", "30d": "30d", "90d": "90d" };

// "20m 29s", "1h 04m": an average, so seconds matter under an hour and not above.
const fmtAvgWatch = (secs) => {
  const h = Math.floor(secs / 3600);
  const m = Math.floor((secs % 3600) / 60);
  const sec = Math.round(secs % 60);
  return h > 0 ? `${h}h ${String(m).padStart(2, "0")}m` : `${m}m ${String(sec).padStart(2, "0")}s`;
};
const fmtCount = (v) => (typeof v === "number" ? v.toLocaleString() : "—");

// Start success from viewers' own players: sessions that began playback over sessions that
// reported. A start is a session, never a person.
function StreamQuality({ total, available }) {
  const sessions = total?.sessions;
  const starts = total?.playback_starts;
  const rate = sessions > 0 ? starts / sessions : null;
  return (
    <Panel
      id="quality"
      title="Stream quality"
      subtitle="Reported by viewers' own players"
      action={<PanelLink to="/organization/playback">Playback detail</PanelLink>}
    >
      <p className={cx("text-[10.5px] font-bold uppercase tracking-[0.14em]", CONSOLE.faint)}>Playback start success</p>
      <p className={cx("mt-1 text-[32px] font-bold leading-10 tabular-nums", rate == null ? CONSOLE.faint : CONSOLE.heading)}>
        {rate == null ? "—" : `${(rate * 100).toFixed(1)}%`}
      </p>
      <p className={cx("text-[12px]", CONSOLE.faint)}>
        {!available
          ? "Player reports cover up to the last 90 days"
          : rate == null
          ? "No player sessions reported in this window"
          : `Across ${sessions.toLocaleString()} player sessions`}
      </p>
      {rate != null && (
        <div className="mt-3 h-2 overflow-hidden rounded-full bg-slate-100 dark:bg-white/[0.08]" aria-hidden="true">
          <div className="h-full rounded-full bg-gradient-to-r from-violet-500 via-sky-500 to-emerald-400" style={{ width: `${Math.min(100, rate * 100)}%` }} />
        </div>
      )}
      <div className="mt-3">
        <FactList
          rows={[
            ["Successful starts", fmtCount(starts)],
            ["Sessions with a failure", fmtCount(total?.failed_sessions)],
            ["Player sessions", fmtCount(sessions)],
          ]}
        />
      </div>
      <Footnote>Live and replay combined. A start is a player session, not a person.</Footnote>
    </Panel>
  );
}

// Regions, devices and traffic sources are not persisted anywhere (see the endpoint's
// breakdowns_note), so this says so instead of drawing bars for figures nobody measured.
function AudienceReach({ note }) {
  return (
    <Panel title="Audience reach" subtitle="Regions, devices and traffic sources" tag="Not measured" tagTone="slate">
      <div className={cx("flex items-start gap-3 rounded-xl border border-dashed p-4 text-[12.5px]", CONSOLE.divider, CONSOLE.muted)}>
        <FiGlobe aria-hidden="true" className="mt-0.5 shrink-0 text-[16px]" />
        <p>{note || "Device, location and traffic-source breakdowns are not measured yet."}</p>
      </div>
    </Panel>
  );
}

// How much of the window is measured: an event with no telemetry is counted as such, never as
// an event whose audience was zero.
const COVERAGE = [
  ["measured", "bg-emerald-500", "Measured event datasets", "Watch-time telemetry recorded", "green"],
  ["measured_zero", "bg-sky-500", "Measured with no watch time", "Telemetry recorded; nobody watched", "sky"],
  ["unmeasured", "bg-amber-500", "No telemetry recorded", "Their watch figures read \u201c\u2014\u201d", "amber"],
];

function DataQuality({ counts, total }) {
  return (
    <Panel id="data-quality" title="Data quality & governance" subtitle={`Measurement coverage across ${total} event${total === 1 ? "" : "s"}`}>
      <ul className={cx("divide-y", CONSOLE.divideY)}>
        {COVERAGE.map(([key, dot, title, detail, tone]) => {
          const n = counts[key] || 0;
          return (
            <li key={key} className="flex items-center gap-3 py-2.5">
              <span aria-hidden="true" className={cx("h-2.5 w-2.5 shrink-0 rounded-full", dot)} />
              <div className="min-w-0 flex-1">
                <p className={cx("text-[13px] font-semibold", CONSOLE.heading)}>{title}</p>
                <p className={cx("text-[12px]", CONSOLE.faint)}>{detail}</p>
              </div>
              <Tag tone={n > 0 ? tone : "slate"}>{n}</Tag>
            </li>
          );
        })}
      </ul>
      <p className={cx("mt-2 flex items-start gap-2 rounded-lg border px-3 py-2 text-[11.5px]", CONSOLE.divider, CONSOLE.faint)}>
        <FiLock aria-hidden="true" className="mt-0.5 shrink-0" />
        Figures are computed on the server for your organization only; the CSV export carries exactly what is shown here.
      </p>
    </Panel>
  );
}

// ORG-05 "Playback starts over time": sessions that began playback in each UTC day (hour /
// week for the shortest and longest windows), from the same player reports as the headline
// row. A range the reports do not cover says so instead of borrowing another window.
const STARTS_LABEL = {
  hour: (d) => d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", timeZone: "UTC" }),
  day: (d) => d.toLocaleDateString("en-GB", { day: "2-digit", month: "short", timeZone: "UTC" }),
  week: (d) => d.toLocaleDateString("en-GB", { day: "2-digit", month: "short", timeZone: "UTC" }),
};

function PlaybackStarts({ series, available, failed, rangeLabel }) {
  const label = STARTS_LABEL[series?.bucket] || STARTS_LABEL.day;
  const data = (series?.points || []).map((p) => ({ label: label(new Date(p.start)), starts: p.playback_starts }));
  const total = data.reduce((n, p) => n + p.starts, 0);
  const empty = !available || failed || !series || total === 0;
  return (
    <ChartCard
      title="Playback starts over time"
      subtitle={`Sessions that began playback, per UTC ${series?.bucket || "day"} · ${rangeLabel}`}
      height={240}
      empty={empty}
      emptyText={
        !available
          ? "Player reports cover up to the last 90 days."
          : failed
          ? "Player reports couldn't be read."
          : "No playback starts were reported in this window."
      }
      action={<span className={cx("inline-flex items-center gap-1.5 text-[12px]", CONSOLE.body)}><span aria-hidden="true" className="h-2 w-2 rounded-full bg-violet-500" /> Playback starts</span>}
    >
      <div style={{ height: 240 }} role="img" aria-label={`Playback starts per ${series?.bucket || "day"} (UTC): ${total} in total.`}>
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart data={data} margin={{ left: 0, right: 8, top: 8 }}>
            <defs>
              <linearGradient id="zk-an-starts" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={CHART.violet} stopOpacity={0.3} />
                <stop offset="100%" stopColor={CHART.violet} stopOpacity={0} />
              </linearGradient>
            </defs>
            <CartesianGrid strokeDasharray="3 3" stroke="currentColor" className="text-slate-100 dark:text-slate-800" vertical={false} />
            <XAxis dataKey="label" fontSize={11} tickLine={false} axisLine={false} stroke="currentColor" className="text-slate-400 dark:text-slate-500" minTickGap={24} />
            <YAxis allowDecimals={false} fontSize={11} tickLine={false} axisLine={false} stroke="currentColor" className="text-slate-400 dark:text-slate-500" width="auto" />
            <ChartTooltip formatter={(v) => [v.toLocaleString(), "Playback starts"]} />
            <Area type="monotone" dataKey="starts" name="Playback starts" stroke={CHART.violet} strokeWidth={2} fill="url(#zk-an-starts)" activeDot={{ r: 4 }} />
          </ComposedChart>
        </ResponsiveContainer>
      </div>
    </ChartCard>
  );
}

export default function OrganizationAnalytics() {
  useHashScroll();
  const [range, setRange] = useState("30d");
  const { data, loading, error, reload } = useApi(() =>
    api.get("/organization/analytics", { params: { range } }).then((r) => r.data)
  );
  const qoeWindow = QOE_WINDOW[range] || null;
  // Optional: the page stands without player reports, and says which figures need them.
  const qoe = useApi(() =>
    qoeWindow
      ? api.get("/organization/playback/qoe", { params: { window: qoeWindow } }).then((r) => r.data).catch(() => null)
      : Promise.resolve(null)
  );

  // useApi fetches on mount and on reload() only — it has no deps array. Without this the
  // range <select> changed the SUBTITLES and nothing else: the request was never re-sent, so
  // "Last 7 days" showed 30-day figures. Same guarded-render pattern the Overview uses.
  const [lastRange, setLastRange] = useState(range);
  if (range !== lastRange) {
    setLastRange(range);
    reload();
    qoe.reload();
  }

  const rangeLabel = RANGES.find((r) => r.key === range)?.label ?? "";

  // Which metric drives the trend chart and the ranking list. Both read the payload that is
  // already on the client — switching them costs no request.
  const [metric, setMetric] = useState("viewers");
  const [chartType, setChartType] = useState("area");

  const kpis = useMemo(() => {
    // No `|| {0,0,0,0}` fallback: an absent payload must not manufacture measured zeros.
    const s = data?.summary || {};
    return [
      {
        // Renamed from "Total Viewers", which the value could not support: it is the sum of
        // each event's PEAK concurrency, so one person at three events counts three times.
        title: "Event Peak Viewers",
        value: s.peak_viewers_summed ?? 0,
        hint: "Sum of peak concurrent viewers across events",
        icon: FiUsers, accent: "violet",
      },
      { title: "Watch Time", value: formatWatchTime(s.watch_hours), icon: FiClock, accent: "blue" },
      { title: "Peak Concurrent Viewers", value: s.peak ?? 0, icon: FiTrendingUp, accent: "emerald" },
      {
        // "Avg. Engagement" read as a measurement. The score is a documented heuristic
        // (services/broadcast.engagement_score), so the name and hint say so.
        title: "Engagement Score",
        value: formatEngagement(s.engagement),
        hint: "Derived from measured audience interactions",
        icon: FiActivity, accent: "amber",
      },
    ];
  }, [data]);

  // Memoised for identity, not cost: `|| []` minted a fresh array every render, which made
  // every derivation below recompute on any state change (metric switch, chart toggle).
  const reports = useMemo(() => data?.reports || [], [data]);

  // ── Everything below is DERIVED from the payload already fetched — reports[] carries
  // per-event viewers, watch_hours, engagement and date, and only the table was reading it.
  // No new endpoint, no invented figures.
  const trend = useMemo(() => {
    const viewership = data?.trends?.viewership || [];
    const watch = data?.trends?.watch_time || [];
    // Merge the two series on their shared bucket label so one canvas can carry both.
    return viewership.map((v, i) => ({
      label: v.label,
      viewers: v.value,
      watch_hours: watch[i]?.value ?? 0,
    }));
  }, [data]);

  // Ranking is re-sorted client-side, so the metric switcher is instant. The server's
  // top_events is peak-only and capped at 5; reports[] has every event.
  const ranked = useMemo(() => {
    const isWatch = metric === "watch_hours";
    const isEng = metric === "engagement";
    return [...reports]
      .filter((r) => {
        if (isWatch) return (r.total_watch_seconds > 0 || r.watch_hours > 0);
        if (isEng) return r.engagement != null && r.engagement > 0;
        return r.viewers > 0;
      })
      .sort((a, b) => {
        if (isWatch) {
          const aSec = a.total_watch_seconds ?? ((a.watch_hours ?? 0) * 3600);
          const bSec = b.total_watch_seconds ?? ((b.watch_hours ?? 0) * 3600);
          return bSec - aSec;
        }
        if (isEng) return (b.engagement ?? 0) - (a.engagement ?? 0);
        return (b.viewers ?? 0) - (a.viewers ?? 0);
      })
      .slice(0, 8)
      .map((r) => ({
        label: r.event,
        value: isWatch ? (r.watch_hours ?? 0) : isEng ? (r.engagement ?? 0) : r.viewers,
      }));
  }, [reports, metric]);

  // Engagement mix: how many events land in each band. Only events with measured engagement
  // (engagement != null) are bucketed; unmeasured events do not falsely count as low engagement.
  const engagementMix = useMemo(() => {
    const scored = reports.filter((r) => r.engagement != null);
    const band = (v) => (v >= 70 ? "Strong (70%+)" : v >= 55 ? "Moderate (55–69%)" : "Low (<55%)");
    const counts = { "Strong (70%+)": 0, "Moderate (55–69%)": 0, "Low (<55%)": 0 };
    scored.forEach((r) => { counts[band(r.engagement)] += 1; });
    return Object.entries(counts)
      .filter(([, v]) => v > 0)
      .map(([label, value]) => ({ label, value }));
  }, [reports]);

  // The headline row (ORG-05): events in the window, player starts and start success from
  // viewers' own players, and an average watch time weighted by each event's viewers.
  const qoeTotal = typeof qoe.data?.total?.sessions === "number" ? qoe.data.total : null;
  const startRate = qoeTotal?.sessions > 0 ? qoeTotal.playback_starts / qoeTotal.sessions : null;
  const avgWatch = useMemo(() => {
    // average_watch_seconds is total / viewers per event, so total / average recovers each
    // event's viewers and the ratio of sums is the true weighted average.
    let total = 0;
    let viewers = 0;
    let events = 0;
    for (const r of reports) {
      if (r.total_watch_seconds > 0 && r.average_watch_seconds > 0) {
        total += r.total_watch_seconds;
        viewers += r.total_watch_seconds / r.average_watch_seconds;
        events += 1;
      }
    }
    return viewers > 0 ? { seconds: total / viewers, events } : null;
  }, [reports]);
  const coverage = useMemo(() => {
    const c = {};
    for (const r of reports) {
      const key = r.measurement_state || "unmeasured";
      c[key] = (c[key] || 0) + 1;
    }
    return c;
  }, [reports]);
  const measuredEvents = (coverage.measured || 0) + (coverage.measured_zero || 0);

  const hasAudience = reports.some((r) => r.viewers > 0);
  const hasEngagement = reports.some((r) => r.engagement != null);

  const engagementEmptyText = !reports.length
    ? "No events in this window yet."
    : !hasAudience
    ? "No events with an audience yet."
    : !hasEngagement
    ? "Engagement telemetry not measured"
    : "No data yet";

  // Watch hours against peak viewers, one point per event: the shape tells you whether big
  // audiences actually stayed. Two events with equal peaks can sit far apart vertically.
  const retention = useMemo(
    () =>
      reports
        .filter((r) => r.viewers > 0)
        .map((r) => ({ x: r.viewers, y: r.watch_hours ?? 0, z: r.engagement, name: r.event })),
    [reports]
  );

  // One export at a time: the ref stops a second tap in the same frame, the state drives the
  // buttons' loading/disabled look.
  const [exporting, setExporting] = useState(false);
  const exportingRef = useRef(false);

  const exportReport = async () => {
    if (exportingRef.current) return;
    if (!reports.length) {
      notify.info("There are no events in this window to export.", { id: EXPORT_TOAST });
      return;
    }
    let csv;
    try {
      // Headers say what the numbers ARE. "Peak Viewers" was already accurate here even when
      // the card above said "Total Viewers"; the engagement column now names itself a score.
      // A figure that was never measured exports as text, not as a numeric 0 that a
      // spreadsheet would happily average.
      const head = ["Event", "Date", "Peak Viewers", "Watch Time (hrs)", "Engagement Score (%)"];
      const notMeasured = (v) => (v == null ? "Not measured" : v);
      const body = reports.map((r) => [
        r.event, r.date, r.viewers,
        notMeasured(r.watch_hours == null ? null : r.watch_hours.toFixed(2)),
        notMeasured(r.engagement),
      ]);
      csv = [head, ...body]
        .map((row) => row.map((c) => `"${String(c ?? "").replace(/"/g, '""')}"`).join(","))
        .join("\n");
    } catch {
      notify.error("Couldn't generate the report from this data. Please refresh and try again.", { id: EXPORT_TOAST });
      return;
    }
    exportingRef.current = true;
    setExporting(true);
    try {
      // Named for the window the rows were fetched for (the payload says), not the <select>,
      // which can already show the next range while that request is in flight.
      const result = await exportTextFile(`zoikostream-analytics-${data?.range || range}.csv`, csv, "text/csv;charset=utf-8");
      // Success is said only once the file was actually downloaded or handed to the share
      // sheet — it used to be said unconditionally, after a click the Android app ignored.
      if (result.ok) notify.success("Report exported", { id: EXPORT_TOAST });
      else if (result.reason === "cancelled") notify.info("Export cancelled.", { id: EXPORT_TOAST });
      else notify.error(EXPORT_FAILED[result.reason] || EXPORT_FAILED.download, { id: EXPORT_TOAST });
    } finally {
      exportingRef.current = false;
      setExporting(false);
    }
  };

  return (
    <div className="space-y-6">
      <OrganizationPageHeader
        breadcrumbs={[["Organization", "/organization/manage"], ["Analytics", "/organization/analytics"], ["Overview"]]}
        eyebrow="Organization console / Cross-event intelligence"
        title="Analytics"
        subtitle="Understand audience activity, playback quality and event outcomes — without losing data context."
        actions={
          // Console vocabulary, not a page-local field style: CONSOLE.select and the console
          // Button are what every other screen in this shell uses.
          <>
            <AskGuideButton question="How did our events perform?" />
            <div className="relative">
              <FiCalendar className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400 dark:text-neutral-400" aria-hidden="true" />
              <select
                value={range}
                onChange={(e) => setRange(e.target.value)}
                aria-label="Date range"
                className={cx(CONSOLE.select, "appearance-none pl-9 pr-8")}
              >
                {RANGES.map((r) => (
                  <option key={r.key} value={r.key}>{r.label}</option>
                ))}
              </select>
              <FiChevronDown className="pointer-events-none absolute right-2.5 top-1/2 -translate-y-1/2 text-slate-400 dark:text-neutral-400" aria-hidden="true" />
            </div>
            {data?.timezone && (
              <span
                className={cx("inline-flex h-9 items-center gap-1.5 rounded-lg border px-3 text-[12.5px]", CONSOLE.control)}
                title="Dates and chart buckets use your organization's time zone"
              >
                <FiClock aria-hidden="true" /> {data.timezone}
              </span>
            )}
            <ConsoleButton onClick={exportReport} disabled={!reports.length || exporting} loading={exporting} leftIcon={FiDownload}>
              Export Report
            </ConsoleButton>
          </>
        }
      />

      {/* A failed range switch shows a banner over the figures we already have rather than
          replacing them: losing the whole view because one refetch failed is worse than
          reading slightly stale numbers that say so. */}
      {error && data && (
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-amber-200 bg-amber-50 px-5 py-3 text-sm text-amber-800 dark:border-amber-500/25 dark:bg-amber-500/10 dark:text-amber-300">
          <span>Couldn't refresh — showing the last figures that loaded.</span>
          <Button size="sm" variant="secondary" onClick={reload}>Retry</Button>
        </div>
      )}

      {error && !data ? (
        <div className="rounded-xl border border-rose-200 bg-rose-50 px-5 py-4 text-sm text-rose-700 dark:border-rose-500/20 dark:bg-rose-500/10 dark:text-rose-300">
          Couldn't load analytics. Try refreshing the page.
        </div>
      ) : loading && !data ? (
        <div className="grid place-items-center py-20"><Spinner /></div>
      ) : (
        // Refetching dims the panels instead of unmounting them, so changing the range keeps
        // the layout still rather than flashing a spinner.
        <div
          className={cx(
            "space-y-6 transition-opacity duration-200 motion-reduce:transition-none",
            loading && "pointer-events-none opacity-50"
          )}
          aria-busy={loading || undefined}
        >
          <KpiRow>
            <KpiTile
              icon={FiCalendar}
              label="Events analysed"
              value={reports.length}
              caption={`${measuredEvents} with measured telemetry · ${rangeLabel}`}
            />
            <KpiTile
              icon={FiPlay}
              tone="sky"
              label="Playback starts"
              value={qoeTotal ? qoeTotal.playback_starts.toLocaleString() : null}
              caption={qoeWindow ? "Player sessions, not unique people" : "Player reports cover up to 90 days"}
            />
            <KpiTile
              icon={FiClock}
              tone="violet"
              label="Avg watch time"
              value={avgWatch ? fmtAvgWatch(avgWatch.seconds) : null}
              caption={avgWatch ? `Per viewer, weighted across ${avgWatch.events} event${avgWatch.events === 1 ? "" : "s"}` : "No watch-time telemetry in this window"}
            />
            <KpiTile
              icon={FiCheck}
              tone="green"
              label="Start success"
              value={startRate == null ? null : `${(startRate * 100).toFixed(1)}%`}
              caption={startRate == null ? (qoeWindow ? "No player sessions reported" : "Player reports cover up to 90 days") : "Sessions that began playback"}
            />
          </KpiRow>

          <Banner title="Know what the numbers mean." aside={<Tag tone="violet">Measured data only</Tag>}>
            A playback start is a player session — not a verified individual. Figures are scoped to your organization&apos;s events, and anything that was not measured reads &ldquo;—&rdquo; rather than zero.
          </Banner>

          <div className="grid items-start gap-6 xl:grid-cols-[minmax(0,1fr)_22rem]">
            <div className="min-w-0 space-y-6">
              <PlaybackStarts series={qoe.data?.series} available={Boolean(qoeWindow)} failed={Boolean(qoe.error)} rangeLabel={rangeLabel} />

              <Panel
                id="performance"
                title="Event performance"
                subtitle="Cross-event comparison · open an event for its own Insights"
                flush
                action={
                  <div className="flex items-center gap-3">
                    <button
                      type="button"
                      onClick={exportReport}
                      disabled={!reports.length || exporting}
                      aria-busy={exporting || undefined}
                      className={cx("hidden items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-[12px] font-semibold disabled:opacity-40 sm:inline-flex", CONSOLE.control, focusRing)}
                    >
                      <FiDownload aria-hidden="true" /> Export CSV
                    </button>
                    <PanelLink to="/organization/events">View all events</PanelLink>
                  </div>
                }
              >
                {reports.length === 0 ? (
                  <p className="border-t border-slate-100 px-5 py-10 text-center text-sm text-slate-400 dark:border-white/[0.08] dark:text-slate-500">No events in this window yet.</p>
                ) : (
                  <div className="border-t border-slate-100 dark:border-white/[0.08]">
                    <DataTable
                      columns={reportColumns}
                      rows={reports}
                      rowKey={(r) => r.id}
                      minWidth={720}
                      searchable
                      searchKeys={["event"]}
                      searchPlaceholder="Find an event…"
                      initialSort={{ key: "viewers", dir: "desc" }}
                      pageSize={10}
                    />
                  </div>
                )}
              </Panel>
            </div>

            <aside className="min-w-0 space-y-6" aria-label="Quality and coverage">
              <StreamQuality total={qoeTotal} available={Boolean(qoeWindow)} />
              <AudienceReach note={data.breakdowns_note} />
              <DataQuality counts={coverage} total={reports.length} />
            </aside>
          </div>

          {/* Trend — both series on one canvas with independent axes, so 24 viewers and
              4 watch-hours stay legible together instead of one flattening the other. */}
          <ChartCard
            title="Viewership & watch time"
            subtitle={`Peak viewers and hours watched · ${rangeLabel}`}
            height={300}
            empty={trend.length === 0}
            emptyText="No events in this window yet."
            action={
              <div className="flex items-center gap-2">
                <Segmented
                  value={chartType}
                  onChange={setChartType}
                  options={[
                    { value: "area", label: "Area" },
                    { value: "bar", label: "Bars" },
                  ]}
                />
              </div>
            }
          >
            <div style={{ height: 300 }}>
              <ResponsiveContainer width="100%" height="100%">
                {/* No negative margins, and Y axes sized to their own labels (width="auto").
                    A Y axis's labels are anchored to its inner edge, so a fixed narrow width
                    plus `left/right: -12` pushed long labels past the SVG's edge, where they
                    were clipped: 0.025h read "025h", and 12000 viewers lost a digit. */}
                <ComposedChart data={trend} margin={{ left: 0, right: 0, top: 8 }}>
                  <defs>
                    <linearGradient id="zk-an-viewers" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor={CHART.violet} stopOpacity={0.35} />
                      <stop offset="100%" stopColor={CHART.violet} stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="3 3" stroke="currentColor" className="text-slate-100 dark:text-slate-800" vertical={false} />
                  <XAxis dataKey="label" fontSize={11} tickLine={false} axisLine={false} stroke="currentColor" className="text-slate-400 dark:text-slate-500" />
                  <YAxis yAxisId="v" fontSize={11} tickLine={false} axisLine={false} stroke={CHART.violet} width="auto" />
                  <YAxis yAxisId="w" orientation="right" fontSize={11} tickLine={false} axisLine={false} stroke={CHART.blue} width="auto" unit="h" tickFormatter={formatHoursTick} />
                  <ChartTooltip
                    formatter={(v, name) => [name === "Watch hours" ? `${v} hrs` : v.toLocaleString(), name]}
                  />
                  <Legend iconType="circle" iconSize={8} wrapperStyle={{ fontSize: 12, paddingTop: 8 }} />
                  {chartType === "area" ? (
                    <Area
                      yAxisId="v"
                      type="monotone"
                      dataKey="viewers"
                      name="Peak viewers"
                      stroke={CHART.violet}
                      strokeWidth={2}
                      fill="url(#zk-an-viewers)"
                      activeDot={{ r: 5 }}
                    />
                  ) : (
                    <Bar yAxisId="v" dataKey="viewers" name="Peak viewers" fill={CHART.violet} radius={[6, 6, 0, 0]} maxBarSize={26} />
                  )}
                  <Line
                    yAxisId="w"
                    type="monotone"
                    dataKey="watch_hours"
                    name="Watch hours"
                    stroke={CHART.blue}
                    strokeWidth={2}
                    dot={{ r: 3 }}
                    activeDot={{ r: 5 }}
                  />
                </ComposedChart>
              </ResponsiveContainer>
            </div>
          </ChartCard>

          {/* Did the audience stay? — beside the four audience totals. */}
          <div className="grid grid-cols-1 gap-6 xl:grid-cols-2">
            <ChartCard
              title="Audience retention"
              subtitle="Watch hours against peak viewers · one point per event"
              height={260}
              empty={retention.length === 0}
              emptyText={!reports.length ? "No events in this window yet." : "No events with an audience yet."}
            >
              <div style={{ height: 260 }}>
                <ResponsiveContainer width="100%" height="100%">
                  {/* Same rule as the trend chart: no negative left margin, and a Y axis as wide
                      as its widest label — watch hours under an hour read 0.025h, not 25h. */}
                  <ScatterChart margin={{ left: 0, right: 8, top: 8, bottom: 4 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="currentColor" className="text-slate-100 dark:text-slate-800" />
                    <XAxis type="number" dataKey="x" name="Peak viewers" fontSize={11} tickLine={false} axisLine={false} stroke="currentColor" className="text-slate-400 dark:text-slate-500" />
                    <YAxis type="number" dataKey="y" name="Watch hours" unit="h" fontSize={11} tickLine={false} axisLine={false} stroke="currentColor" className="text-slate-400 dark:text-slate-500" width="auto" tickFormatter={formatHoursTick} />
                    <ChartTooltip
                      cursor={{ strokeDasharray: "3 3" }}
                      formatter={(v, name) => [name === "Watch hours" ? `${v} hrs` : v.toLocaleString(), name]}
                      labelFormatter={() => ""}
                    />
                    <Scatter data={retention} name="Events" fill={CHART.violet} fillOpacity={0.75} />
                  </ScatterChart>
                </ResponsiveContainer>
              </div>
              <p className={cx("mt-2 text-xs", CONSOLE.faint)}>
                Up and to the right is a big audience that stayed. Far right but low means they
                arrived and left.
              </p>
            </ChartCard>

            <Panel title="Audience & engagement" subtitle={`Peaks, watch time and engagement · ${rangeLabel}`}>
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                {kpis.map((k) => (
                  <StatsCard key={k.title} {...k} />
                ))}
              </div>
            </Panel>
          </div>

          {/* Ranking + engagement mix */}
          <div className="grid grid-cols-1 gap-6 xl:grid-cols-2">
            <RankedBars
              title="Top Performing Events"
              subtitle={`Ranked by ${METRICS.find((m) => m.value === metric)?.rankLabel}`}
              items={ranked}
              color={metric === "watch_hours" ? CHART.blue : metric === "engagement" ? CHART.amber : CHART.violet}
              format={METRICS.find((m) => m.value === metric)?.format}
              action={<Segmented value={metric} onChange={setMetric} options={METRICS} />}
              emptyText={
                !reports.length
                  ? "No events in this window yet."
                  : metric === "watch_hours"
                  ? "No events with watch time in this window yet."
                  : metric === "engagement"
                  ? "No events with engagement in this window yet."
                  : "No events with viewers in this window yet."
              }
            />

            <PieChart
              title="Engagement mix"
              subtitle="Events by engagement band · chat, questions and reactions against peak audience"
              data={engagementMix}
              colors={[CHART.emerald, CHART.amber, CHART.rose]}
              empty={engagementMix.length === 0}
              emptyText={engagementEmptyText}
            />
          </div>

          {/* Session-aware metrics: additional to everything above, which is unchanged. */}
          <ViewingSessionsPanel summary={data.session_summary} reports={reports} />
        </div>
      )}
    </div>
  );
}
