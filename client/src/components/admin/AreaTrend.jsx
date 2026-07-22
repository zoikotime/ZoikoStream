import { Area, AreaChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { useTheme } from "../../theme/ThemeContext";

// Minimal single-series area chart for the ops center — muted, axis-light, no grid.
// Distinct from the org dashboard's AreaChartCard (which is Card-wrapped with a full
// grid + legend); this one lives flush inside a Panel so charts stay quiet and flat.
// data: [{ label, value }].
export default function AreaTrend({
  data,
  color = "#8b5cf6",
  height = 220,
  showX = false,
  valueFormatter = (v) => Number(v).toLocaleString(),
}) {
  const { theme } = useTheme();
  const dark = theme === "dark";
  const id = `at-${color.replace("#", "")}`;
  return (
    <div style={{ height }} className="w-full">
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={data} margin={{ top: 6, right: 4, bottom: 0, left: 4 }}>
          <defs>
            <linearGradient id={id} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={color} stopOpacity={0.22} />
              <stop offset="100%" stopColor={color} stopOpacity={0} />
            </linearGradient>
          </defs>
          <YAxis hide domain={["dataMin", "dataMax"]} />
          {showX && (
            <XAxis
              dataKey="label"
              tickLine={false}
              axisLine={false}
              fontSize={11}
              minTickGap={28}
              stroke={dark ? "#475569" : "#94a3b8"}
            />
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
          <Area
            type="monotone"
            dataKey="value"
            stroke={color}
            strokeWidth={2}
            fill={`url(#${id})`}
            isAnimationActive={false}
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}
