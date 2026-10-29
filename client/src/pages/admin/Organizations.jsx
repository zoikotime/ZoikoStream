import { useMemo, useState } from "react";
import toast from "react-hot-toast";
import {
  FiCheckCircle, FiDownload, FiEdit2, FiGrid, FiLock, FiPlus, FiSearch, FiSlash, FiTrash2,
} from "react-icons/fi";
import { Badge, Button, CONSOLE, DataTable, Panel, StatCard, initials } from "../../components/admin";
import useElevation from "../../components/admin/useElevation";
import api, { errMsg } from "../../api";
import useApi from "../../hooks/useApi";
import OrgModal from "./OrgModal";
import ConfirmDialog from "../../ui/ConfirmDialog";
import { ConsoleButton } from "../../ui/Button";
import Modal from "../../ui/Modal";

// Stable identity for the "nothing loaded yet" case. The useMemo hooks below take this list
// as a dependency, and a fresh `[]` literal on every render would defeat every one of them
// (permanently, for a response that simply omits the field). It is never mutated.
const NONE = [];

// What the dialog says about THIS organization. users_count and events_count are already on
// every row (the list endpoint computes both), and they are two of the things the server
// refuses a delete over — so the dialog can warn before the operator commits instead of
// confirming a destructive action the backend was always going to reject.
function deleteBody(o) {
  const identity = `${o.name} (${o.id})`;
  const holds = [
    o.users_count ? `${o.users_count} user(s)` : null,
    o.events_count ? `${o.events_count} event(s)` : null,
  ].filter(Boolean);
  if (holds.length) {
    return `${identity} still has ${holds.join(" and ")}. The platform refuses to delete an `
      + `organization that still owns records — remove or reassign them first, or suspend the `
      + `organization instead. Deleting is permanent and cannot be undone.`;
  }
  return `${identity} will be permanently deleted. This cannot be undone. If it still owns `
    + `events, tickets, invitations or commercial records, the delete will be refused and `
    + `nothing will change.`;
}

const PLAN_TONE = { Enterprise: "brand", Pro: "info", Starter: "neutral" };
const STATUS_TONE = { active: "success", trial: "warning", suspended: "danger" };
const SUB_TONE = { active: "success", trial: "warning", past_due: "danger", cancelled: "neutral" };

// Field skins come from the console tokens so a hover or focus change lands on every filter
// row at once, instead of being re-typed per page.
const inputCls = CONSOLE.search;
const selectCls = CONSOLE.select;

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
  const elevation = useElevation("platform");
  const [q, setQ] = useState("");
  const [plan, setPlan] = useState("all");
  const [status, setStatus] = useState("all");
  const [modalOpen, setModalOpen] = useState(false);
  const [editingOrg, setEditingOrg] = useState(null);

  const organizations = data?.organizations || NONE;
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

  // The row whose delete is being confirmed, and the one whose request is in flight.
  const [confirmDelete, setConfirmDelete] = useState(null);
  const [elevationTarget, setElevationTarget] = useState(null);
  const [elevationReady, setElevationReady] = useState(false);
  const [elevationError, setElevationError] = useState(null);
  const [deleting, setDeleting] = useState(false);

  const hasPlatformElevation = elevation.status === "active"
    || (elevationReady && elevation.status === "none");

  const requestDelete = (organization) => {
    setElevationError(null);
    setElevationReady(false);
    if (elevation.status === "active") {
      setConfirmDelete(organization);
    } else {
      setElevationTarget(organization);
    }
  };

  const startElevation = async () => {
    const organization = elevationTarget;
    if (!organization || elevation.elevating) return;
    setElevationError(null);
    try {
      const granted = await elevation.elevate(
        `Delete organization ${organization.name} (${organization.id})`
      );
      if (!granted) {
        setElevationError(
          "You already hold a different elevation. End it from the rail, then elevate for platform operations."
        );
        return;
      }
      setElevationReady(true);
      setElevationTarget(null);
      setConfirmDelete(organization);
    } catch (e) {
      setElevationError(errMsg(e));
    }
  };

  const cancelDeleteFlow = () => {
    if (deleting) return;
    setConfirmDelete(null);
    setElevationTarget(null);
    setElevationReady(false);
    setElevationError(null);
  };

  // The dialog states the row's known deletion preconditions. Elevation is checked BEFORE
  // this final confirmation; the server still revalidates it on every DELETE request.
  const remove = async () => {
    const o = confirmDelete;
    // Guard rather than rely on the disabled button: a second Enter on the confirm button
    // must not open a second DELETE for the same row.
    if (!o || deleting) return;
    setDeleting(true);
    try {
      await api.delete(`/admin/organizations/${o.id}`);
      // Only after the server confirmed it. 204 is the success path; anything else threw.
      toast.success(`${o.name} deleted`);
      setConfirmDelete(null);
      // Both the table and the counters above it come from the same request.
      reload();
    } catch (e) {
      const message = errMsg(e);
      if (e?.response?.status === 403 && /elevat/i.test(message)) {
        // The grant may expire or be revoked after the UI check. Do not retry deletion:
        // refresh the mirrored state and require an explicit elevation and confirmation.
        setConfirmDelete(null);
        setElevationTarget(o);
        setElevationReady(false);
        setElevationError(message);
        elevation.reload();
      } else {
        toast.error(message);
        setConfirmDelete(null);
      }
    } finally {
      setDeleting(false);
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
      <Button variant="ghost" size="sm" iconOnly title={`Delete ${o.name}`} leftIcon={FiTrash2} className="hover:text-rose-600 dark:hover:text-rose-400" onClick={() => requestDelete(o)} />
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

      {modalOpen && (
        <OrgModal
          // Keyed so "edit A, close, edit B" cannot show A's values, even if this stops being
          // conditionally mounted one day.
          key={editingOrg?.id ?? "new"}
          open
          onClose={() => setModalOpen(false)}
          org={editingOrg}
          plans={plans}
          onSaved={reload}
        />
      )}

      <ConfirmDialog
        open={Boolean(confirmDelete) && hasPlatformElevation}
        busy={deleting}
        onClose={cancelDeleteFlow}
        onConfirm={remove}
        title={confirmDelete ? `Delete ${confirmDelete.name}?` : "Delete organization?"}
        body={confirmDelete ? deleteBody(confirmDelete) : null}
        confirmLabel="Delete organization"
      />

      <Modal
        open={Boolean(elevationTarget) || (Boolean(confirmDelete) && !hasPlatformElevation)}
        onClose={cancelDeleteFlow}
        title="Elevated access required"
        size="sm"
        footer={
          <>
            <ConsoleButton variant="secondary" size="sm" onClick={cancelDeleteFlow}>
              Cancel
            </ConsoleButton>
            {elevation.status === "unknown" ? (
              <ConsoleButton
                variant="primary"
                size="sm"
                onClick={elevation.reload}
              >
                Retry access check
              </ConsoleButton>
            ) : (
              <ConsoleButton
                variant="primary"
                size="sm"
                loading={elevation.elevating}
                disabled={elevation.elevating}
                onClick={startElevation}
              >
                Elevate access
              </ConsoleButton>
            )}
          </>
        }
      >
        <div className="space-y-3">
          <div className="flex gap-3">
            <FiLock aria-hidden="true" className="mt-0.5 shrink-0 text-amber-600 dark:text-amber-400" />
            <div>
              <p className="font-medium text-slate-900 dark:text-white">
                {elevationTarget?.name || confirmDelete?.name}
              </p>
              <p className="text-xs text-slate-500 dark:text-slate-400">
                Organization ID: {elevationTarget?.id || confirmDelete?.id}
              </p>
            </div>
          </div>
          <p>
            {elevation.status === "unknown"
              ? "Access state is unknown because the console API is unreachable. No deletion request has been sent."
              : elevationError
                ? "The server did not accept the previous elevation for this action. Re-elevate before confirming deletion."
                : elevation.status === "expired"
                  ? "Your elevation has expired. Start a new platform elevation before confirming deletion."
                  : elevation.status === "wrong_scope"
                    ? "Your current elevation does not cover platform operations. Elevate for platform access before confirming deletion."
                    : "Deleting an organization requires platform elevation. Start an elevation session before confirming deletion. No deletion request has been sent."}
          </p>
          {elevationError && (
            <p role="alert" className="rounded-lg bg-rose-50 px-3 py-2.5 text-xs text-rose-700 dark:bg-rose-500/10 dark:text-rose-300">
              {elevationError}
            </p>
          )}
        </div>
      </Modal>
    </div>
  );
}
