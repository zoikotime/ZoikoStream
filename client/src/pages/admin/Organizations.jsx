import { useMemo, useState } from "react";
import toast from "react-hot-toast";
import {
  FiCheckCircle, FiDownload, FiEdit2, FiGrid, FiPlus, FiSearch, FiSlash, FiTrash2,
} from "react-icons/fi";
import {
  Badge, Button, DataTable, Panel, StatCard, initials,
} from "../../components/admin";
import api, { errMsg } from "../../api";
import useApi from "../../hooks/useApi";
import OrgModal from "./OrgModal";

const PLAN_TONE = { Enterprise: "brand", Pro: "info", Starter: "neutral" };
const STATUS_TONE = { active: "success", trial: "warning", suspended: "danger" };
const SUB_TONE = { active: "success", trial: "warning", past_due: "danger", cancelled: "neutral" };

const inputCls =
  "h-9 w-full rounded-lg border border-slate-200 bg-white pl-9 pr-3 text-sm text-slate-800 placeholder:text-slate-400 focus-visible:border-violet-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100";
const selectCls =
  "h-9 rounded-lg border border-slate-200 bg-white px-3 text-sm text-slate-700 focus-visible:border-violet-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200";

const gb = (n) => `${Number(n || 0).toLocaleString(undefined, { maximumFractionDigits: 1 })} GB`;

function useOrgsData() {
  return useApi(() =>
    Promise.all([
      api.get("/admin/organizations", { params: { page_size: 100 } }).then((r) => r.data.items),
      api.get("/admin/plans").then((r) => r.data),
    ]).then(([organizations, plans]) => ({ organizations, plans }))
  );
}

// Real Super Admin Organizations console: GET/POST/PATCH/DELETE against /admin/organizations,
// create/edit through OrgModal, suspend/activate + delete wired straight to the API.
export default function Organizations() {
  const { data, loading, error, reload } = useOrgsData();
  const [q, setQ] = useState("");
  const [plan, setPlan] = useState("all");
  const [status, setStatus] = useState("all");
  const [modalOpen, setModalOpen] = useState(false);
  const [editingOrg, setEditingOrg] = useState(null);

  const organizations = useMemo(() => data?.organizations || [], [data]);
  const plans = data?.plans || [];

  const kpis = useMemo(
    () => ({
      total: organizations.length,
      active: organizations.filter((o) => o.status === "active").length,
      trial: organizations.filter((o) => o.status === "trial").length,
      suspended: organizations.filter((o) => o.status === "suspended").length,
      enterprise: organizations.filter((o) => o.plan === "Enterprise").length,
    }),
    [organizations]
  );

  const filtered = useMemo(() => {
    const query = q.trim().toLowerCase();
    return organizations.filter(
      (o) =>
        (!query || o.name.toLowerCase().includes(query) || (o.domain || "").toLowerCase().includes(query)) &&
        (plan === "all" || o.plan === plan) &&
        (status === "all" || o.status === status)
    );
  }, [organizations, q, plan, status]);

  const exportCsv = () => {
    const head = ["Organization", "Domain", "Plan", "Status", "Users", "Events", "Bandwidth GB", "Storage GB", "Region"];
    const lines = [head, ...filtered.map((o) => [o.name, o.domain || "", o.plan || "", o.status, o.users_count, o.events_count, o.bandwidth_gb, o.storage_used_gb, o.region || ""])];
    const csv = lines.map((r) => r.map((f) => `"${String(f).replace(/"/g, '""')}"`).join(",")).join("\n");
    const url = URL.createObjectURL(new Blob([csv], { type: "text/csv" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = "organizations.csv";
    a.click();
    URL.revokeObjectURL(url);
    toast.success(`Exported ${filtered.length} organizations`);
  };

  const openCreate = () => { setEditingOrg(null); setModalOpen(true); };
  const openEdit = (o) => { setEditingOrg(o); setModalOpen(true); };

  const toggleSuspend = async (o) => {
    const next = o.status === "suspended" ? "active" : "suspended";
    try {
      await api.patch(`/admin/organizations/${o.id}`, { status: next });
      toast.success(`${o.name} ${next === "suspended" ? "suspended" : "activated"}`);
      reload();
    } catch (e) {
      toast.error(errMsg(e));
    }
  };

  const remove = async (o) => {
    if (!window.confirm(`Delete ${o.name}? This cannot be undone.`)) return;
    try {
      await api.delete(`/admin/organizations/${o.id}`);
      toast.success(`${o.name} deleted`);
      reload();
    } catch (e) {
      toast.error(errMsg(e));
    }
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
            <span className="text-xs text-slate-400">{o.domain || "—"}</span>
          </div>
        </div>
      ),
    },
    { key: "plan", header: "Plan", sortable: true, render: (o) => (o.plan ? <Badge tone={PLAN_TONE[o.plan] || "neutral"}>{o.plan}</Badge> : <span className="text-slate-300 dark:text-slate-600">—</span>) },
    {
      key: "subscription_status",
      header: "Subscription",
      render: (o) => (o.subscription_status ? <Badge tone={SUB_TONE[o.subscription_status] || "neutral"}>{o.subscription_status}</Badge> : <span className="text-slate-300 dark:text-slate-600">—</span>),
    },
    { key: "users_count", header: "Users", align: "right", sortable: true },
    { key: "events_count", header: "Events", align: "right", sortable: true },
    { key: "bandwidth_gb", header: "Bandwidth", align: "right", mono: true, sortable: true, render: (o) => gb(o.bandwidth_gb) },
    { key: "storage_used_gb", header: "Storage", align: "right", mono: true, sortable: true, render: (o) => gb(o.storage_used_gb) },
    { key: "region", header: "Region", sortable: true, render: (o) => o.region || "—" },
  ];

  const rowActions = (o) => (
    <>
      <Button variant="ghost" size="sm" iconOnly title={`Edit ${o.name}`} leftIcon={FiEdit2} onClick={() => openEdit(o)} />
      <Button
        variant="ghost"
        size="sm"
        iconOnly
        title={o.status === "suspended" ? "Activate" : "Suspend"}
        leftIcon={o.status === "suspended" ? FiCheckCircle : FiSlash}
        onClick={() => toggleSuspend(o)}
      />
      <Button variant="ghost" size="sm" iconOnly title={`Delete ${o.name}`} leftIcon={FiTrash2} className="hover:text-rose-600 dark:hover:text-rose-400" onClick={() => remove(o)} />
    </>
  );

  if (error) {
    return (
      <div className="mx-auto max-w-[1440px] rounded-xl border border-rose-200 bg-rose-50 px-5 py-4 text-sm text-rose-700 dark:border-rose-500/20 dark:bg-rose-500/10 dark:text-rose-300">
        Couldn't load organizations. Try refreshing the page.
      </div>
    );
  }

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
          <Button variant="primary" leftIcon={FiPlus} onClick={openCreate}>
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
            {plans.map((p) => <option key={p.slug} value={p.name}>{p.name}</option>)}
          </select>
          <select value={status} onChange={(e) => setStatus(e.target.value)} className={selectCls} aria-label="Filter by status">
            <option value="all">All statuses</option>
            <option value="active">Active</option>
            <option value="trial">Trial</option>
            <option value="suspended">Suspended</option>
          </select>
        </div>
        <div className="border-t border-slate-100 dark:border-slate-800/70" />
        <DataTable
          columns={columns}
          rows={filtered}
          rowKey={(o) => o.id}
          loading={loading}
          rowActions={rowActions}
          initialSort={{ key: "users_count", dir: "desc" }}
          pageSize={8}
          minWidth={980}
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

      <OrgModal
        open={modalOpen}
        onClose={() => setModalOpen(false)}
        org={editingOrg}
        plans={plans}
        onSaved={reload}
      />
    </div>
  );
}
