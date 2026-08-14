import { useMemo, useState } from "react";
import { FiCheckCircle, FiFileText, FiPlus, FiRefreshCw, FiSearch } from "react-icons/fi";
import {
  Badge, Button, DataTable, DetailField, KpiCard, Panel, CONSOLE, cx, type,
} from "../../components/admin";
import ConsoleScreen from "../../components/admin/ConsoleScreen";
import Drawer from "../../ui/Drawer";
import Modal from "../../ui/Modal";
import { Input, Label, Select, Textarea } from "../../ui/forms";
import api, { errMsg } from "../../api";
import useApi from "../../hooks/useApi";
import useInterval from "../../hooks/useInterval";
import { notify } from "../../ui/Toast";

// Governance — real obligation tracking. GET/POST/PATCH against /admin/governance-records:
// the same `governance_records` table the Command Center's action queues already read for
// two kinds ("single_path_override", the readiness gate; "break_glass", the elevation
// review widget) — this page is the write side plus the general-purpose kinds a
// compliance/governance workflow actually needs day to day.
//
// Deliberately one generic record shape (kind, org, detail, due date, status) rather than
// the eight-odd specialized subsystems a full GRC suite eventually wants (a DPIA workflow
// engine, a legal-hold litigation service, a data-subject-rights processor, evidence
// storage with integrity hashes) — those don't exist in this build. What's real here is
// real: opening an obligation, tracking its owner-free due date and status, and resolving
// it with an audit trail (every mutation goes through _audit in routers/admin.py).

const KINDS = ["dpia", "legal_hold", "privacy_request", "access_review", "exception", "obligation"];
const KIND_LABEL = {
  dpia: "DPIA", legal_hold: "Legal hold", privacy_request: "Privacy request",
  access_review: "Access review", exception: "Exception", obligation: "Obligation",
  single_path_override: "Single-path override", break_glass: "Break-glass review",
};
const STATUSES = ["open", "in_review", "resolved"];
const STATUS_TONE = { open: "warning", in_review: "info", resolved: "success" };

const REFRESH_MS = 30000;

function useGovernanceData(kind, status) {
  return useApi(() =>
    api
      .get("/admin/governance-records", { params: { kind: kind === "all" ? undefined : kind, status: status === "all" ? undefined : status, page_size: 200 } })
      .then((r) => ({ items: r.data.items, fetched_at: Date.now() }))
  );
}

function useOrgOptions() {
  return useApi(() => api.get("/admin/organizations", { params: { page_size: 200 } }).then((r) => r.data.items));
}

function isOverdue(r) {
  return r.status !== "resolved" && r.due_at && new Date(r.due_at) < new Date();
}

function OpenRecordModal({ open, onClose, orgs, onCreated }) {
  const [kind, setKind] = useState(KINDS[0]);
  const [detail, setDetail] = useState("");
  const [orgId, setOrgId] = useState("");
  const [dueAt, setDueAt] = useState("");
  const [busy, setBusy] = useState(false);

  const reset = () => {
    setKind(KINDS[0]);
    setDetail("");
    setOrgId("");
    setDueAt("");
  };

  const submit = async () => {
    setBusy(true);
    try {
      await api.post("/admin/governance-records", {
        kind, detail: detail.trim() || null, org_id: orgId || null,
        due_at: dueAt ? new Date(dueAt).toISOString() : null,
      });
      notify.success("Governance record opened");
      reset();
      onCreated();
      onClose();
    } catch (e) {
      notify.error(errMsg(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal
      open={open}
      onClose={busy ? () => {} : onClose}
      title="Open a governance record"
      size="lg"
      footer={
        <>
          <Button variant="secondary" size="sm" onClick={onClose} disabled={busy}>Cancel</Button>
          <Button size="sm" loading={busy} disabled={busy} onClick={submit}>Open record</Button>
        </>
      }
    >
      <div className="space-y-4">
        <div>
          <Label variant="console" htmlFor="gov-kind">Kind</Label>
          <Select id="gov-kind" variant="console" value={kind} onChange={(e) => setKind(e.target.value)}>
            {KINDS.map((k) => <option key={k} value={k}>{KIND_LABEL[k]}</option>)}
          </Select>
        </div>
        <div>
          <Label variant="console" htmlFor="gov-detail">Detail</Label>
          <Textarea id="gov-detail" variant="console" value={detail} onChange={(e) => setDetail(e.target.value)} rows={3} />
        </div>
        <div className="grid gap-4 sm:grid-cols-2">
          <div>
            <Label variant="console" htmlFor="gov-org">Organization (optional)</Label>
            <Select id="gov-org" variant="console" value={orgId} onChange={(e) => setOrgId(e.target.value)}>
              <option value="">Not tied to one organization</option>
              {(orgs || []).map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}
            </Select>
          </div>
          <div>
            <Label variant="console" htmlFor="gov-due">Due date (optional)</Label>
            <Input id="gov-due" variant="console" type="date" value={dueAt} onChange={(e) => setDueAt(e.target.value)} />
          </div>
        </div>
      </div>
    </Modal>
  );
}

function RecordDrawer({ record, open, onClose, onUpdated }) {
  const [busy, setBusy] = useState(false);
  if (!record) return <Drawer open={open} onClose={onClose} title="Governance record" />;

  const patch = async (fields) => {
    setBusy(true);
    try {
      await api.patch(`/admin/governance-records/${record.id}`, fields);
      notify.success("Record updated");
      onUpdated();
    } catch (e) {
      notify.error(errMsg(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Drawer open={open} onClose={onClose} title={KIND_LABEL[record.kind] || record.kind} width="w-[28rem] max-w-[90vw]">
      {record.detail && <p className={cx("text-[13px]", CONSOLE.body)}>{record.detail}</p>}

      <div className="mt-4 grid grid-cols-2 gap-3">
        <DetailField label="Organization" value={record.organization_name || "—"} />
        <DetailField label="Status" value={<Badge tone={STATUS_TONE[record.status]}>{record.status}</Badge>} />
        <DetailField label="Opened" value={record.opened_at ? new Date(record.opened_at).toLocaleString() : "—"} />
        <DetailField
          label="Due"
          value={
            record.due_at ? (
              <span className={isOverdue(record) ? "text-rose-600 dark:text-rose-400" : undefined}>
                {new Date(record.due_at).toLocaleDateString()}{isOverdue(record) ? " · overdue" : ""}
              </span>
            ) : "—"
          }
        />
        <DetailField label="Resolved" value={record.resolved_at ? new Date(record.resolved_at).toLocaleString() : "—"} />
      </div>

      <div className="mt-5">
        <Label variant="console" htmlFor="gov-edit-status">Status</Label>
        <Select id="gov-edit-status" variant="console" value={record.status} disabled={busy} onChange={(e) => patch({ status: e.target.value })}>
          {STATUSES.map((s) => <option key={s} value={s}>{s}</option>)}
        </Select>
      </div>

      {record.status !== "resolved" && (
        <Button className="mt-4" size="sm" leftIcon={FiCheckCircle} loading={busy} disabled={busy} onClick={() => patch({ status: "resolved" })}>
          Mark resolved
        </Button>
      )}
    </Drawer>
  );
}

export default function Governance() {
  const [kind, setKind] = useState("all");
  const [status, setStatus] = useState("all");
  const [q, setQ] = useState("");
  const [selected, setSelected] = useState(null);
  const [createOpen, setCreateOpen] = useState(false);

  const { data, loading, error, reload } = useGovernanceData(kind, status);
  const { data: orgs } = useOrgOptions();

  useInterval(reload, REFRESH_MS);
  const [ageSeconds, setAgeSeconds] = useState(0);
  useInterval(() => setAgeSeconds(Math.floor((Date.now() - data.fetched_at) / 1000)), 1000, Boolean(data));

  const records = data?.items || [];
  const selectedLive = selected ? records.find((r) => r.id === selected.id) || selected : null;

  const counts = useMemo(() => ({
    open: records.filter((r) => r.status !== "resolved").length,
    overdue: records.filter(isOverdue).length,
    resolved: records.filter((r) => r.status === "resolved").length,
  }), [records]);

  const rows = useMemo(() => {
    const query = q.trim().toLowerCase();
    return records.filter((r) => {
      if (query && !`${r.detail || ""} ${r.organization_name || ""}`.toLowerCase().includes(query)) return false;
      return true;
    });
  }, [records, q]);

  const columns = [
    { key: "kind", header: "Kind", render: (r) => <Badge tone="neutral">{KIND_LABEL[r.kind] || r.kind}</Badge> },
    { key: "detail", header: "Detail", render: (r) => <p className={cx("min-w-0 truncate text-[13px]", CONSOLE.body)}>{r.detail || "—"}</p> },
    { key: "organization_name", header: "Organization", render: (r) => r.organization_name || "—" },
    { key: "status", header: "Status", render: (r) => <Badge tone={STATUS_TONE[r.status]} dot>{r.status}</Badge> },
    {
      key: "due_at", header: "Due", align: "right",
      render: (r) => (
        <span className={cx("text-[12px]", type.mono, isOverdue(r) ? "font-semibold text-rose-600 dark:text-rose-400" : CONSOLE.muted)}>
          {r.due_at ? new Date(r.due_at).toLocaleDateString() : "—"}
        </span>
      ),
    },
    { key: "action", header: "Action", align: "right", render: (r) => (
      <Button variant="secondary" size="sm" onClick={() => setSelected(r)} aria-label="Open governance record">Open</Button>
    ) },
  ];

  return (
    <ConsoleScreen
      title="Governance"
      subtitle="Obligation tracking, tied to the same governance_records table the Command Center's action queues already read."
      ageSeconds={ageSeconds}
      loading={loading}
      error={error}
      hasData={Boolean(data)}
      endpoint="/admin/governance-records"
      onRetry={reload}
      actions={
        <>
          <Button variant="secondary" leftIcon={FiRefreshCw} onClick={reload}>Refresh</Button>
          <Button leftIcon={FiPlus} onClick={() => setCreateOpen(true)}>Open record</Button>
        </>
      }
    >
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        <KpiCard label="Open" value={counts.open} pressed={status === "open"} onClick={() => setStatus(status === "open" ? "all" : "open")} />
        <KpiCard label="Overdue" value={counts.overdue} tone={counts.overdue ? "text-rose-600 dark:text-rose-400" : undefined} />
        <KpiCard label="Resolved" value={counts.resolved} pressed={status === "resolved"} onClick={() => setStatus(status === "resolved" ? "all" : "resolved")} />
      </div>

      <Panel title="Records" flush>
        <div className="flex flex-wrap items-center gap-3 px-4 py-3">
          <div className="relative min-w-[220px] flex-1">
            <FiSearch className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" aria-hidden="true" />
            <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search detail or organization…" aria-label="Search governance records" className={CONSOLE.search} />
          </div>
          <select value={kind} onChange={(e) => setKind(e.target.value)} className={CONSOLE.select} aria-label="Filter by kind">
            <option value="all">All kinds</option>
            {KINDS.map((k) => <option key={k} value={k}>{KIND_LABEL[k]}</option>)}
          </select>
          <select value={status} onChange={(e) => setStatus(e.target.value)} className={CONSOLE.select} aria-label="Filter by status">
            <option value="all">All statuses</option>
            {STATUSES.map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
        </div>
        <div className={cx("border-t", CONSOLE.divider)} />
        <DataTable
          columns={columns}
          rows={rows}
          rowKey={(r) => r.id}
          onRowClick={setSelected}
          pageSize={10}
          minWidth={900}
          empty={{
            icon: FiFileText,
            title: "No records match these filters",
            description: "Nothing open right now, or nothing satisfies the active filters.",
          }}
        />
      </Panel>

      <OpenRecordModal open={createOpen} onClose={() => setCreateOpen(false)} orgs={orgs} onCreated={reload} />
      <RecordDrawer record={selectedLive} open={Boolean(selected)} onClose={() => setSelected(null)} onUpdated={reload} />
    </ConsoleScreen>
  );
}
