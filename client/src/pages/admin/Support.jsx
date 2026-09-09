import { useMemo, useState } from "react";
import toast from "react-hot-toast";
import { FiLifeBuoy, FiPlus, FiTrash2 } from "react-icons/fi";
import { Badge, Button, CONSOLE, DataTable, Panel, StatCard } from "../../components/admin";
import { Select } from "../../ui/forms";
import { timeAgo } from "../../components/admin/format";
import api, { errMsg } from "../../api";
import useApi from "../../hooks/useApi";
import SupportTicketModal from "./SupportTicketModal";

// Stable identity for the "nothing loaded yet" case. The useMemo hooks below take this list
// as a dependency, and a fresh `[]` literal on every render would defeat every one of them
// (permanently, for a response that simply omits the field). It is never mutated.
const NONE = [];

const PRIORITY_TONE = { urgent: "danger", high: "warning", normal: "info", low: "neutral" };
const label = (s) => s.split("_").map((w) => w[0].toUpperCase() + w.slice(1)).join(" ");

// Field skins come from the console tokens so a hover or focus change lands on every filter
// row at once, instead of being re-typed per page.
const selectCls = CONSOLE.select;

function useSupportData() {
  return useApi(() =>
    Promise.all([
      api.get("/admin/support-tickets", { params: { page_size: 100 } }).then((r) => r.data.items),
      api.get("/admin/organizations", { params: { page_size: 100 } }).then((r) => r.data.items),
    ]).then(([tickets, organizations]) => ({ tickets, organizations }))
  );
}

// Support ticket queue — real GET/POST/PATCH/DELETE against /admin/support-tickets. No
// self-service submission exists in the org dashboard yet, so tickets are logged and
// worked from here.
export default function Support() {
  const { data, loading, error, reload } = useSupportData();
  const [status, setStatus] = useState("all");
  const [modalOpen, setModalOpen] = useState(false);

  const tickets = data?.tickets || NONE;
  const organizations = data?.organizations || [];

  const kpis = useMemo(
    () => ({
      open: tickets.filter((t) => t.status === "open").length,
      inProgress: tickets.filter((t) => t.status === "in_progress").length,
      resolved: tickets.filter((t) => t.status === "resolved").length,
      urgent: tickets.filter((t) => t.priority === "urgent" && t.status !== "resolved" && t.status !== "closed").length,
    }),
    [tickets]
  );

  const filtered = useMemo(
    () => (status === "all" ? tickets : tickets.filter((t) => t.status === status)),
    [tickets, status]
  );

  const setTicketStatus = async (t, next) => {
    try {
      await api.patch(`/admin/support-tickets/${t.id}`, { status: next });
      toast.success(`${t.subject} → ${label(next)}`);
      reload();
    } catch (e) {
      toast.error(errMsg(e));
    }
  };

  const remove = async (t) => {
    if (!window.confirm(`Delete ticket "${t.subject}"? This cannot be undone.`)) return;
    try {
      await api.delete(`/admin/support-tickets/${t.id}`);
      toast.success("Ticket deleted");
      reload();
    } catch (e) {
      toast.error(errMsg(e));
    }
  };

  const columns = [
    { key: "subject", header: "Ticket", render: (t) => (
      <>
        <p className="font-medium text-slate-800 dark:text-slate-100">{t.subject}</p>
        <p className="text-xs text-slate-400">{t.organization_name || "—"}{t.requester_email ? ` · ${t.requester_email}` : ""}</p>
      </>
    ) },
    { key: "priority", header: "Priority", render: (t) => <Badge tone={PRIORITY_TONE[t.priority] || "neutral"}>{label(t.priority)}</Badge> },
    { key: "status", header: "Status", render: (t) => (
      <select
        value={t.status}
        onChange={(e) => setTicketStatus(t, e.target.value)}
        className={selectCls}
        aria-label={`Status for ${t.subject}`}
      >
        <option value="open">Open</option>
        <option value="in_progress">In Progress</option>
        <option value="resolved">Resolved</option>
        <option value="closed">Closed</option>
      </select>
    ) },
    { key: "created_at", header: "Opened", align: "right", render: (t) => (t.created_at ? timeAgo(t.created_at) : "—") },
  ];

  const rowActions = (t) => (
    <Button variant="ghost" size="sm" iconOnly title="Delete ticket" leftIcon={FiTrash2} className="hover:text-rose-600 dark:hover:text-rose-400" onClick={() => remove(t)} />
  );

  if (error) {
    return (
      <div className="mx-auto max-w-[1200px] rounded-xl border border-rose-200 bg-rose-50 px-5 py-4 text-sm text-rose-700 dark:border-rose-500/20 dark:bg-rose-500/10 dark:text-rose-300">
        Couldn't load support tickets. Try refreshing the page.
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-[1200px] space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-[24px] font-semibold tracking-tight text-slate-900 dark:text-white">Support</h1>
          <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">Ticket queue across every organization</p>
        </div>
        <Button leftIcon={FiPlus} onClick={() => setModalOpen(true)} disabled={organizations.length === 0}>Log Ticket</Button>
      </div>

      <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
        <StatCard label="Open" value={kpis.open} loading={loading} />
        <StatCard label="In Progress" value={kpis.inProgress} loading={loading} />
        <StatCard label="Resolved" value={kpis.resolved} loading={loading} />
        <StatCard label="Urgent (unresolved)" value={kpis.urgent} loading={loading} />
      </div>

      <Panel flush>
        <div className="flex flex-wrap items-center gap-3 px-4 py-3">
          <Select variant="console" className="w-48" value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Filter by status">
            <option value="all">All statuses</option>
            <option value="open">Open</option>
            <option value="in_progress">In Progress</option>
            <option value="resolved">Resolved</option>
            <option value="closed">Closed</option>
          </Select>
        </div>
        <div className="border-t border-slate-100 dark:border-slate-800/70" />
        <DataTable
          columns={columns}
          rows={filtered}
          rowKey={(t) => t.id}
          loading={loading}
          rowActions={rowActions}
          pageSize={10}
          minWidth={700}
          empty={{
            icon: FiLifeBuoy,
            title: "No support tickets",
            description: status === "all" ? "Nothing logged yet." : `No ${label(status).toLowerCase()} tickets.`,
          }}
        />
      </Panel>

      {modalOpen && (
        <SupportTicketModal open onClose={() => setModalOpen(false)} organizations={organizations} onSaved={reload} />
      )}
    </div>
  );
}
