import { useMemo, useState } from "react";
import { FiCreditCard, FiEdit2, FiSearch } from "react-icons/fi";
import { Badge, Button, DataTable, Panel, StatCard, money } from "../../components/admin";
import api from "../../api";
import useApi from "../../hooks/useApi";
import SubscriptionModal from "./SubscriptionModal";

const STATUS_TONE = { active: "success", trial: "warning", past_due: "danger", cancelled: "neutral" };
const statusLabel = (s) => s.split("_").map((w) => w[0].toUpperCase() + w.slice(1)).join(" ");

const inputCls =
  "h-9 w-full rounded-lg border border-slate-200 bg-white pl-9 pr-3 text-sm text-slate-800 placeholder:text-slate-400 focus-visible:border-violet-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100";
const selectCls =
  "h-9 rounded-lg border border-slate-200 bg-white px-3 text-sm text-slate-700 focus-visible:border-violet-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200";

const fmtDate = (iso) => (iso ? new Date(iso).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" }) : "—");

function useSubscriptionsData() {
  return useApi(() =>
    Promise.all([
      api.get("/admin/subscriptions", { params: { page_size: 100 } }).then((r) => r.data.items),
      api.get("/admin/plans").then((r) => r.data),
    ]).then(([subscriptions, plans]) => ({ subscriptions, plans }))
  );
}

// Every org subscription, real GET/PATCH against /admin/subscriptions.
export default function Subscriptions() {
  const { data, loading, error, reload } = useSubscriptionsData();
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("all");
  const [modalOpen, setModalOpen] = useState(false);
  const [editingSub, setEditingSub] = useState(null);

  const subs = useMemo(() => data?.subscriptions || [], [data]);
  const plans = data?.plans || [];

  const kpis = useMemo(() => {
    const mrr = subs
      .filter((s) => s.status === "active" || s.status === "trial")
      .reduce((sum, s) => sum + (s.price_monthly || 0), 0);
    return {
      total: subs.length,
      active: subs.filter((s) => s.status === "active").length,
      trial: subs.filter((s) => s.status === "trial").length,
      pastDue: subs.filter((s) => s.status === "past_due").length,
      mrr,
    };
  }, [subs]);

  const filtered = useMemo(() => {
    const query = q.trim().toLowerCase();
    return subs.filter(
      (s) =>
        (!query || (s.organization_name || "").toLowerCase().includes(query)) &&
        (status === "all" || s.status === status)
    );
  }, [subs, q, status]);

  const openEdit = (s) => { setEditingSub(s); setModalOpen(true); };

  const columns = [
    { key: "organization_name", header: "Organization", sortable: true, render: (s) => <span className="font-medium text-slate-800 dark:text-slate-100">{s.organization_name || "—"}</span> },
    { key: "plan", header: "Plan", sortable: true, render: (s) => s.plan || "—" },
    { key: "price_monthly", header: "Price", align: "right", mono: true, sortable: true, render: (s) => (s.price_monthly != null ? money(s.price_monthly) + "/mo" : "—") },
    { key: "status", header: "Status", render: (s) => <Badge tone={STATUS_TONE[s.status] || "neutral"} dot>{statusLabel(s.status)}</Badge> },
    { key: "seats", header: "Seats", align: "right", sortable: true },
    { key: "started_at", header: "Started", align: "right", render: (s) => fmtDate(s.started_at) },
    { key: "current_period_end", header: "Renews", align: "right", render: (s) => fmtDate(s.current_period_end) },
  ];

  const rowActions = (s) => (
    <Button variant="ghost" size="sm" iconOnly title="Edit subscription" leftIcon={FiEdit2} onClick={() => openEdit(s)} />
  );

  if (error) {
    return (
      <div className="mx-auto max-w-[1440px] rounded-xl border border-rose-200 bg-rose-50 px-5 py-4 text-sm text-rose-700 dark:border-rose-500/20 dark:bg-rose-500/10 dark:text-rose-300">
        Couldn't load subscriptions. Try refreshing the page.
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-[1440px] space-y-6">
      <div>
        <h1 className="text-[24px] font-semibold tracking-tight text-slate-900 dark:text-white">Subscriptions</h1>
        <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">Billing status across every organization</p>
      </div>

      <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 xl:grid-cols-5">
        <StatCard label="Total" value={kpis.total} loading={loading} />
        <StatCard label="Active" value={kpis.active} loading={loading} />
        <StatCard label="Trial" value={kpis.trial} loading={loading} />
        <StatCard label="Past Due" value={kpis.pastDue} loading={loading} />
        <StatCard label="MRR" value={money(kpis.mrr)} loading={loading} />
      </div>

      <Panel flush>
        <div className="flex flex-wrap items-center gap-3 px-4 py-3">
          <div className="relative min-w-0 flex-1">
            <FiSearch className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
            <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search by organization…" className={inputCls} />
          </div>
          <select value={status} onChange={(e) => setStatus(e.target.value)} className={selectCls} aria-label="Filter by status">
            <option value="all">All statuses</option>
            <option value="active">Active</option>
            <option value="trial">Trial</option>
            <option value="past_due">Past Due</option>
            <option value="cancelled">Cancelled</option>
          </select>
        </div>
        <div className="border-t border-slate-100 dark:border-slate-800/70" />
        <DataTable
          columns={columns}
          rows={filtered}
          rowKey={(s) => s.id}
          loading={loading}
          rowActions={rowActions}
          initialSort={{ key: "started_at", dir: "desc" }}
          pageSize={10}
          minWidth={860}
          empty={{
            icon: FiCreditCard,
            title: "No subscriptions match your filters",
            description: "Try clearing the search or switching the status filter.",
            action: (
              <Button variant="secondary" size="sm" onClick={() => { setQ(""); setStatus("all"); }}>
                Clear filters
              </Button>
            ),
          }}
        />
      </Panel>

      <SubscriptionModal
        open={modalOpen}
        onClose={() => setModalOpen(false)}
        subscription={editingSub}
        plans={plans}
        onSaved={reload}
      />
    </div>
  );
}
