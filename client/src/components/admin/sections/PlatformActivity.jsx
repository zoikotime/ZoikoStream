import { FiActivity, FiAlertTriangle, FiFilm, FiGrid, FiLifeBuoy, FiRadio, FiZap } from "react-icons/fi";
import Panel from "../Panel";
import HealthDot from "../HealthDot";

import { feed } from "../../../data/platform";

// Neutral chip per event type — the icon carries the meaning, colour is reserved for status.
const TYPE_ICON = {
  org: FiGrid,
  stream: FiRadio,
  recording: FiFilm,
  incident: FiAlertTriangle,
  webhook: FiZap,
  support: FiLifeBuoy,
};

// Section 7 — Recent Platform Activity. A tight operational log; each row's status is a
// single dot, not a coloured card.
export default function PlatformActivity() {
  return (
    <Panel eyebrow="Live feed" title="Recent Platform Activity" flush>
      <ul className="divide-y divide-slate-100 dark:divide-slate-800">
        {feed.map((f, i) => {
          const Ico = TYPE_ICON[f.type] || FiActivity;
          return (
            <li key={i} className="flex items-center gap-3 px-5 py-3">
              <span className="grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-slate-100 text-slate-500 dark:bg-slate-800 dark:text-slate-400">
                <Ico className="text-sm" />
              </span>
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-medium text-slate-800 dark:text-slate-100">{f.title}</p>
                <p className="truncate text-xs text-slate-500 dark:text-slate-400">{f.detail}</p>
              </div>
              <HealthDot status={f.status} label="" className="shrink-0" />
              <span className="w-16 shrink-0 text-right text-xs text-slate-400">{f.when}</span>
            </li>
          );
        })}
      </ul>
    </Panel>
  );
}
