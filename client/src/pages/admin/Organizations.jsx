import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { FiPlus, FiSearch, FiEye } from "react-icons/fi";
import { ORGS, ORG_STATUS } from "../../data/orgs";

const initials = (name = "") =>
  name.trim().split(/\s+/).slice(0, 2).map((w) => w[0]).join("").toUpperCase();

const th = "px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-slate-400";
const td = "px-4 py-3 text-sm text-slate-600 dark:text-neutral-300";

export default function Organizations() {
  const [q, setQ] = useState("");
  const navigate = useNavigate();
  const rows = ORGS.filter((o) => o.name.toLowerCase().includes(q.trim().toLowerCase()));

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-slate-900 dark:text-white">
            Organizations
          </h1>
          <p className="text-sm text-slate-500 dark:text-neutral-400">
            {ORGS.length} organizations on the platform.
          </p>
        </div>
        <button
          onClick={() => navigate("/admin/organizations/create")}
          className="inline-flex items-center gap-1.5 rounded-xl bg-violet-600 px-4 py-2.5 text-sm font-semibold text-white shadow-sm hover:bg-violet-700"
        >
          <FiPlus /> Add Organization
        </button>
      </div>

      <div className="rounded-2xl border border-slate-200 bg-white shadow-sm dark:border-neutral-800 dark:bg-neutral-900">
        {/* Search */}
        <div className="border-b border-slate-100 p-4 dark:border-neutral-800">
          <div className="relative max-w-sm">
            <FiSearch className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
            <input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="Search organizations…"
              className="w-full rounded-xl border border-slate-200 bg-white py-2.5 pl-10 pr-4 text-sm text-slate-800 outline-none focus:border-violet-500 focus:ring-2 focus:ring-violet-100 dark:border-neutral-700 dark:bg-neutral-800 dark:text-neutral-100 dark:focus:ring-violet-500/20"
            />
          </div>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full min-w-[640px]">
            <thead className="border-b border-slate-100 dark:border-neutral-800">
              <tr>
                <th className={th}>Organization</th>
                <th className={th}>Plan</th>
                <th className={`${th} text-right`}>Users</th>
                <th className={`${th} text-right`}>Events</th>
                <th className={th}>Status</th>
                <th className={`${th} text-right`}>Action</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 dark:divide-neutral-800">
              {rows.map((o) => (
                <tr key={o.name} className="hover:bg-slate-50 dark:hover:bg-neutral-800/50">
                  <td className={td}>
                    <div className="flex items-center gap-3">
                      <span className="grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-gradient-to-br from-violet-500 to-indigo-600 text-xs font-semibold text-white">
                        {initials(o.name)}
                      </span>
                      <span className="font-medium text-slate-800 dark:text-neutral-100">{o.name}</span>
                    </div>
                  </td>
                  <td className={td}>{o.plan}</td>
                  <td className={`${td} text-right`}>{o.users}</td>
                  <td className={`${td} text-right`}>{o.events}</td>
                  <td className={td}>
                    <span className={`rounded-full px-2.5 py-0.5 text-xs font-semibold ${ORG_STATUS[o.status]}`}>
                      {o.status}
                    </span>
                  </td>
                  <td className={`${td} text-right`}>
                    {/* ponytail: org detail page lands later */}
                    <button
                      title="View organization"
                      className="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 px-3 py-1.5 text-sm font-medium text-slate-600 hover:bg-slate-50 dark:border-neutral-700 dark:text-neutral-300 dark:hover:bg-neutral-800"
                    >
                      <FiEye /> View
                    </button>
                  </td>
                </tr>
              ))}
              {rows.length === 0 && (
                <tr>
                  <td colSpan={6} className="px-4 py-10 text-center text-sm text-slate-400">
                    No organizations match “{q}”.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
