import { AreaChart, Area, ResponsiveContainer, YAxis } from "recharts";

// Tiny axis-less trend line for KPI tiles. `data` = number[] or [{ value }].
// ponytail: reuses recharts (already a dep) rather than hand-rolling an SVG path.
export default function Sparkline({ data, color = "#8b5cf6", height = 40 }) {
  const series = data.map((d, i) => ({ i, value: typeof d === "number" ? d : d.value }));
  const id = `spark-${color.replace("#", "")}`;
  return (
    <div style={{ height }} className="w-full">
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={series} margin={{ top: 4, right: 0, bottom: 0, left: 0 }}>
          <defs>
            <linearGradient id={id} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={color} stopOpacity={0.4} />
              <stop offset="100%" stopColor={color} stopOpacity={0} />
            </linearGradient>
          </defs>
          <YAxis hide domain={["dataMin", "dataMax"]} />
          <Area type="monotone" dataKey="value" stroke={color} strokeWidth={2} fill={`url(#${id})`} isAnimationActive={false} />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}
