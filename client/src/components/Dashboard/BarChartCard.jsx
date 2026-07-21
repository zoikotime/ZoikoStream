import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Cell } from "recharts";
import { useTheme } from "../../theme/ThemeContext";
import Card from "../../ui/Card";

// Reusable bar-chart card. `data` = [{ label, value }]. Tallest bar is highlighted.
export default function BarChartCard({ title, subtitle, data, color = "#7c3aed" }) {
  const { theme } = useTheme();
  const dark = theme === "dark";
  const grid = dark ? "#1e293b" : "#f1f5f9"; // slate-800 / slate-100
  const axis = dark ? "#64748b" : "#94a3b8"; // slate-500 / slate-400
  const max = Math.max(...data.map((d) => d.value));

  return (
    <Card padding="md">
      <div className="mb-4">
        <h2 className="font-semibold text-slate-900 dark:text-white">{title}</h2>
        {subtitle && <p className="text-sm text-slate-500 dark:text-slate-400">{subtitle}</p>}
      </div>
      <div className="h-64">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={data} margin={{ left: -16, right: 8, top: 8 }}>
            <CartesianGrid strokeDasharray="3 3" stroke={grid} vertical={false} />
            <XAxis dataKey="label" stroke={axis} fontSize={11} tickLine={false} axisLine={false} />
            <YAxis stroke={axis} fontSize={11} tickLine={false} axisLine={false} />
            <Tooltip
              cursor={{ fill: dark ? "rgba(124,58,237,0.08)" : "rgba(124,58,237,0.06)" }}
              contentStyle={{
                borderRadius: 8,
                border: `1px solid ${grid}`,
                fontSize: 12,
                background: dark ? "#0f172a" : "#fff",
                color: dark ? "#e2e8f0" : "#0f172a",
              }}
            />
            <Bar dataKey="value" radius={[6, 6, 0, 0]} maxBarSize={26}>
              {data.map((d, i) => (
                <Cell key={i} fill={color} fillOpacity={d.value === max ? 1 : 0.4} />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
    </Card>
  );
}
