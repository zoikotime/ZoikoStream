import { Link } from "react-router-dom";
import Panel from "../Panel";
import { timeAgo } from "../format";

// Best human label for what an audit row touched: prefer a name from the mutation's
// meta blob (organization.create logs {name: ...}, etc.), else fall back to the raw
// target type/id so nothing renders blank.
function targetLabel(a) {
  const metaName = a.meta?.name || a.meta?.email;
  if (metaName) return metaName;
  if (a.target_type && a.target_id) return `${a.target_type} · ${a.target_id.slice(0, 8)}`;
  return a.target_type || "—";
}

// Section 9 — Audit Timeline. Who did what, most recent first, straight from
// GET /admin/audit-logs — the same table every mutation in this app writes to.
export default function AuditTimeline({ logs = [] }) {
  return (
    <Panel
      eyebrow="Accountability"
      title="Audit Timeline"
      action={
        <Link to="/admin/audit" className="font-medium text-violet-600 hover:underline dark:text-violet-400">
          Full audit log →
        </Link>
      }
    >
      {logs.length === 0 ? (
        <p className="text-sm text-slate-400 dark:text-slate-500">No admin actions logged yet.</p>
      ) : (
        <ol className="relative space-y-4 pl-5">
          <span className="absolute inset-y-1 left-[3px] w-px bg-slate-200 dark:bg-slate-800" aria-hidden />
          {logs.map((a) => (
            <li key={a.id} className="relative">
              <span className="absolute -left-5 top-1.5 h-[7px] w-[7px] rounded-full border-2 border-white bg-violet-500 dark:border-slate-900" aria-hidden />
              <p className="text-sm">
                <code className="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-[12px] text-slate-700 dark:bg-slate-800 dark:text-slate-200">
                  {a.action}
                </code>{" "}
                <span className="text-slate-600 dark:text-slate-300">{targetLabel(a)}</span>
              </p>
              <p className="mt-0.5 text-xs text-slate-400">
                {a.actor_email || "system"} · {timeAgo(a.created_at)}
              </p>
            </li>
          ))}
        </ol>
      )}
    </Panel>
  );
}
