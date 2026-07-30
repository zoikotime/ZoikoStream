import { FiCheck, FiInfo, FiShield } from "react-icons/fi";
import { Panel } from "../../components/admin";
import api from "../../api";
import useApi from "../../hooks/useApi";
import Skeleton from "../../ui/Skeleton";

function useRolesData() {
  return useApi(() => api.get("/admin/roles").then((r) => r.data));
}

// Read-only reference — GET /admin/roles, derived straight from security.py's
// require_min_role ladder. There's no dynamic permission system to edit here:
// authorization is code-defined, so an "editable" matrix would be cosmetic and would
// silently do nothing when toggled. This page documents what's actually enforced.
export default function Roles() {
  const { data: roles, loading, error } = useRolesData();

  if (error) {
    return (
      <div className="mx-auto max-w-[1000px] rounded-xl border border-rose-200 bg-rose-50 px-5 py-4 text-sm text-rose-700 dark:border-rose-500/20 dark:bg-rose-500/10 dark:text-rose-300">
        Couldn't load roles. Try refreshing the page.
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-[1000px] space-y-6">
      <div>
        <h1 className="text-[24px] font-semibold tracking-tight text-slate-900 dark:text-white">Roles & Permissions</h1>
        <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">What each role can do across the platform</p>
      </div>

      <div className="flex items-start gap-2.5 rounded-xl border border-slate-200 bg-slate-50 px-5 py-3.5 text-sm text-slate-600 dark:border-slate-800 dark:bg-slate-800/40 dark:text-slate-300">
        <FiInfo className="mt-0.5 shrink-0 text-slate-400" />
        <span>Reference only — authorization is enforced in code (each role sits on a fixed ladder), not by a database-editable permission set.</span>
      </div>

      {loading ? (
        <div className="space-y-4">
          {Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} variant="block" className="h-32" />)}
        </div>
      ) : (
        <div className="space-y-4">
          {(roles || []).map((r) => (
            <Panel
              key={r.role}
              eyebrow={`Rank ${r.rank}`}
              title={r.label}
              description={r.description}
              action={<FiShield className="text-lg text-violet-500" />}
            >
              <ul className="grid gap-2 sm:grid-cols-2">
                {r.capabilities.map((c) => (
                  <li key={c} className="flex items-start gap-2 text-sm text-slate-600 dark:text-slate-300">
                    <FiCheck className="mt-0.5 shrink-0 text-green-500" />
                    <span>{c}</span>
                  </li>
                ))}
              </ul>
            </Panel>
          ))}
        </div>
      )}
    </div>
  );
}
