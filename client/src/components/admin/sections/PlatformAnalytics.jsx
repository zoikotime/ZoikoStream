import AreaChartCard from "../../../components/Dashboard/AreaChartCard";
import DonutChartCard from "../../../components/Dashboard/DonutChartCard";
import SectionHeading from "../SectionHeading";
import SectionCard from "../SectionCard";
import { analytics, CHART, CATEGORICAL } from "../../../data/platform";

// Activity heatmap — requests by day (rows) × hour (cols). Intensity 0-4 maps to a
// violet alpha ramp via inline style (dynamic Tailwind classes can't be JIT-scanned).
function Heatmap({ data }) {
  return (
    <SectionCard title="Activity Heatmap" subtitle="Requests by day & hour (UTC)">
      <div className="overflow-x-auto">
        <div className="min-w-[560px] space-y-1">
          {data.map((row) => (
            <div key={row.day} className="flex items-center gap-1">
              <span className="w-9 shrink-0 text-xs text-slate-400">{row.day}</span>
              {row.hours.map((v, h) => (
                <div
                  key={h}
                  className="h-4 flex-1 rounded-sm"
                  style={{ background: `rgba(139,92,246,${0.08 + v * 0.2})` }}
                  title={`${row.day} ${h}:00 · level ${v}`}
                />
              ))}
            </div>
          ))}
          <div className="flex items-center gap-1 pl-10 pt-1">
            {Array.from({ length: 24 }).map((_, h) => (
              <span key={h} className="flex-1 text-center text-[9px] text-slate-400">{h % 6 === 0 ? h : ""}</span>
            ))}
          </div>
        </div>
      </div>
      <div className="mt-3 flex items-center justify-end gap-1.5 text-[11px] text-slate-400">
        Less
        {[0, 1, 2, 3, 4].map((v) => (
          <span key={v} className="h-3 w-3 rounded-sm" style={{ background: `rgba(139,92,246,${0.08 + v * 0.2})` }} />
        ))}
        More
      </div>
    </SectionCard>
  );
}

// Section 7 — traffic, bandwidth, API load and a request heatmap over the last 14 days.
export default function PlatformAnalytics() {
  return (
    <section className="space-y-4">
      <SectionHeading title="Platform Analytics" description="Traffic, bandwidth and API load · last 14 days" />

      <div className="grid grid-cols-1 gap-6 xl:grid-cols-3">
        <div className="xl:col-span-2">
          <AreaChartCard
            title="Traffic"
            subtitle="Playback vs live-stream views"
            data={analytics.traffic}
            keys={[
              { key: "playback", name: "Playback", color: CHART.violet },
              { key: "live", name: "Live", color: CHART.blue },
            ]}
          />
        </div>
        <DonutChartCard title="Traffic Mix" subtitle="Share by workload" data={analytics.mix} colors={CATEGORICAL} />
      </div>

      <div className="grid grid-cols-1 gap-6 xl:grid-cols-3">
        <div className="xl:col-span-2">
          <AreaChartCard
            title="API Requests"
            subtitle="Total requests served per day"
            data={analytics.apiBandwidth}
            keys={[{ key: "requests", name: "Requests", color: CHART.indigo }]}
            type="line"
          />
        </div>
        <AreaChartCard
          title="Bandwidth"
          subtitle="Egress per day (GB)"
          data={analytics.apiBandwidth}
          keys={[{ key: "bandwidth", name: "Bandwidth", color: CHART.cyan }]}
          suffix=" GB"
        />
      </div>

      <Heatmap data={analytics.heatmap} />
    </section>
  );
}
