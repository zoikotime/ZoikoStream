import { useMemo, useState } from "react";
import { FiPlus, FiRefreshCw, FiSearch, FiShield } from "react-icons/fi";
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

// Stable identity for the "nothing loaded yet" case. The useMemo hooks below take this list
// as a dependency, and a fresh `[]` literal on every render would defeat every one of them
// (permanently, for a response that simply omits the field). It is never mutated.
const NONE = [];

// Trust & Safety — real case tracking for tenant-originated security/policy incidents.
// GET/POST/PATCH against /admin/incidents (kind=security): the same `incidents` table the
// Command Center's Incidents panel and action queues already read (services/ops.py) —
// this page is the write side that table never had before.
//
// Deliberately narrower than a full content-moderation console: there is no chat/Q&A/
// audience-report queue here (Live Events has none in this build), no automated confidence
// scoring, no appeals workflow — those would need subsystems (a moderation queue tied to
// live messages, an evidence store) that don't exist yet. What's here is real: opening a
// case, tracking its severity/status/commander, and linking it to the affected
// organization, whose actual restriction lever (suspend/activate) already lives on the
// Organizations console (/admin/organizations) rather than being duplicated here.

const SEVERITIES = ["sev1", "sev2", "sev3", "sev4"];
const SEVERITY_LABEL = { sev1: "Sev1 · Critical", sev2: "Sev2 · High", sev3: "Sev3 · Moderate", sev4: "Sev4 · Low" };
const SEVERITY_TONE = { sev1: "danger", sev2: "danger", sev3: "warning", sev4: "neutral" };
const STATUSES = ["open", "investigating", "monitoring", "resolved"];
const STATUS_TONE = { open: "danger", investigating: "warning", monitoring: "info", resolved: "success" };

const REFRESH_MS = 30000;

function useIncidentsData(status, orgId) {
  return useApi(() =>
    api
      .get("/admin/incidents", { params: { kind: "security", status: status === "all" ? undefined : status, org_id: orgId === "all" ? undefined : orgId, page_size: 200 } })
      .then((r) => ({ items: r.data.items, fetched_at: Date.now() }))
  );
}

function useOrgOptions() {
  return useApi(() => api.get("/admin/organizations", { params: { page_size: 200 } }).then((r) => r.data.items));
}

function OpenCaseModal({ open, onClose, orgs, onCreated }) {
  const [title, setTitle] = useState("");
  const [detail, setDetail] = useState("");
  const [severity, setSeverity] = useState("sev3");
  const [orgId, setOrgId] = useState("");
  const [busy, setBusy] = useState(false);

  const reset = () => {
    setTitle("");
    setDetail("");
    setSeverity("sev3");
    setOrgId("");
  };

  const submit = async () => {
    if (!title.trim()) return;
    setBusy(true);
    try {
      await api.post("/admin/incidents", {
        title: title.trim(), detail: detail.trim() || null, severity, kind: "security",
        org_id: orgId || null,
      });
      notify.success("Case opened");
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
      title="Open a Trust & Safety case"
      size="lg"
      footer={
        <>
          <Button variant="secondary" size="sm" onClick={onClose} disabled={busy}>Cancel</Button>
          <Button size="sm" loading={busy} disabled={busy || !title.trim()} onClick={submit}>Open case</Button>
        </>
      }
    >
      <div className="space-y-4">
        <div>
          <Label variant="console" htmlFor="ts-title">Title</Label>
          <Input id="ts-title" variant="console" value={title} onChange={(e) => setTitle(e.target.value)} placeholder="What happened" maxLength={200} />
        </div>
        <div>
          <Label variant="console" htmlFor="ts-detail">Detail</Label>
          <Textarea id="ts-detail" variant="console" value={detail} onChange={(e) => setDetail(e.target.value)} rows={3} />
        </div>
        <div className="grid gap-4 sm:grid-cols-2">
          <div>
            <Label variant="console" htmlFor="ts-severity">Severity</Label>
            <Select id="ts-severity" variant="console" value={severity} onChange={(e) => setSeverity(e.target.value)}>
              {SEVERITIES.map((s) => <option key={s} value={s}>{SEVERITY_LABEL[s]}</option>)}
            </Select>
          </div>
          <div>
            <Label variant="console" htmlFor="ts-org">Organization (optional)</Label>
            <Select id="ts-org" variant="console" value={orgId} onChange={(e) => setOrgId(e.target.value)}>
              <option value="">Not tied to one organization</option>
              {(orgs || []).map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}
            </Select>
          </div>
        </div>
      </div>
    </Modal>
  );
}

function CaseDrawer({ incident, open, onClose, onUpdated }) {
  const [busy, setBusy] = useState(false);
  if (!incident) return <Drawer open={open} onClose={onClose} title="Case" />;

  const patch = async (fields) => {
    setBusy(true);
    try {
      await api.patch(`/admin/incidents/${incident.id}`, fields);
      notify.success("Case updated");
      onUpdated();
    } catch (e) {
      notify.error(errMsg(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Drawer open={open} onClose={onClose} title={incident.title} width="w-[28rem] max-w-[90vw]">
      <p className={cx("font-mono text-[11px]", CONSOLE.faint)}>{incident.ref}</p>
      {incident.detail && <p className={cx("mt-2 text-[13px]", CONSOLE.body)}>{incident.detail}</p>}

      {/* DetailField's own label/value grid reads the sm: breakpoint off the viewport, not
          this ~450px drawer panel — an outer grid-cols-2 squeezes each field too narrow and
          wraps the value character-by-character. Stack instead, per DetailField's own doc
          comment on how a drawer should use it. */}
      <dl className={cx("mt-4 divide-y", CONSOLE.divider)}>
        <DetailField label="Organization" value={incident.organization_name || "—"} />
        <DetailField label="Commander" value={incident.commander || "—"} />
        <DetailField label="Opened" value={incident.started_at ? new Date(incident.started_at).toLocaleString() : "—"} />
        <DetailField label="Resolved" value={incident.resolved_at ? new Date(incident.resolved_at).toLocaleString() : "—"} />
      </dl>

      <div className="mt-5 grid gap-4 sm:grid-cols-2">
        <div>
          <Label variant="console" htmlFor="ts-edit-status">Status</Label>
          <Select
            id="ts-edit-status" variant="console" value={incident.status} disabled={busy}
            onChange={(e) => patch({ status: e.target.value })}
          >
            {STATUSES.map((s) => <option key={s} value={s}>{s}</option>)}
          </Select>
        </div>
        <div>
          <Label variant="console" htmlFor="ts-edit-severity">Severity</Label>
          <Select
            id="ts-edit-severity" variant="console" value={incident.severity} disabled={busy}
            onChange={(e) => patch({ severity: e.target.value })}
          >
            {SEVERITIES.map((s) => <option key={s} value={s}>{SEVERITY_LABEL[s]}</option>)}
          </Select>
        </div>
      </div>

      {incident.org_id && (
        <a
          href="/admin/organizations"
          className="mt-5 inline-block text-[13px] font-medium text-violet-600 hover:text-violet-500 dark:text-violet-400"
        >
          Suspend or manage this organization →
        </a>
      )}
    </Drawer>
  );
}

export default function Security() {
  const [status, setStatus] = useState("all");
  const [orgFilter, setOrgFilter] = useState("all");
  const [q, setQ] = useState("");
  const [severity, setSeverity] = useState("all");
  const [selected, setSelected] = useState(null);
  const [createOpen, setCreateOpen] = useState(false);

  const { data, loading, error, reload } = useIncidentsData(status, orgFilter);
  const { data: orgs } = useOrgOptions();

  useInterval(reload, REFRESH_MS);
  const [ageSeconds, setAgeSeconds] = useState(0);
  useInterval(() => setAgeSeconds(Math.floor((Date.now() - data.fetched_at) / 1000)), 1000, Boolean(data));

  const incidents = data?.items || NONE;

  // If the drawer's selected row is stale after a reload, resync it to the fresh copy.
  const selectedLive = selected ? incidents.find((i) => i.id === selected.id) || selected : null;

  const counts = useMemo(() => ({
    open: incidents.filter((i) => i.status === "open").length,
    critical: incidents.filter((i) => ["sev1", "sev2"].includes(i.severity) && i.status !== "resolved").length,
    resolved: incidents.filter((i) => i.status === "resolved").length,
  }), [incidents]);

  const rows = useMemo(() => {
    const query = q.trim().toLowerCase();
    return incidents.filter((i) => {
      if (query && !`${i.title} ${i.ref} ${i.organization_name || ""}`.toLowerCase().includes(query)) return false;
      if (severity !== "all" && i.severity !== severity) return false;
      return true;
    });
  }, [incidents, q, severity]);

  const columns = [
    { key: "ref", header: "Case", render: (i) => (
      <div className="min-w-0">
        <p className={cx("text-[13px] font-semibold", CONSOLE.heading)}>{i.title}</p>
        <p className={cx("text-[11px]", type.mono, CONSOLE.faint)}>{i.ref}</p>
      </div>
    ) },
    { key: "organization_name", header: "Organization", render: (i) => i.organization_name || "—" },
    { key: "severity", header: "Severity", render: (i) => <Badge tone={SEVERITY_TONE[i.severity]}>{SEVERITY_LABEL[i.severity]}</Badge> },
    { key: "status", header: "Status", render: (i) => <Badge tone={STATUS_TONE[i.status]} dot>{i.status}</Badge> },
    { key: "commander", header: "Commander", render: (i) => i.commander || "—" },
    { key: "started_at", header: "Opened", align: "right", render: (i) => (i.started_at ? new Date(i.started_at).toLocaleDateString() : "—") },
    { key: "action", header: "Action", align: "right", render: (i) => (
      <Button variant="secondary" size="sm" onClick={() => setSelected(i)} aria-label={`Open case ${i.title}`}>Open</Button>
    ) },
  ];

  return (
    <ConsoleScreen
      title="Trust & Safety"
      subtitle="Security and policy case tracking, tied to the same incidents table the Command Center's own Incidents panel reads."
      ageSeconds={ageSeconds}
      loading={loading}
      error={error}
      hasData={Boolean(data)}
      endpoint="/admin/incidents"
      onRetry={reload}
      actions={
        <>
          <Button variant="secondary" leftIcon={FiRefreshCw} onClick={reload}>Refresh</Button>
          <Button leftIcon={FiPlus} onClick={() => setCreateOpen(true)}>Open case</Button>
        </>
      }
    >
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        <KpiCard label="Open" value={counts.open} tone={counts.open ? "text-rose-600 dark:text-rose-400" : undefined} pressed={status === "open"} onClick={() => setStatus(status === "open" ? "all" : "open")} />
        <KpiCard label="Critical (Sev1/Sev2)" value={counts.critical} tone={counts.critical ? "text-rose-600 dark:text-rose-400" : undefined} />
        <KpiCard label="Resolved" value={counts.resolved} pressed={status === "resolved"} onClick={() => setStatus(status === "resolved" ? "all" : "resolved")} />
      </div>

      <Panel title="Cases" flush>
        <div className="flex flex-wrap items-center gap-3 px-4 py-3">
          <div className="relative min-w-[220px] flex-1">
            <FiSearch className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" aria-hidden="true" />
            <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search title, ref, or organization…" aria-label="Search cases" className={CONSOLE.search} />
          </div>
          <select value={status} onChange={(e) => setStatus(e.target.value)} className={CONSOLE.select} aria-label="Filter by status">
            <option value="all">All statuses</option>
            {STATUSES.map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
          <select value={severity} onChange={(e) => setSeverity(e.target.value)} className={CONSOLE.select} aria-label="Filter by severity">
            <option value="all">All severities</option>
            {SEVERITIES.map((s) => <option key={s} value={s}>{SEVERITY_LABEL[s]}</option>)}
          </select>
          <select value={orgFilter} onChange={(e) => setOrgFilter(e.target.value)} className={CONSOLE.select} aria-label="Filter by organization">
            <option value="all">All organizations</option>
            {(orgs || []).map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}
          </select>
        </div>
        <div className={cx("border-t", CONSOLE.divider)} />
        <DataTable
          columns={columns}
          rows={rows}
          rowKey={(i) => i.id}
          onRowClick={setSelected}
          pageSize={10}
          minWidth={900}
          empty={{
            icon: FiShield,
            title: "No cases match these filters",
            description: "Nothing open right now, or nothing satisfies the active filters.",
          }}
        />
      </Panel>

      <OpenCaseModal open={createOpen} onClose={() => setCreateOpen(false)} orgs={orgs} onCreated={reload} />
      <CaseDrawer incident={selectedLive} open={Boolean(selected)} onClose={() => setSelected(null)} onUpdated={reload} />
    </ConsoleScreen>
  );
}
