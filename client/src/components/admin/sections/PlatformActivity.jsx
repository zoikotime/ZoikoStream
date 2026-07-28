import { useMemo } from "react";
import { FiGrid, FiUserPlus } from "react-icons/fi";
import Panel from "../Panel";
import { timeAgo } from "../format";

// Section 7 — Recent Platform Activity. Merges the two real feeds /admin/dashboard
// already returns (newest organizations + newest signups) into one chronological log —
// no separate event-bus/webhook feed exists yet, so this is the honest "what's new".
export default function PlatformActivity({ latestOrganizations = [], latestSignups = [] }) {
  const items = useMemo(() => {
    const orgs = latestOrganizations.map((o) => ({
      key: `org-${o.id}`,
      icon: FiGrid,
      title: "New organization created",
      detail: o.name,
      when: o.created_at,
    }));
    const users = latestSignups.map((u) => ({
      key: `user-${u.id}`,
      icon: FiUserPlus,
      title: "New user joined",
      detail: `${u.full_name} · ${u.organization_name || u.role}`,
      when: u.created_at,
    }));
    return [...orgs, ...users]
      .sort((a, b) => new Date(b.when || 0) - new Date(a.when || 0))
      .slice(0, 8);
  }, [latestOrganizations, latestSignups]);

  if (items.length === 0) {
    return (
      <Panel eyebrow="Live feed" title="Recent Platform Activity" flush>
        <p className="px-5 py-6 text-sm text-slate-400 dark:text-slate-500">Nothing new yet.</p>
      </Panel>
    );
  }

  return (
    <Panel eyebrow="Live feed" title="Recent Platform Activity" flush>
      <ul className="divide-y divide-slate-100 dark:divide-slate-800">
        {items.map((f) => (
          <li key={f.key} className="flex items-center gap-3 px-5 py-3">
            <span className="grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-slate-100 text-slate-500 dark:bg-slate-800 dark:text-slate-400">
              <f.icon className="text-sm" />
            </span>
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm font-medium text-slate-800 dark:text-slate-100">{f.title}</p>
              <p className="truncate text-xs text-slate-500 dark:text-slate-400">{f.detail}</p>
            </div>
            <span className="w-16 shrink-0 text-right text-xs text-slate-400">{timeAgo(f.when)}</span>
          </li>
        ))}
      </ul>
    </Panel>
  );
}
