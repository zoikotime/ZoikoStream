import { useEffect, useMemo, useState } from "react";
import toast from "react-hot-toast";
import {
  FiCheckCircle, FiDownload, FiEye, FiGrid, FiPlus, FiSearch, FiSlash, FiTrash2,
} from "react-icons/fi";
import {
  Badge, Button, DataTable, HealthDot, Panel, StatCard, initials,
} from "../../components/admin";
import { ORGS } from "../../data/orgs";

const PLAN_TONE = { Enterprise: "brand", Pro: "info", Starter: "neutral" };
const STATUS_TONE = { Active: "success", Trial: "warning", Suspended: "danger" };

// Parse "12.3 TB" / "480 GB" -> GB, so Bandwidth/Storage sort numerically.
const toGb = (s) => {
  const [n, unit] = String(s).split(" ");
  return parseFloat(n) * (unit === "TB" ? 1000 : 1);
};

const inputCls =
  "h-9 w-full rounded-lg border border-slate-200 bg-white pl-9 pr-3 text-sm text-slate-800 placeholder:text-slate-400 focus-visible:border-violet-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100";
const selectCls =
  "h-9 rounded-lg border border-slate-200 bg-white px-3 text-sm text-slate-700 focus-visible:border-violet-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200";

// Reference implementation of the admin design system: page header + StatCard KPIs +
// filter toolbar + DataTable (sortable, hover row actions, pagination, empty state).
export default function Organizations() {
  const [loading, setLoading] = useState(true);
  const [q, setQ] = useState("");
  const [plan, setPlan] = useState("all");
  const [status, setStatus] = useState("all");

  useEffect(() => {
    // ponytail: demo boot delay to exercise the skeletons. Replace with GET /admin/organizations.
    const t = setTimeout(() => setLoading(false), 450);
    return () => clearTimeout(t);
  }, []);

  const kpis = useMemo(
    () => ({
      total: ORGS.length,
      active: ORGS.filter((o) => o.status === "Active").length,
      trial: ORGS.filter((o) => o.status === "Trial").length,
      suspended: ORGS.filter((o) => o.status === "Suspended").length,
      enterprise: ORGS.filter((o) => o.plan === "Enterprise").length,
    }),
    []
  );

  const filtered = useMemo(() => {
    const query = q.trim().toLowerCase();
    return ORGS.filter(
      (o) =>
        (!query || o.name.toLowerCase().includes(query) || o.domain.includes(query)) &&
        (plan === "all" || o.plan === plan) &&
        (status === "all" || o.status === status)
    );
  }, [q, plan, status]);

  const exportCsv = () => {
    const head = ["Organization", "Domain", "Plan", "Status", "Users", "Events", "Bandwidth", "Storage", "Region"];
    const lines = [head, ...filtered.map((o) => [o.name, o.domain, o.plan, o.status, o.users, o.events, o.bandwidth, o.storage, o.region])];
    const csv = lines.map((r) => r.map((f) => `"${String(f).replace(/"/g, '""')}"`).join(",")).join("\n");
    const url = URL.createObjectURL(new Blob([csv], { type: "text/csv" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = "organizations.csv";
    a.click();
    URL.revokeObjectURL(url);
    toast.success(`Exported ${filtered.length} organizations`);
  };

  const columns = [
    {
      key: "name",
      header: "Organization",
      sortable: true,
      render: (o) => (
        <div className="flex items-center gap-3">
          <span className="grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-slate-100 text-xs font-semibold text-slate-600 dark:bg-slate-800 dark:text-slate-300">
            {initials(o.name)}
          </span>
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <span className="font-medium text-slate-800 dark:text-slate-100">{o.name}</span>
              <Badge tone={STATUS_TONE[o.status]} dot>{o.status}</Badge>
            </div>
            <span className="text-xs text-slate-400">{o.domain}</span>
          </div>
        </div>
      ),
    },
    { key: "plan", header: "Plan", sortable: true, render: (o) => <Badge tone={PLAN_TONE[o.plan]}>{o.plan}</Badge> },
    { key: "users", header: "Users", align: "right", sortable: true },
    { key: "events", header: "Events", align: "right", sortable: true },
    { key: "bandwidth", header: "Bandwidth", align: "right", mono: true, sortable: true, sortValue: (o) => toGb(o.bandwidth) },
    { key: "storage", header: "Storage", align: "right", mono: true, sortable: true, sortValue: (o) => toGb(o.storage) },
    { key: "region", header: "Region", sortable: true },
    { key: "health", header: "Health", render: (o) => <HealthDot status={o.health} /> },
  ];

  const rowActions = (o) => (
    <>
      <Button variant="ghost" size="sm" iconOnly title={`View ${o.name}`} leftIcon={FiEye} onClick={() => toast(`Open ${o.name}`, { icon: "🔎" })} />
      <Button
        variant="ghost"
        size="sm"
        iconOnly
        title={o.status === "Suspended" ? "Activate" : "Suspend"}
        leftIcon={o.status === "Suspended" ? FiCheckCircle : FiSlash}
        onClick={() => toast.success(`${o.status === "Suspended" ? "Activated" : "Suspended"} ${o.name}`)}
      />
      <Button variant="ghost" size="sm" iconOnly title={`Delete ${o.name}`} leftIcon={FiTrash2} className="hover:text-rose-600 dark:hover:text-rose-400" onClick={() => toast(`Deleted ${o.name}`, { icon: "🗑️" })} />
    </>
  );

  return (
    <div className="mx-auto max-w-[1440px] space-y-6">
      {/* Page header */}
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-[24px] font-semibold tracking-tight text-slate-900 dark:text-white">Organizations</h1>
          <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">Manage every organization on the platform</p>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="secondary" leftIcon={FiDownload} onClick={exportCsv} disabled={loading || filtered.length === 0}>
            Export
          </Button>
          <Button variant="primary" leftIcon={FiPlus} onClick={() => toast("Create organization — coming soon", { icon: "🏢" })}>
            Add Organization
          </Button>
        </div>
      </div>

      {/* KPI tiles */}
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 xl:grid-cols-5">
        <StatCard label="Total" value={kpis.total} loading={loading} />
        <StatCard label="Active" value={kpis.active} loading={loading} />
        <StatCard label="Trial" value={kpis.trial} loading={loading} />
        <StatCard label="Suspended" value={kpis.suspended} loading={loading} />
        <StatCard label="Enterprise" value={kpis.enterprise} loading={loading} />
      </div>

      {/* Table */}
      <Panel flush>
        <div className="flex flex-wrap items-center gap-3 px-4 py-3">
          <div className="relative min-w-0 flex-1">
            <FiSearch className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
            <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search by name or domain…" className={inputCls} />
          </div>
          <select value={plan} onChange={(e) => setPlan(e.target.value)} className={selectCls} aria-label="Filter by plan">
            <option value="all">All plans</option>
            <option value="Enterprise">Enterprise</option>
            <option value="Pro">Pro</option>
            <option value="Starter">Starter</option>
          </select>
          <select value={status} onChange={(e) => setStatus(e.target.value)} className={selectCls} aria-label="Filter by status">
            <option value="all">All statuses</option>
            <option value="Active">Active</option>
            <option value="Trial">Trial</option>
            <option value="Suspended">Suspended</option>
          </select>
        </div>
        <div className="border-t border-slate-100 dark:border-slate-800/70" />
        <DataTable
          columns={columns}
          rows={filtered}
          rowKey={(o) => o.id}
          loading={loading}
          rowActions={rowActions}
          initialSort={{ key: "users", dir: "desc" }}
          pageSize={8}
          minWidth={920}
          empty={{
            icon: FiGrid,
            title: "No organizations match your filters",
            description: "Try clearing the search or switching the plan and status filters.",
            action: (
              <Button variant="secondary" size="sm" onClick={() => { setQ(""); setPlan("all"); setStatus("all"); }}>
                Clear filters
              </Button>
            ),
          }}
        />
      </Panel>
    </div>
  );
}
