import { useState } from "react";
import {
  FiPackage, FiShield, FiXCircle, FiAlertTriangle, FiPlus, FiCheckCircle,
} from "react-icons/fi";
import { Panel, Button, Badge, StatCard, TabStrip } from "../../components/admin";
import api, { errMsg } from "../../api";
import useApi from "../../hooks/useApi";
import { notify } from "../../ui/Toast";
import { CatalogVersionModal, CatalogVersionDrawer } from "./CommerceCatalogModal";
import { ServiceProfileModal, CancellationPolicyModal } from "./CommercePolicyModals";

// Super Admin side of ZST-LE-COM-001: the registries that Live Event orders are built
// from (catalog, service profiles, cancellation policies) plus the ledger reconciliation
// view. Per-event order construction (quote -> order -> capacity -> invoice -> incident)
// lives on the event's own detail page (pages/admin/EventDetail.jsx), not here — this page
// is account-wide config, that one is a specific commercial transaction.
const TABS = [
  { key: "catalog", label: "Catalog" },
  { key: "profiles", label: "Service Profiles" },
  { key: "policies", label: "Cancellation Policies" },
  { key: "reconciliation", label: "Reconciliation" },
];

const STATUS_TONE = { draft: "neutral", published: "success", retired: "danger" };

function StatusBadge({ status }) {
  return <Badge status={STATUS_TONE[status] || "neutral"}>{status}</Badge>;
}

function CatalogTab() {
  const { data, loading, error, reload } = useApi(() => api.get("/commercial/catalog-versions").then((r) => r.data));
  const [modalOpen, setModalOpen] = useState(false);
  const [openVersion, setOpenVersion] = useState(null);
  const rows = data || [];

  return (
    <div className="space-y-4">
      <div className="flex justify-end">
        <Button leftIcon={FiPlus} size="sm" onClick={() => setModalOpen(true)}>New catalog version</Button>
      </div>
      <Panel flush>
        {error ? (
          <p className="px-5 py-8 text-center text-sm text-rose-600 dark:text-rose-400">Couldn't load catalog versions. {errMsg(error)}</p>
        ) : !loading && rows.length === 0 ? (
          <div className="px-5 py-16 text-center">
            <div className="mx-auto mb-3 grid h-10 w-10 place-items-center rounded-full bg-slate-100 text-slate-400 dark:bg-slate-800">
              <FiPackage className="text-lg" />
            </div>
            <p className="text-sm font-semibold text-slate-700 dark:text-slate-200">No catalog versions yet</p>
            <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">Create one and price its lines before any event can be ordered.</p>
          </div>
        ) : (
          <ul className="divide-y divide-slate-100 dark:divide-slate-800">
            {loading && Array.from({ length: 3 }).map((_, i) => (
              <li key={i} className="flex items-center justify-between px-5 py-4">
                <div className="zk-skeleton h-4 w-48 rounded bg-slate-200 dark:bg-slate-800" />
              </li>
            ))}
            {!loading && rows.map((v) => (
              <li key={v.id} className="flex cursor-pointer items-center justify-between gap-3 px-5 py-4 hover:bg-slate-50 dark:hover:bg-slate-800/40" onClick={() => setOpenVersion(v)}>
                <div className="min-w-0">
                  <p className="font-medium text-slate-800 dark:text-slate-100">{v.vertical} · {v.version_label}</p>
                  <p className="text-xs text-slate-500 dark:text-slate-400">{v.lines.length} line{v.lines.length === 1 ? "" : "s"}{v.notes ? ` · ${v.notes}` : ""}</p>
                </div>
                <StatusBadge status={v.status} />
              </li>
            ))}
          </ul>
        )}
      </Panel>
      {modalOpen && <CatalogVersionModal onClose={() => setModalOpen(false)} onSaved={reload} />}
      {openVersion && (
        <CatalogVersionDrawer
          version={rows.find((r) => r.id === openVersion.id) || openVersion}
          onClose={() => setOpenVersion(null)}
          onChanged={reload}
        />
      )}
    </div>
  );
}

function ProfilesTab() {
  const { data, loading, error, reload } = useApi(() => api.get("/commercial/service-profiles").then((r) => r.data));
  const [modalOpen, setModalOpen] = useState(false);
  const rows = data || [];

  const publish = async (p) => {
    try {
      await api.post(`/commercial/service-profiles/${p.id}/publish`);
      notify.success("Service profile published");
      reload();
    } catch (e) {
      notify.error(errMsg(e));
    }
  };

  return (
    <div className="space-y-4">
      <div className="flex justify-end">
        <Button leftIcon={FiPlus} size="sm" onClick={() => setModalOpen(true)}>New service profile</Button>
      </div>
      <Panel flush>
        {error ? (
          <p className="px-5 py-8 text-center text-sm text-rose-600 dark:text-rose-400">Couldn't load service profiles. {errMsg(error)}</p>
        ) : !loading && rows.length === 0 ? (
          <div className="px-5 py-16 text-center">
            <div className="mx-auto mb-3 grid h-10 w-10 place-items-center rounded-full bg-slate-100 text-slate-400 dark:bg-slate-800">
              <FiShield className="text-lg" />
            </div>
            <p className="text-sm font-semibold text-slate-700 dark:text-slate-200">No service profiles yet</p>
            <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">R0-R3 profiles bind risk tier to mandatory readiness evidence.</p>
          </div>
        ) : (
          <ul className="divide-y divide-slate-100 dark:divide-slate-800">
            {!loading && rows.map((p) => (
              <li key={p.id} className="flex items-center justify-between gap-3 px-5 py-4">
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <Badge status="brand">{p.risk_tier.toUpperCase()}</Badge>
                    <p className="font-medium text-slate-800 dark:text-slate-100">{p.name}</p>
                  </div>
                  <p className="mt-0.5 text-xs text-slate-500 dark:text-slate-400">
                    {[
                      p.requires_backup_contribution && "backup contribution",
                      p.requires_dual_recording && "dual recording",
                      p.requires_command_owner && "command owner",
                      p.requires_reserved_capacity && "reserved capacity",
                    ].filter(Boolean).join(" · ") || "No mandatory evidence gates"}
                  </p>
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  <StatusBadge status={p.status} />
                  {p.status === "draft" && (
                    <Button variant="secondary" size="sm" leftIcon={FiCheckCircle} onClick={() => publish(p)}>Publish</Button>
                  )}
                </div>
              </li>
            ))}
          </ul>
        )}
      </Panel>
      {modalOpen && <ServiceProfileModal onClose={() => setModalOpen(false)} onSaved={reload} />}
    </div>
  );
}

function PoliciesTab() {
  const { data, loading, error, reload } = useApi(() => api.get("/commercial/cancellation-policies").then((r) => r.data));
  const [modalOpen, setModalOpen] = useState(false);
  const rows = data || [];

  const publish = async (p) => {
    try {
      await api.post(`/commercial/cancellation-policies/${p.id}/publish`);
      notify.success("Cancellation policy published");
      reload();
    } catch (e) {
      notify.error(errMsg(e));
    }
  };

  return (
    <div className="space-y-4">
      <div className="flex justify-end">
        <Button leftIcon={FiPlus} size="sm" onClick={() => setModalOpen(true)}>New cancellation policy</Button>
      </div>
      <Panel flush>
        {error ? (
          <p className="px-5 py-8 text-center text-sm text-rose-600 dark:text-rose-400">Couldn't load cancellation policies. {errMsg(error)}</p>
        ) : !loading && rows.length === 0 ? (
          <div className="px-5 py-16 text-center">
            <div className="mx-auto mb-3 grid h-10 w-10 place-items-center rounded-full bg-slate-100 text-slate-400 dark:bg-slate-800">
              <FiXCircle className="text-lg" />
            </div>
            <p className="text-sm font-semibold text-slate-700 dark:text-slate-200">No cancellation policies yet</p>
            <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">Without one, cancellations fail closed rather than guessing a refund amount.</p>
          </div>
        ) : (
          <ul className="divide-y divide-slate-100 dark:divide-slate-800">
            {!loading && rows.map((p) => (
              <li key={p.id} className="flex items-center justify-between gap-3 px-5 py-4">
                <div className="min-w-0">
                  <p className="font-medium text-slate-800 dark:text-slate-100">
                    {p.vertical} · {p.risk_tier ? p.risk_tier.toUpperCase() : "All tiers"}
                  </p>
                  <p className="text-xs text-slate-500 dark:text-slate-400">
                    {p.lead_time_min_hours}h{p.lead_time_max_hours ? `–${p.lead_time_max_hours}h` : "+"} notice · {p.refund_percentage}% refund
                  </p>
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  <StatusBadge status={p.status} />
                  {p.status === "draft" && (
                    <Button variant="secondary" size="sm" leftIcon={FiCheckCircle} onClick={() => publish(p)}>Publish</Button>
                  )}
                </div>
              </li>
            ))}
          </ul>
        )}
      </Panel>
      {modalOpen && <CancellationPolicyModal onClose={() => setModalOpen(false)} onSaved={reload} />}
    </div>
  );
}

function ReconList({ title, items, render, empty }) {
  return (
    <Panel title={title} count={items.length}>
      {items.length === 0 ? (
        <p className="text-sm text-slate-500 dark:text-slate-400">{empty}</p>
      ) : (
        <ul className="space-y-2">{items.map(render)}</ul>
      )}
    </Panel>
  );
}

function ReconciliationTab() {
  const { data, loading, error } = useApi(() => api.get("/commercial/reconciliation").then((r) => r.data));

  if (error) return <p className="px-1 py-8 text-center text-sm text-rose-600 dark:text-rose-400">Couldn't load reconciliation. {errMsg(error)}</p>;
  if (loading) return <div className="zk-skeleton h-40 w-full rounded-xl bg-slate-200 dark:bg-slate-800" />;

  const totalIssues =
    data.reservations_without_order.length + data.reservations_with_missing_order.length +
    data.unmatched_settlements.length + data.orders_missing_invoice.length;

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-4 xl:grid-cols-4">
        <StatCard label="Capacity without order" value={data.reservations_without_order.length} />
        <StatCard label="Orphaned reservations" value={data.reservations_with_missing_order.length} />
        <StatCard label="Unmatched settlements" value={data.unmatched_settlements.length} />
        <StatCard label="Orders missing invoice" value={data.orders_missing_invoice.length} />
      </div>

      {totalIssues === 0 ? (
        <Panel static>
          <div className="flex items-center gap-3 py-4">
            <FiCheckCircle className="text-lg text-emerald-500" />
            <p className="text-sm text-slate-600 dark:text-slate-300">No reconciliation exceptions right now.</p>
          </div>
        </Panel>
      ) : (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          <ReconList
            title="Hard-reserved capacity with no order" items={data.reservations_without_order}
            empty="None." render={(r) => (
              <li key={r.id} className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-300">
                {r.resource_type} — event {r.event_id}
              </li>
            )}
          />
          <ReconList
            title="Reservations pointing at a missing order" items={data.reservations_with_missing_order}
            empty="None." render={(r) => (
              <li key={r.id} className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-300">
                {r.resource_type} — event {r.event_id}
              </li>
            )}
          />
          <ReconList
            title="Unmatched payment settlements" items={data.unmatched_settlements}
            empty="None." render={(p) => (
              <li key={p.id} className="rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-800 dark:border-rose-500/30 dark:bg-rose-500/10 dark:text-rose-300">
                {p.provider} · {p.provider_payment_ref} · {p.amount} {p.currency}
              </li>
            )}
          />
          <ReconList
            title="Accepted orders with no invoice" items={data.orders_missing_invoice}
            empty="None." render={(o) => (
              <li key={o.id} className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-300">
                Order {o.id.slice(0, 8)} · {o.total_amount} {o.currency}
              </li>
            )}
          />
        </div>
      )}
    </div>
  );
}

export default function Commerce() {
  const [tab, setTab] = useState("catalog");

  return (
    <div className="mx-auto max-w-[1100px] space-y-6">
      <div>
        <h1 className="text-[24px] font-semibold tracking-tight text-slate-900 dark:text-white">Live Events Commerce</h1>
        <p className="mt-1 flex items-start gap-2 text-sm text-slate-500 dark:text-slate-400">
          <FiAlertTriangle className="mt-0.5 shrink-0 text-amber-500" />
          Nothing here charges real money — no payment processor is connected yet. This
          configures the registries a Live Event order is built from (ZST-LE-COM-001).
        </p>
      </div>

      <TabStrip tabs={TABS} active={tab} onChange={setTab} label="Commerce sections" idPrefix="commerce" />

      {tab === "catalog" && <CatalogTab />}
      {tab === "profiles" && <ProfilesTab />}
      {tab === "policies" && <PoliciesTab />}
      {tab === "reconciliation" && <ReconciliationTab />}
    </div>
  );
}
