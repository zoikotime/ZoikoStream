import { useEffect, useRef, useState } from "react";
import { FiCreditCard, FiEdit2, FiSearch } from "react-icons/fi";
import { Badge, Button, CONSOLE, DataTable, Panel, StatCard, TabStrip, money } from "../../components/admin";
import api from "../../api";
import useApi from "../../hooks/useApi";
import SubscriptionModal from "./SubscriptionModal";
import UsageEntitlements from "./UsageEntitlements";

const TABS = [
  { key: "usage", label: "Entitlements & usage" },
  { key: "subscriptions", label: "Subscriptions" },
];

// Stable identity for the "nothing loaded yet" case. The useMemo hooks below take this list
// as a dependency, and a fresh `[]` literal on every render would defeat every one of them
// (permanently, for a response that simply omits the field). It is never mutated.
const NONE = [];

// Both spellings: legacy rows are translated on read, never rewritten
// (models/subscription.LEGACY_SUBSCRIPTION_STATES), so either can arrive here.
const STATUS_TONE = {
  active: "success", trial: "warning", trialing: "warning", past_due: "danger",
  suspended: "danger", cancelled: "neutral", canceled: "neutral", trial_expired: "neutral",
};
const statusLabel = (s) => s.split("_").map((w) => w[0].toUpperCase() + w.slice(1)).join(" ");

// Field skins come from the console tokens so a hover or focus change lands on every filter
// row at once, instead of being re-typed per page.
const inputCls = CONSOLE.search;
const selectCls = CONSOLE.select;

const fmtDate = (iso) => (iso ? new Date(iso).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" }) : "—");

// Filters go to the SERVER. The page used to fetch the first 100 subscriptions and filter
// those in the browser, so an organization past row 100 could not be found and a status
// filter only ever narrowed what happened to be loaded.
function useSubscriptionsData({ q, status }) {
  return useApi(() =>
    Promise.all([
      api
        .get("/admin/subscriptions", {
          params: { page_size: 100, q: q || undefined, status: status === "all" ? undefined : status },
        })
        .then((r) => r.data),
      api.get("/admin/plans").then((r) => r.data),
    ]).then(([page, plans]) => ({ subscriptions: page.items, total: page.total, plans }))
  );
}

// Dataset-wide, from the server — never counted off the fetched page. Every KPI here used to
// be `subs.length` / `subs.filter(...)` over the first 100 rows, so "Total" stopped at 100 and
// read like a real count. MRR is the same server function Analytics uses, so the two pages
// can no longer show different numbers for one platform.
function useSubscriptionSummary() {
  return useApi(() => api.get("/admin/subscriptions/summary").then((r) => r.data));
}

// Usage & Entitlements (sidebar name; route /admin/subscriptions). Two views over one source:
// per-organization entitlements and measured usage (GET /admin/usage), and the plan
// subscriptions those entitlements come from (GET/PATCH /admin/subscriptions).
export default function Subscriptions() {
  const [tab, setTab] = useState("usage");
  // Plans feed both the usage plan filter and the subscription editor.
  const { data: plans } = useApi(() => api.get("/admin/plans").then((r) => r.data));

  return (
    <div className="mx-auto max-w-[1440px] space-y-6">
      <div>
        <h1 className="text-[24px] font-semibold tracking-tight text-slate-900 dark:text-white">Usage &amp; Entitlements</h1>
        {/* Not "billing": invoicing and payment collection are outside ZoikoStream's product
            boundary. This page reports what each organization is entitled to, what it has
            used, and the plan subscription the entitlement comes from. */}
        <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
          What each organization is entitled to, what it has used against those limits, and
          the plan subscriptions behind them.
        </p>
      </div>
      <TabStrip tabs={TABS} active={tab} onChange={setTab} label="Usage and entitlements sections" idPrefix="usage" />
      <div role="tabpanel" id={`usagepanel-${tab}`} aria-labelledby={`usage-${tab}`}>
        {tab === "usage" ? <UsageEntitlements plans={plans || NONE} /> : <SubscriptionsList />}
      </div>
    </div>
  );
}

// Every org subscription, real GET/PATCH against /admin/subscriptions.
function SubscriptionsList() {
  const [qInput, setQInput] = useState("");
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("all");
  const { data, loading, error, reload } = useSubscriptionsData({ q, status });
  const { data: summary, error: summaryError, reload: reloadSummary } = useSubscriptionSummary();

  // Debounced, so a search is one request rather than one per keystroke.
  useEffect(() => {
    const t = setTimeout(() => setQ(qInput.trim()), 300);
    return () => clearTimeout(t);
  }, [qInput]);

  const mounted = useRef(false);
  useEffect(() => {
    if (!mounted.current) { mounted.current = true; return; }
    reload();
  }, [q, status, reload]);
  const [modalOpen, setModalOpen] = useState(false);
  const [editingSub, setEditingSub] = useState(null);

  const subs = data?.subscriptions || NONE;
  const plans = data?.plans || [];

  // "—" when the summary could not be read: unknown, which is not the same fact as zero.
  const dash = "—";
  const kpis = summary
    ? { total: summary.total, active: summary.active, trial: summary.trial,
        pastDue: summary.past_due, mrr: summary.contracted_mrr }
    : { total: dash, active: dash, trial: dash, pastDue: dash, mrr: null };

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
      <div className="rounded-xl border border-rose-200 bg-rose-50 px-5 py-4 text-sm text-rose-700 dark:border-rose-500/20 dark:bg-rose-500/10 dark:text-rose-300">
        Couldn't load subscriptions. Try refreshing the page.
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 xl:grid-cols-5">
        {summaryError && (
          <div className="col-span-full flex items-center justify-between gap-3 rounded-xl border border-amber-200 bg-amber-50 px-4 py-2.5 text-sm text-amber-800 dark:border-amber-500/25 dark:bg-amber-500/10 dark:text-amber-200">
            <span>Couldn&apos;t load the subscription totals. The list below is unaffected.</span>
            <Button variant="secondary" size="sm" onClick={reloadSummary}>Retry</Button>
          </div>
        )}
        <StatCard label="Total" value={kpis.total} loading={loading} />
        <StatCard label="Active" value={kpis.active} loading={loading} />
        <StatCard label="Trial" value={kpis.trial} loading={loading} />
        <StatCard label="Past Due" value={kpis.pastDue} loading={loading} />
        <StatCard label="Contracted MRR" value={kpis.mrr == null ? "—" : money(kpis.mrr)} loading={loading} />
      </div>

      <Panel flush>
        <div className="flex flex-wrap items-center gap-3 px-4 py-3">
          <div className="relative min-w-0 flex-1">
            <FiSearch className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
            <input value={qInput} onChange={(e) => setQInput(e.target.value)} placeholder="Search by organization…" className={inputCls} />
          </div>
          <select value={status} onChange={(e) => setStatus(e.target.value)} className={selectCls} aria-label="Filter by status">
            <option value="all">All statuses</option>
            <option value="active">Active</option>
            <option value="trialing">Trialing</option>
            <option value="past_due">Past Due</option>
            <option value="suspended">Suspended</option>
            <option value="canceled">Canceled</option>
          </select>
        </div>
        <div className="border-t border-slate-100 dark:border-slate-800/70" />
        <DataTable
          columns={columns}
          rows={subs}
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

      {modalOpen && (
        <SubscriptionModal
          key={editingSub?.id ?? "none"}
          open
          onClose={() => setModalOpen(false)}
          subscription={editingSub}
          plans={plans}
          onSaved={() => { reload(); reloadSummary(); }}
        />
      )}
    </div>
  );
}
