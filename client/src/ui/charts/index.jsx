import {
  AreaChart as RAreaChart,
  Area,
  LineChart as RLineChart,
  Line,
  BarChart as RBarChart,
  Bar,
  PieChart as RPieChart,
  Pie,
  Cell,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend as RLegend,
  ResponsiveContainer,
} from "recharts";
import { useTheme } from "../../theme/ThemeContext";
import Card from "../Card";

// ─────────────────────────────────────────────────────────────────────────────
// Shared chart system. One home for every recharts wrapper + the card shell, tooltip,
// theme colors, loading and empty states that used to be copy-pasted per chart.
// Chart bodies keep their exact recharts config so visuals are unchanged.
// ─────────────────────────────────────────────────────────────────────────────

// Theme-derived grid/axis colors (was duplicated in every card).
function useChartTheme() {
  const { theme } = useTheme();
  const dark = theme === "dark";
  return {
    dark,
    grid: dark ? "#1e293b" : "#f1f5f9", // slate-800 / slate-100
    axis: dark ? "#64748b" : "#94a3b8", // slate-500 / slate-400
  };
}
const tipStyle = (dark) => ({
  borderRadius: 8,
  border: `1px solid ${dark ? "#1e293b" : "#f1f5f9"}`,
  fontSize: 12,
  background: dark ? "#0f172a" : "#fff",
  color: dark ? "#e2e8f0" : "#0f172a",
});

// Preconfigured, theme-aware recharts Tooltip. Use inside any chart.
export function ChartTooltip(props) {
  const { dark } = useChartTheme();
  return <Tooltip contentStyle={tipStyle(dark)} {...props} />;
}

export function ChartLoading({ height = 256 }) {
  return <div className="zk-skeleton rounded-xl bg-slate-100 dark:bg-slate-800" style={{ height }} aria-hidden="true" />;
}

export function ChartEmpty({ height = 256, text = "No data yet" }) {
  return (
    <div className="grid place-items-center text-sm text-slate-400 dark:text-slate-500" style={{ height }}>
      {text}
    </div>
  );
}

// Card shell with title/subtitle header + loading/empty handling. The chart goes inside.
export function ChartCard({ title, subtitle, action, loading = false, empty = false, emptyText, height = 256, className = "", children }) {
  return (
    <Card padding="md" className={className}>
      {(title || subtitle || action) && (
        <div className="mb-4 flex items-start justify-between gap-3">
          <div className="min-w-0">
            {title && <h2 className="font-semibold text-slate-900 dark:text-white">{title}</h2>}
            {subtitle && <p className="text-sm text-slate-500 dark:text-slate-400">{subtitle}</p>}
          </div>
          {action}
        </div>
      )}
      {loading ? <ChartLoading height={height} /> : empty ? <ChartEmpty height={height} text={emptyText} /> : children}
    </Card>
  );
}

// Color-dot + label + value readout (the donut's legend; reusable as a standalone legend).
export function ChartLegend({ items }) {
  return (
    <ul className="min-w-0 flex-1 space-y-2">
      {items.map((it) => (
        <li key={it.label} className="flex items-center justify-between gap-2 text-sm">
          <span className="flex min-w-0 items-center gap-2 text-slate-600 dark:text-slate-300">
            <span className="h-2.5 w-2.5 shrink-0 rounded-full" style={{ background: it.color }} />
            <span className="truncate">{it.label}</span>
          </span>
          <span className="shrink-0 font-medium tabular-nums text-slate-800 dark:text-slate-100">{it.value}</span>
        </li>
      ))}
    </ul>
  );
}

// Trend card — area (gradient fill) or line. `keys` = [{ key, name, color }]; multi-key shows a legend.
export function AreaChart({ title, subtitle, data, keys, type = "area", suffix = "", height = 256, loading = false, empty = false }) {
  const { grid, axis } = useChartTheme();
  const multi = keys.length > 1;
  const tick = (v) => (v >= 1000 ? `${Math.round(v / 100) / 10}k` : v);
  const fmt = (v) => `${Number(v).toLocaleString()}${suffix}`;
  const Chart = type === "area" ? RAreaChart : RLineChart;
  return (
    <ChartCard title={title} subtitle={subtitle} loading={loading} empty={empty} height={height}>
      <div style={{ height }}>
        <ResponsiveContainer width="100%" height="100%">
          <Chart data={data} margin={{ left: -12, right: 8, top: 8 }}>
            {type === "area" && (
              <defs>
                {keys.map((k) => (
                  <linearGradient key={k.key} id={`grad-${k.key}`} x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor={k.color} stopOpacity={0.35} />
                    <stop offset="100%" stopColor={k.color} stopOpacity={0.02} />
                  </linearGradient>
                ))}
              </defs>
            )}
            <CartesianGrid strokeDasharray="3 3" stroke={grid} vertical={false} />
            <XAxis dataKey="label" stroke={axis} fontSize={11} tickLine={false} axisLine={false} />
            <YAxis stroke={axis} fontSize={11} tickLine={false} axisLine={false} width={44} tickFormatter={tick} />
            <ChartTooltip formatter={fmt} />
            {multi && <RLegend wrapperStyle={{ fontSize: 12 }} iconType="circle" />}
            {keys.map((k) =>
              type === "area" ? (
                <Area key={k.key} type="monotone" dataKey={k.key} name={k.name} stroke={k.color} strokeWidth={2} fill={`url(#grad-${k.key})`} />
              ) : (
                <Line key={k.key} type="monotone" dataKey={k.key} name={k.name} stroke={k.color} strokeWidth={2} dot={false} activeDot={{ r: 4 }} />
              )
            )}
          </Chart>
        </ResponsiveContainer>
      </div>
    </ChartCard>
  );
}

// Line variant of the trend card.
export function LineChart(props) {
  return <AreaChart type="line" {...props} />;
}

// Bar-chart card. `data` = [{ label, value }]. Tallest bar highlighted.
export function BarChart({ title, subtitle, data, color = "#7c3aed", height = 256, loading = false, empty = false }) {
  const { dark, grid, axis } = useChartTheme();
  const max = Math.max(...data.map((d) => d.value));
  return (
    <ChartCard title={title} subtitle={subtitle} loading={loading} empty={empty} height={height}>
      <div style={{ height }}>
        <ResponsiveContainer width="100%" height="100%">
          <RBarChart data={data} margin={{ left: -16, right: 8, top: 8 }}>
            <CartesianGrid strokeDasharray="3 3" stroke={grid} vertical={false} />
            <XAxis dataKey="label" stroke={axis} fontSize={11} tickLine={false} axisLine={false} />
            <YAxis stroke={axis} fontSize={11} tickLine={false} axisLine={false} />
            <ChartTooltip cursor={{ fill: dark ? "rgba(124,58,237,0.08)" : "rgba(124,58,237,0.06)" }} />
            <Bar dataKey="value" radius={[6, 6, 0, 0]} maxBarSize={26}>
              {data.map((d, i) => (
                <Cell key={i} fill={color} fillOpacity={d.value === max ? 1 : 0.4} />
              ))}
            </Bar>
          </RBarChart>
        </ResponsiveContainer>
      </div>
    </ChartCard>
  );
}

// Donut + labelled legend. `data` = [{ label, value }]; `colors` cycles per slice.
export function PieChart({ title, subtitle, data, colors, loading = false, empty = false }) {
  const total = data.reduce((s, d) => s + d.value, 0) || 1;
  const pct = (v) => Math.round((v / total) * 100);
  return (
    <ChartCard title={title} subtitle={subtitle} loading={loading} empty={empty} height={160}>
      <div className="flex items-center gap-5">
        <div className="h-40 w-40 shrink-0">
          <ResponsiveContainer width="100%" height="100%">
            <RPieChart>
              <Pie data={data} dataKey="value" nameKey="label" innerRadius={46} outerRadius={70} paddingAngle={2} stroke="none">
                {data.map((_, i) => (
                  <Cell key={i} fill={colors[i % colors.length]} />
                ))}
              </Pie>
              <ChartTooltip formatter={(v, name) => [`${pct(v)}%`, name]} />
            </RPieChart>
          </ResponsiveContainer>
        </div>
        <ChartLegend items={data.map((d, i) => ({ label: d.label, value: `${pct(d.value)}%`, color: colors[i % colors.length] }))} />
      </div>
    </ChartCard>
  );
}

// Lib-free circular progress gauge (SVG).
export function RadialChart({ title, percent, label, footer, color = "#7c3aed" }) {
  const r = 54;
  const circ = 2 * Math.PI * r;
  const offset = circ * (1 - percent / 100);
  return (
    <Card padding="md">
      <h2 className="mb-2 font-semibold text-slate-900 dark:text-white">{title}</h2>
      <div className="relative mx-auto grid h-40 w-40 place-items-center">
        <svg width="160" height="160" viewBox="0 0 160 160" className="-rotate-90">
          <circle cx="80" cy="80" r={r} fill="none" strokeWidth="14" className="stroke-slate-100 dark:stroke-slate-800" />
          <circle cx="80" cy="80" r={r} fill="none" stroke={color} strokeWidth="14" strokeLinecap="round" strokeDasharray={circ} strokeDashoffset={offset} />
        </svg>
        <div className="absolute text-center">
          <p className="text-3xl font-bold text-slate-900 dark:text-white">{percent}%</p>
          <p className="text-xs text-slate-500 dark:text-slate-400">{label}</p>
        </div>
      </div>
      {footer && <p className="mt-2 text-center text-sm text-slate-500 dark:text-slate-400">{footer}</p>}
    </Card>
  );
}

// Minimal single-series area chart for the ops center — muted, axis-light, no grid, flush
// (lives inside a Panel). Keeps its own quiet tooltip. data: [{ label, value }].
export function AreaTrend({ data, color = "#8b5cf6", height = 220, showX = false, valueFormatter = (v) => Number(v).toLocaleString() }) {
  const { dark } = useChartTheme();
  const id = `at-${color.replace("#", "")}`;
  return (
    <div style={{ height }} className="w-full">
      <ResponsiveContainer width="100%" height="100%">
        <RAreaChart data={data} margin={{ top: 6, right: 4, bottom: 0, left: 4 }}>
          <defs>
            <linearGradient id={id} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={color} stopOpacity={0.22} />
              <stop offset="100%" stopColor={color} stopOpacity={0} />
            </linearGradient>
          </defs>
          <YAxis hide domain={["dataMin", "dataMax"]} />
          {showX && (
            <XAxis dataKey="label" tickLine={false} axisLine={false} fontSize={11} minTickGap={28} stroke={dark ? "#475569" : "#94a3b8"} />
          )}
          <Tooltip
            cursor={{ stroke: dark ? "#334155" : "#cbd5e1", strokeWidth: 1 }}
            contentStyle={{
              borderRadius: 8,
              border: `1px solid ${dark ? "#1e293b" : "#e2e8f0"}`,
              background: dark ? "#0f172a" : "#fff",
              color: dark ? "#e2e8f0" : "#0f172a",
              fontSize: 12,
              boxShadow: "none",
            }}
            formatter={(v) => [valueFormatter(v), ""]}
            labelFormatter={() => ""}
          />
          <Area type="monotone" dataKey="value" stroke={color} strokeWidth={2} fill={`url(#${id})`} isAnimationActive={false} />
        </RAreaChart>
      </ResponsiveContainer>
    </div>
  );
}

// Tiny axis-less trend line for KPI tiles. `data` = number[] or [{ value }].
export function Sparkline({ data, color = "#8b5cf6", height = 40 }) {
  const series = data.map((d, i) => ({ i, value: typeof d === "number" ? d : d.value }));
  const id = `spark-${color.replace("#", "")}`;
  return (
    <div style={{ height }} className="w-full">
      <ResponsiveContainer width="100%" height="100%">
        <RAreaChart data={series} margin={{ top: 4, right: 0, bottom: 0, left: 0 }}>
          <defs>
            <linearGradient id={id} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={color} stopOpacity={0.4} />
              <stop offset="100%" stopColor={color} stopOpacity={0} />
            </linearGradient>
          </defs>
          <YAxis hide domain={["dataMin", "dataMax"]} />
          <Area type="monotone" dataKey="value" stroke={color} strokeWidth={2} fill={`url(#${id})`} isAnimationActive={false} />
        </RAreaChart>
      </ResponsiveContainer>
    </div>
  );
}
