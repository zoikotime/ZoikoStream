import {
  AreaChart, Area, LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer,
} from "recharts";
import { useTheme } from "../../theme/ThemeContext";
import Card from "../../ui/Card";

// Trend card mirroring BarChartCard's styling. `keys` = [{ key, name, color }].
// type "area" fills a gradient; type "line" draws plain lines. Multi-key shows a legend.
export default function AreaChartCard({ title, subtitle, data, keys, type = "area", suffix = "", height = 256 }) {
  const { theme } = useTheme();
  const dark = theme === "dark";
  const grid = dark ? "#1e293b" : "#f1f5f9"; // slate-800 / slate-100
  const axis = dark ? "#64748b" : "#94a3b8"; // slate-500 / slate-400
  const multi = keys.length > 1;
  const tick = (v) => (v >= 1000 ? `${Math.round(v / 100) / 10}k` : v);

  const tooltipStyle = {
    borderRadius: 8,
    border: `1px solid ${grid}`,
    fontSize: 12,
    background: dark ? "#0f172a" : "#fff",
    color: dark ? "#e2e8f0" : "#0f172a",
  };
  const fmt = (v) => `${Number(v).toLocaleString()}${suffix}`;
  const Chart = type === "area" ? AreaChart : LineChart;

  return (
    <Card padding="md">
      <div className="mb-4">
        <h2 className="font-semibold text-slate-900 dark:text-white">{title}</h2>
        {subtitle && <p className="text-sm text-slate-500 dark:text-slate-400">{subtitle}</p>}
      </div>
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
            <Tooltip contentStyle={tooltipStyle} formatter={fmt} />
            {multi && <Legend wrapperStyle={{ fontSize: 12 }} iconType="circle" />}
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
    </Card>
  );
}
