import { PieChart, Pie, Cell, Tooltip, ResponsiveContainer } from "recharts";
import { useTheme } from "../../theme/ThemeContext";
import Card from "../../ui/Card";

// Donut + labelled legend. `data` = [{ label, value }]; `colors` cycles per slice.
// The legend doubles as the accessible readout (color dot + label + %).
export default function DonutChartCard({ title, subtitle, data, colors }) {
  const { theme } = useTheme();
  const dark = theme === "dark";
  const total = data.reduce((s, d) => s + d.value, 0) || 1;
  const pct = (v) => Math.round((v / total) * 100);

  return (
    <Card padding="md">
      <div className="mb-2">
        <h2 className="font-semibold text-slate-900 dark:text-white">{title}</h2>
        {subtitle && <p className="text-sm text-slate-500 dark:text-slate-400">{subtitle}</p>}
      </div>
      <div className="flex items-center gap-5">
        <div className="h-40 w-40 shrink-0">
          <ResponsiveContainer width="100%" height="100%">
            <PieChart>
              <Pie data={data} dataKey="value" nameKey="label" innerRadius={46} outerRadius={70} paddingAngle={2} stroke="none">
                {data.map((_, i) => (
                  <Cell key={i} fill={colors[i % colors.length]} />
                ))}
              </Pie>
              <Tooltip
                formatter={(v, name) => [`${pct(v)}%`, name]}
                contentStyle={{
                  borderRadius: 8,
                  border: `1px solid ${dark ? "#1e293b" : "#f1f5f9"}`,
                  fontSize: 12,
                  background: dark ? "#0f172a" : "#fff",
                  color: dark ? "#e2e8f0" : "#0f172a",
                }}
              />
            </PieChart>
          </ResponsiveContainer>
        </div>
        <ul className="min-w-0 flex-1 space-y-2">
          {data.map((d, i) => (
            <li key={d.label} className="flex items-center justify-between gap-2 text-sm">
              <span className="flex min-w-0 items-center gap-2 text-slate-600 dark:text-slate-300">
                <span className="h-2.5 w-2.5 shrink-0 rounded-full" style={{ background: colors[i % colors.length] }} />
                <span className="truncate">{d.label}</span>
              </span>
              <span className="shrink-0 font-medium tabular-nums text-slate-800 dark:text-slate-100">{pct(d.value)}%</span>
            </li>
          ))}
        </ul>
      </div>
    </Card>
  );
}
