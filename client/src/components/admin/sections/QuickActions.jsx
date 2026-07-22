import { useNavigate } from "react-router-dom";
import toast from "react-hot-toast";
import Panel from "../Panel";
import Icon from "../icons";
import { cx } from "../../../ui/tokens";
import { quickActions } from "../../../data/platform";

// Section 10 — Quick Actions. A slim command row. Only the primary action carries the
// brand fill; the rest are quiet outline buttons.
export default function QuickActions() {
  const navigate = useNavigate();
  const run = (a) => (a.to ? navigate(a.to) : toast(`${a.label} — coming soon`, { icon: "🛠️" }));

  return (
    <Panel title="Quick Actions" eyebrow="Operate">
      <div className="flex flex-wrap gap-2.5">
        {quickActions.map((a, i) => (
          <button
            key={a.label}
            onClick={() => run(a)}
            className={cx(
              "inline-flex items-center gap-2 rounded-lg px-3.5 py-2 text-sm font-medium transition",
              i === 0
                ? "bg-violet-600 text-white hover:bg-violet-700"
                : "border border-slate-200 text-slate-700 hover:bg-slate-50 dark:border-slate-700 dark:text-slate-200 dark:hover:bg-slate-800"
            )}
          >
            <Icon name={a.icon} className="text-base" />
            {a.label}
          </button>
        ))}
      </div>
    </Panel>
  );
}
