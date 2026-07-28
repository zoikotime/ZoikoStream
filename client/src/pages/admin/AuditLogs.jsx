import { useMemo, useState } from "react";
import { FiFileText, FiSearch } from "react-icons/fi";
import { DataTable, Panel, StatCard, timeAgo } from "../../components/admin";
import api from "../../api";
import useApi from "../../hooks/useApi";

const inputCls =
  "h-9 w-full rounded-lg border border-slate-200 bg-white pl-9 pr-3 text-sm text-slate-800 placeholder:text-slate-400 focus-visible:border-violet-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100";
const selectCls =
  "h-9 rounded-lg border border-slate-200 bg-white px-3 text-sm text-slate-700 focus-visible:border-violet-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200";

// Isolated so Date.now() isn't called directly in component/render scope.
function countLast24h(rows) {
  const dayMs = 24 * 60 * 60 * 1000;
  const now = Date.now();
  return rows.filter((a) => a.created_at && now - new Date(a.created_at).getTime() < dayMs).length;
}

// Best human label for what an action touched — prefer a name/email from the mutation's
// meta blob, else fall back to the raw target type/id so nothing renders blank.
function targetLabel(a) {
  const metaName = a.meta?.name || a.meta?.email;
  if (metaName) return metaName;
  if (a.target_type && a.target_id) return `${a.target_type} · ${a.target_id.slice(0, 8)}`;
  return a.target_type || "—";
}

function useAuditLogsData() {
  return useApi(() => api.get("/admin/audit-logs", { params: { page_size: 100 } }).then((r) => r.data.items));
}

// Every super-admin mutation, straight from GET /admin/audit-logs — the same append-only
// table every write in the admin API records to.
export default function AuditLogs() {
  const { data: logs, loading, error } = useAuditLogsData();
  const [q, setQ] = useState("");
  const [action, setAction] = useState("all");
  const [targetType, setTargetType] = useState("all");

  const rows = logs || [];

  const actions = useMemo(() => [...new Set(rows.map((a) => a.action))].sort(), [rows]);
  const targetTypes = useMemo(() => [...new Set(rows.map((a) => a.target_type).filter(Boolean))].sort(), [rows]);

  const kpis = useMemo(() => {
    return {
      total: rows.length,
      last24h: countLast24h(rows),
      actors: new Set(rows.map((a) => a.actor_email).filter(Boolean)).size,
    };
  }, [rows]);

  const filtered = useMemo(() => {
    const query = q.trim().toLowerCase();
    return rows.filter(
      (a) =>
        (!query ||
          (a.actor_email || "").toLowerCase().includes(query) ||
          a.action.toLowerCase().includes(query) ||
          targetLabel(a).toLowerCase().includes(query)) &&
        (action === "all" || a.action === action) &&
        (targetType === "all" || a.target_type === targetType)
    );
  }, [rows, q, action, targetType]);

  const columns = [
    { key: "created_at", header: "When", sortable: true, render: (a) => (
      <span title={a.created_at ? new Date(a.created_at).toLocaleString() : undefined}>
        {a.created_at ? timeAgo(a.created_at) : "—"}
      </span>
    ) },
    { key: "actor_email", header: "Actor", sortable: true, render: (a) => a.actor_email || "system" },
    { key: "action", header: "Action", sortable: true, render: (a) => (
      <code className="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-[12px] text-slate-700 dark:bg-slate-800 dark:text-slate-200">
        {a.action}
      </code>
    ) },
    { key: "target", header: "Target", render: (a) => <span className="text-slate-600 dark:text-slate-300">{targetLabel(a)}</span> },
    { key: "ip", header: "IP", align: "right", mono: true, render: (a) => a.ip || "—" },
  ];

  if (error) {
    return (
      <div className="mx-auto max-w-[1440px] rounded-xl border border-rose-200 bg-rose-50 px-5 py-4 text-sm text-rose-700 dark:border-rose-500/20 dark:bg-rose-500/10 dark:text-rose-300">
        Couldn't load the audit log. Try refreshing the page.
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-[1440px] space-y-6">
      <div>
        <h1 className="text-[24px] font-semibold tracking-tight text-slate-900 dark:text-white">Audit Logs</h1>
        <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">Every action taken through the Super Admin console</p>
      </div>

      <div className="grid grid-cols-2 gap-4 sm:grid-cols-3">
        <StatCard label="Total Entries" value={kpis.total} loading={loading} />
        <StatCard label="Last 24h" value={kpis.last24h} loading={loading} />
        <StatCard label="Distinct Actors" value={kpis.actors} loading={loading} />
      </div>

      <Panel flush>
        <div className="flex flex-wrap items-center gap-3 px-4 py-3">
          <div className="relative min-w-0 flex-1">
            <FiSearch className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
            <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search by actor, action, or target…" className={inputCls} />
          </div>
          <select value={action} onChange={(e) => setAction(e.target.value)} className={selectCls} aria-label="Filter by action">
            <option value="all">All actions</option>
            {actions.map((a) => <option key={a} value={a}>{a}</option>)}
          </select>
          <select value={targetType} onChange={(e) => setTargetType(e.target.value)} className={selectCls} aria-label="Filter by target type">
            <option value="all">All targets</option>
            {targetTypes.map((t) => <option key={t} value={t}>{t}</option>)}
          </select>
        </div>
        <div className="border-t border-slate-100 dark:border-slate-800/70" />
        <DataTable
          columns={columns}
          rows={filtered}
          rowKey={(a) => a.id}
          loading={loading}
          initialSort={{ key: "created_at", dir: "desc" }}
          pageSize={15}
          minWidth={780}
          empty={{
            icon: FiFileText,
            title: "No audit entries match your filters",
            description: "Try clearing the search or switching the action and target filters.",
          }}
        />
      </Panel>
    </div>
  );
}
