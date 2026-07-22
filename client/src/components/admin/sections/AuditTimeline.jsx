import { Link } from "react-router-dom";
import Panel from "../Panel";
import { auditLog } from "../../../data/platform";

// Section 9 — Audit Timeline. Who did what, most recent first, as a quiet vertical rail.
// The action reads like a code path (monospace) — the engineering-console signature.
export default function AuditTimeline() {
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
      <ol className="relative space-y-4 pl-5">
        <span className="absolute inset-y-1 left-[3px] w-px bg-slate-200 dark:bg-slate-800" aria-hidden />
        {auditLog.map((a) => (
          <li key={a.id} className="relative">
            <span className="absolute -left-5 top-1.5 h-[7px] w-[7px] rounded-full border-2 border-white bg-violet-500 dark:border-slate-900" aria-hidden />
            <p className="text-sm">
              <code className="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-[12px] text-slate-700 dark:bg-slate-800 dark:text-slate-200">
                {a.action}
              </code>{" "}
              <span className="text-slate-600 dark:text-slate-300">{a.target}</span>
            </p>
            <p className="mt-0.5 text-xs text-slate-400">
              {a.actor} · {a.when}
            </p>
          </li>
        ))}
      </ol>
    </Panel>
  );
}
