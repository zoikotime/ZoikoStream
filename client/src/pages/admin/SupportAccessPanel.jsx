import { useEffect, useRef, useState } from "react";
import { FiKey, FiPlus } from "react-icons/fi";
import { Badge, Button, CONSOLE, DataTable, Panel, cx } from "../../components/admin";
import OrgFilter from "../../components/admin/OrgFilter";
import { timeAgo } from "../../components/admin/format";
import Modal from "../../ui/Modal";
import ConfirmDialog from "../../ui/ConfirmDialog";
import { Input, Label, Select, Switch, Textarea } from "../../ui/forms";
import { notify } from "../../ui/Toast";
import api, { errMsg } from "../../api";
import useApi from "../../hooks/useApi";
import { useAuth } from "../../auth/AuthContext";
import { SUPPORT_STATUS } from "./supportAccess";

// Authorized support access (ORG-009), platform-wide — GET /admin/support-access.
//
// This is the lifecycle of a Zoiko engineer reaching INTO a tenant: who asked, for which
// organization, with which capabilities, why, whether the organization agreed, and when it
// ends. It is not platform governance (role changes, suspensions), which stays on Identity &
// Access and never passes through here.
//
// The console offers only the steps the platform side really owns, each through its existing
// route and each still refused by the server when it does not apply:
//   Request      POST /admin/support-access              asks; grants nothing
//   Countersign  POST /admin/support-access/{id}/countersign   break-glass second authorizer
//   Start        POST /admin/support-access/{id}/start   the requesting engineer only
//   End          POST /admin/support-access/{id}/end     closes a live session early
// APPROVE and REJECT belong to the organization and are deliberately absent here: offering
// them on the platform would be the bypass ORG-009 exists to prevent.

const STATUSES = ["requested", "approved", "active", "ended", "expired", "denied"];
const STATUS_TONE = {
  requested: "warning", approved: "info", active: "success",
  ended: "neutral", expired: "neutral", denied: "danger",
};
const reasonLabel = (r) => r.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());
const fmt = (iso) => (iso ? new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }) : "—");

function RequestModal({ onClose, onSaved }) {
  const { user } = useAuth();
  const { data: vocab, error: vocabError } = useApi(() =>
    api.get("/admin/support-access/vocabulary").then((r) => r.data));
  const [org, setOrg] = useState(null);
  const [form, setForm] = useState({
    case_reference: "", reason_category: "", engineer_display: user?.full_name || "",
    requested_scope: "", allowed_actions: [], minutes: 60, emergency: false, emergency_reason: "",
  });
  const [saving, setSaving] = useState(false);
  const set = (k, v) => setForm((f) => ({ ...f, [k]: v }));
  const toggleCap = (key) => set("allowed_actions", form.allowed_actions.includes(key)
    ? form.allowed_actions.filter((k) => k !== key) : [...form.allowed_actions, key]);

  const submit = async () => {
    if (!org) return notify.error("Choose the organization to ask");
    if (!form.reason_category) return notify.error("Choose a reason");
    if (!form.allowed_actions.length) return notify.error("Name at least one capability");
    if (form.emergency && !form.emergency_reason.trim()) return notify.error("Emergency access needs a declared reason");
    setSaving(true);
    try {
      await api.post("/admin/support-access", {
        ...form, org_id: org.id, minutes: Number(form.minutes),
        emergency_reason: form.emergency ? form.emergency_reason.trim() : null,
      });
      notify.success(`Access requested from ${org.name}. Nothing is granted until they approve.`);
      onSaved();
      onClose();
    } catch (e) {
      notify.error(errMsg(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal
      open
      onClose={onClose}
      title="Request support access"
      size="lg"
      footer={
        <>
          <Button variant="secondary" size="sm" onClick={onClose}>Cancel</Button>
          <Button size="sm" loading={saving} onClick={submit} disabled={!vocab}>Send request</Button>
        </>
      }
    >
      {vocabError ? (
        <p className="text-sm text-rose-600 dark:text-rose-400">Couldn&apos;t load the request options. {errMsg(vocabError)}</p>
      ) : (
        <div className="space-y-4">
          <p className="text-xs text-slate-500 dark:text-slate-400">
            The organization is told about this request and decides it. Nothing is granted until
            they approve, and the session only begins when you start it.
          </p>
          <div>
            <Label variant="console">Organization</Label>
            <OrgFilter value={org} onChange={setOrg} label="Organization to request access from" />
          </div>
          <div className="grid gap-4 sm:grid-cols-2">
            <div>
              <Label variant="console">Case reference</Label>
              <Input variant="console" value={form.case_reference} onChange={(e) => set("case_reference", e.target.value)} placeholder="e.g. SUP-1042" />
            </div>
            <div>
              <Label variant="console">Reason</Label>
              <Select variant="console" value={form.reason_category} onChange={(e) => set("reason_category", e.target.value)} aria-label="Reason">
                <option value="">Choose…</option>
                {(vocab?.reasons || []).map((r) => <option key={r} value={r}>{reasonLabel(r)}</option>)}
              </Select>
            </div>
            <div>
              <Label variant="console">Engineer (shown to the organization)</Label>
              <Input variant="console" value={form.engineer_display} onChange={(e) => set("engineer_display", e.target.value)} />
            </div>
            <div>
              <Label variant="console">Duration (minutes, max {vocab?.max_minutes ?? "—"})</Label>
              <Input variant="console" type="number" min={1} max={vocab?.max_minutes} value={form.minutes} onChange={(e) => set("minutes", e.target.value)} />
            </div>
          </div>
          <div>
            <Label variant="console">What you need to do</Label>
            <Textarea variant="console" rows={2} value={form.requested_scope} onChange={(e) => set("requested_scope", e.target.value)} placeholder="Shown to the organization's approver" />
          </div>
          <fieldset>
            <legend className="mb-1 text-[13px] font-medium text-slate-700 dark:text-slate-200">Capabilities</legend>
            <div className="grid gap-1.5 sm:grid-cols-2">
              {(vocab?.capabilities || []).map((c) => (
                <label key={c.key} className="flex items-start gap-2 text-[13px] text-slate-700 dark:text-slate-200">
                  <input type="checkbox" checked={form.allowed_actions.includes(c.key)} onChange={() => toggleCap(c.key)} className="mt-0.5" />
                  <span>{c.label}{c.sensitive && <span className="ml-1 text-[11px] text-amber-700 dark:text-amber-300">(sensitive)</span>}</span>
                </label>
              ))}
            </div>
          </fieldset>
          <Switch
            checked={form.emergency}
            onChange={(v) => set("emergency", v)}
            accent="violet"
            label={
              <span>
                <span className="block font-medium text-slate-800 dark:text-slate-100">Emergency (break-glass)</span>
                <span className="block text-xs text-slate-400">
                  Starts without the organization&apos;s approval only after a different operator
                  countersigns. The organization is notified immediately and a post-use review is due.
                </span>
              </span>
            }
          />
          {form.emergency && (
            <div>
              <Label variant="console">Emergency reason</Label>
              <Textarea variant="console" rows={2} value={form.emergency_reason} onChange={(e) => set("emergency_reason", e.target.value)} />
            </div>
          )}
        </div>
      )}
    </Modal>
  );
}

export default function SupportAccessPanel() {
  const [status, setStatus] = useState("all");
  const [org, setOrg] = useState(null);
  const [requesting, setRequesting] = useState(false);
  const [confirmEnd, setConfirmEnd] = useState(null);
  const [busy, setBusy] = useState(null);

  const { data, loading, error, reload } = useApi(() =>
    api.get("/admin/support-access", {
      params: { status: status === "all" ? undefined : status, org_id: org?.id, page_size: 100 },
    }).then((r) => r.data));

  const mounted = useRef(false);
  useEffect(() => {
    if (!mounted.current) { mounted.current = true; return; }
    reload();
  }, [status, org, reload]);

  const act = async (row, step, done) => {
    setBusy(`${row.id}:${step}`);
    try {
      await api.post(`/admin/support-access/${row.id}/${step}`);
      notify.success(done);
      reload();
    } catch (e) {
      notify.error(errMsg(e));
    } finally {
      setBusy(null);
    }
  };

  const rows = data?.items || [];
  const summary = data?.summary;

  // Only what the server would accept from THIS operator. The server still decides.
  const actionsFor = (r) => {
    const out = [];
    const startable = (r.status === "approved" && !r.emergency)
      || (r.emergency && (r.status === "approved" || (r.status === "requested" && r.countersigned)));
    if (r.is_mine && startable) out.push(["start", "Start session", "Support session started"]);
    if (r.emergency && r.status === "requested" && !r.is_mine && !r.countersigned) {
      out.push(["countersign", "Countersign", "Emergency request countersigned"]);
    }
    return out;
  };

  const columns = [
    { key: "case_reference", header: "Case", render: (r) => (
      <>
        <p className="font-medium text-slate-800 dark:text-slate-100">
          {r.case_reference}{r.emergency && <Badge tone="danger" size="sm" className="ml-1.5">Emergency</Badge>}
        </p>
        <p className="text-xs text-slate-400">{reasonLabel(r.reason_category)}</p>
      </>
    ) },
    { key: "organization_name", header: "Organization", render: (r) => r.organization_name || "—" },
    { key: "engineer_display", header: "Engineer", render: (r) => (
      <span>{r.engineer_display}{r.is_mine && <span className="ml-1 text-xs text-slate-400">(you)</span>}</span>
    ) },
    { key: "scope", header: "Scope", render: (r) => (
      <>
        <p className="text-[13px] text-slate-700 dark:text-slate-200">{r.requested_scope}</p>
        <p className="text-[11px] text-slate-400">{(r.allowed_action_list || []).join(", ") || "—"} · {r.requested_minutes} min</p>
      </>
    ) },
    { key: "status", header: "Status", render: (r) => (
      <span title={SUPPORT_STATUS[r.status]?.detail}>
        <Badge tone={STATUS_TONE[r.status] || "neutral"} dot>{SUPPORT_STATUS[r.status]?.label || r.status}</Badge>
      </span>
    ) },
    { key: "decision", header: "Decision", render: (r) => (
      <div className="text-[12px] text-slate-600 dark:text-slate-300">
        <p>Requested {timeAgo(r.requested_at)}</p>
        {r.approved_by_email && <p>Approved by {r.approved_by_email} · {fmt(r.approved_at)}</p>}
        {r.emergency && r.status === "requested" && (
          <p className={r.countersigned ? "text-emerald-700 dark:text-emerald-400" : "text-amber-700 dark:text-amber-300"}>
            {r.countersigned ? "Countersigned" : "Needs a second operator's countersignature"}
          </p>
        )}
        {r.status === "active" && r.expires_at && <p>Ends {fmt(r.expires_at)}</p>}
        {r.ended_at && <p>Ended {fmt(r.ended_at)}</p>}
      </div>
    ) },
    { key: "actions", header: "", align: "right", render: (r) => (
      <div className="flex justify-end gap-2">
        {actionsFor(r).map(([step, label, done]) => (
          <Button key={step} variant="secondary" size="sm" loading={busy === `${r.id}:${step}`} onClick={() => act(r, step, done)}>{label}</Button>
        ))}
        {r.status === "active" && (
          <Button variant="secondary" size="sm" onClick={() => setConfirmEnd(r)}>End</Button>
        )}
      </div>
    ) },
  ];

  return (
    <Panel
      eyebrow="ORG-009"
      title="Authorized support access"
      description="Every request by a Zoiko engineer to reach into a tenant. The organization approves or declines; the platform can only request, countersign an emergency, start its own approved session, or end one."
      flush
      action={<Button size="sm" leftIcon={FiPlus} onClick={() => setRequesting(true)}>Request access</Button>}
    >
      <div className="flex flex-wrap items-center gap-2 px-4 py-3" data-testid="support-access-summary">
        {STATUSES.map((s) => (
          <button
            key={s}
            type="button"
            aria-pressed={status === s}
            onClick={() => setStatus(status === s ? "all" : s)}
            className={cx("rounded-full border px-2.5 py-1 text-[12px]",
              status === s ? "border-violet-400 bg-violet-50 text-violet-700 dark:bg-violet-500/15 dark:text-violet-200"
                : "border-slate-200 text-slate-600 dark:border-white/10 dark:text-neutral-300")}
          >
            {SUPPORT_STATUS[s]?.label || s}: <span className="font-semibold">{summary ? summary[s] : "—"}</span>
          </button>
        ))}
        <div className="ml-auto"><OrgFilter value={org} onChange={setOrg} /></div>
      </div>
      <div className={cx("border-t", CONSOLE.divider)} />
      {error ? (
        <div className="flex items-center justify-between gap-3 px-5 py-6 text-sm text-rose-700 dark:text-rose-300" role="alert">
          <span>Couldn&apos;t load support access requests.</span>
          <Button variant="secondary" size="sm" onClick={reload}>Retry</Button>
        </div>
      ) : (
        <DataTable
          columns={columns}
          rows={rows}
          rowKey={(r) => r.id}
          loading={loading}
          pageSize={10}
          minWidth={1000}
          empty={{
            icon: FiKey,
            title: status === "all" && !org ? "No support access has been requested" : "No requests match these filters",
            description: "Requests appear here from the moment they are raised, whatever the organization decides.",
          }}
        />
      )}
      {data && data.total > rows.length && (
        <p className="px-4 pb-3 text-xs text-slate-500 dark:text-slate-400">
          Showing the newest {rows.length} of {data.total}. Filter by status or organization to narrow.
        </p>
      )}

      {requesting && <RequestModal onClose={() => setRequesting(false)} onSaved={reload} />}
      <ConfirmDialog
        open={Boolean(confirmEnd)}
        onClose={() => setConfirmEnd(null)}
        onConfirm={async () => { const r = confirmEnd; setConfirmEnd(null); await act(r, "end", "Support session ended"); }}
        title="End this support session?"
        body={confirmEnd ? `Access to ${confirmEnd.organization_name || "this organization"} for case ${confirmEnd.case_reference} stops immediately. The organization is notified.` : null}
        confirmLabel="End session"
      />
    </Panel>
  );
}
