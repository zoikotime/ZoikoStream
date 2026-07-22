import { Link } from "react-router-dom";
import Panel from "../Panel";
import AreaTrend from "../AreaTrend";
import { useLiveValue } from "../LiveCounter";
import { CHART, liveActivity } from "../../../data/platform";

const rows = [
  { label: "Live events", value: liveActivity.liveEvents },
  { label: "Streams starting", value: liveActivity.streamsStarting },
  { label: "Avg bitrate", value: `${liveActivity.avgBitrate} Mbps` },
  { label: "Recording jobs", value: liveActivity.recordingJobs },
  { label: "Replay processing", value: liveActivity.replayProcessing },
];

// Section 3 — Current Live Platform. One realtime chart (concurrent viewers) beside a
// quiet list of live counters. The single ticking number carries the "realtime" feel.
export default function LivePlatform() {
  const viewers = useLiveValue(liveActivity.currentViewers, liveActivity.currentViewers * 0.01, 2500);

  return (
    <Panel
      eyebrow="Realtime"
      title="Current Live Platform"
      action={
        <Link to="/admin/live-events" className="font-medium text-violet-600 hover:underline dark:text-violet-400">
          View live events →
        </Link>
      }
    >
      <div className="grid gap-6 lg:grid-cols-3">
        <div className="lg:col-span-2">
          <div className="flex items-baseline gap-2">
            <span className="h-2 w-2 animate-pulse rounded-full bg-green-500" />
            <span className="text-3xl font-semibold tabular-nums tracking-tight text-slate-900 dark:text-white">
              {Math.round(viewers).toLocaleString()}
            </span>
            <span className="text-sm text-slate-500 dark:text-slate-400">concurrent viewers</span>
          </div>
          <div className="mt-3">
            <AreaTrend data={liveActivity.viewersSeries} color={CHART.violet} height={176} />
          </div>
        </div>
        <ul className="divide-y divide-slate-100 dark:divide-slate-800 lg:border-l lg:border-slate-100 lg:pl-6 dark:lg:border-slate-800">
          {rows.map((r) => (
            <li key={r.label} className="flex items-center justify-between py-2.5 first:pt-0">
              <span className="text-sm text-slate-500 dark:text-slate-400">{r.label}</span>
              <span className="text-sm font-semibold tabular-nums text-slate-900 dark:text-white">
                {typeof r.value === "number" ? r.value.toLocaleString() : r.value}
              </span>
            </li>
          ))}
        </ul>
      </div>
    </Panel>
  );
}
