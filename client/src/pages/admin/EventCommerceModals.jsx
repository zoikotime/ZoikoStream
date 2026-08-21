import { useState } from "react";
import Modal from "../../ui/Modal";
import { ConsoleButton as Button } from "../../ui/Button";
import { Input, Textarea, Label, Select } from "../../ui/forms";
import { notify } from "../../ui/Toast";
import { errMsg } from "../../api";

const BILLING_CLASSIFICATIONS = ["commercial", "internal", "demo", "pilot", "sponsored", "complimentary", "qa", "sandbox"];
const BILLING_SOURCES = ["direct_zoikostream", "zoiko_one", "partner", "contract"];
const CAUSE_DOMAINS = ["zoiko_platform", "customer_venue", "contribution_device", "third_party_provider", "audience_device", "force_majeure", "mixed_unknown"];
const SEVERITIES = ["sev1", "sev2", "sev3", "sev4"];
const READINESS_RESULTS = ["not_started", "in_progress", "pass", "conditional_pass", "fail"];
const REMEDY_TYPES = ["credit", "refund", "fee_waiver"];

// Generic small create-modal: a title, a submit handler, and whatever fields the caller
// renders as children. Every commerce construction action below (quote, order, capacity
// hold, payment milestone, readiness check, incident, remedy) is this same shape, so this
// is the one place the save/error/loading plumbing lives instead of copied eight times.
export function ActionModal({ title, submitLabel = "Create", onSubmit, onClose, disabled, children }) {
  const [saving, setSaving] = useState(false);
  const submit = async (e) => {
    e.preventDefault();
    setSaving(true);
    try {
      await onSubmit();
      onClose();
    } catch (e2) {
      notify.error(errMsg(e2));
    } finally {
      setSaving(false);
    }
  };
  return (
    <Modal
      open
      onClose={() => !saving && onClose()}
      title={title}
      size="md"
      footer={
        <>
          <Button variant="secondary" size="sm" onClick={onClose} disabled={saving}>Cancel</Button>
          <Button size="sm" onClick={submit} loading={saving} disabled={disabled}>{submitLabel}</Button>
        </>
      }
    >
      <form onSubmit={submit} className="space-y-4">{children}</form>
    </Modal>
  );
}

export function NewQuoteModal({ catalogVersions, onCreate, onClose }) {
  // Tax starts EMPTY and is required to submit. It used to default to "0", which pre-filled a
  // commercial assumption the person quoting never actually made (ZST-LE-COM-001 L4: tax is
  // determined, never assumed). A genuine zero still just needs typing 0.
  // Currency starts EMPTY and is required, for the same reason tax does: pre-filling "USD"
  // states a commercial fact the person quoting never chose, and a mis-currencied quote is a
  // real financial document. Typing USD is three keystrokes; a wrong currency is a wrong price.
  const [form, setForm] = useState({ catalog_version_id: catalogVersions[0]?.id || "", currency: "", amount: "", tax_amount: "", valid_until: "", notes: "" });
  const set = (k, v) => setForm((f) => ({ ...f, [k]: v }));
  return (
    <ActionModal title="New quote" onClose={onClose}
      disabled={!form.catalog_version_id || !form.amount || form.tax_amount === "" || form.currency.trim().length !== 3}
      onSubmit={() => onCreate({
        catalog_version_id: form.catalog_version_id, currency: form.currency, amount: form.amount,
        tax_amount: form.tax_amount, valid_until: form.valid_until ? new Date(form.valid_until).toISOString() : null,
        notes: form.notes.trim() || null,
      })}
    >
      <div>
        <Label variant="console">Catalog version</Label>
        <Select variant="console" value={form.catalog_version_id} onChange={(e) => set("catalog_version_id", e.target.value)}>
          {catalogVersions.length === 0 && <option value="">No published catalog versions</option>}
          {catalogVersions.map((v) => <option key={v.id} value={v.id}>{v.vertical} · {v.version_label}</option>)}
        </Select>
      </div>
      <div className="grid grid-cols-2 gap-3">
        <div><Label variant="console">Amount</Label><Input variant="console" type="number" min="0" step="0.01" value={form.amount} onChange={(e) => set("amount", e.target.value)} /></div>
        <div><Label variant="console">Tax (required)</Label><Input variant="console" type="number" min="0" step="0.01" placeholder="Determined tax" value={form.tax_amount} onChange={(e) => set("tax_amount", e.target.value)} /></div>
      </div>
      <div><Label variant="console">Currency</Label><Input variant="console" value={form.currency} onChange={(e) => set("currency", e.target.value.toUpperCase())} maxLength={3} /></div>
      <div><Label variant="console">Valid until</Label><Input variant="console" type="datetime-local" value={form.valid_until} onChange={(e) => set("valid_until", e.target.value)} /></div>
      <div><Label variant="console">Notes</Label><Textarea variant="console" rows={2} value={form.notes} onChange={(e) => set("notes", e.target.value)} /></div>
    </ActionModal>
  );
}

export function NewOrderModal({ catalogVersions, serviceProfiles, cancellationPolicies, quotes, onCreate, onClose }) {
  const [form, setForm] = useState({
    catalog_version_id: catalogVersions[0]?.id || "", service_profile_id: "", cancellation_policy_id: "",
    // Empty, not "USD" — an order's currency is a commercial fact, not a default (see NewQuoteModal).
    currency: "", billing_classification: "commercial", billing_source: "direct_zoikostream", quote_id: "",
  });
  const set = (k, v) => setForm((f) => ({ ...f, [k]: v }));
  const acceptedQuotes = quotes.filter((q) => q.status === "accepted");
  return (
    <ActionModal title="New order" onClose={onClose}
      disabled={!form.catalog_version_id || form.currency.trim().length !== 3}
      onSubmit={() => onCreate({
        catalog_version_id: form.catalog_version_id,
        service_profile_id: form.service_profile_id || null,
        cancellation_policy_id: form.cancellation_policy_id || null,
        currency: form.currency, billing_classification: form.billing_classification,
        billing_source: form.billing_source, quote_id: form.quote_id || null,
        purchaser_type: "organization",
      })}
    >
      <div>
        <Label variant="console">Catalog version</Label>
        <Select variant="console" value={form.catalog_version_id} onChange={(e) => set("catalog_version_id", e.target.value)}>
          {catalogVersions.length === 0 && <option value="">No published catalog versions</option>}
          {catalogVersions.map((v) => <option key={v.id} value={v.id}>{v.vertical} · {v.version_label}</option>)}
        </Select>
      </div>
      {acceptedQuotes.length > 0 && (
        <div>
          <Label variant="console">Link an accepted quote (optional)</Label>
          <Select variant="console" value={form.quote_id} onChange={(e) => set("quote_id", e.target.value)}>
            <option value="">No linked quote</option>
            {acceptedQuotes.map((q) => <option key={q.id} value={q.id}>v{q.version} · {q.amount} {q.currency}</option>)}
          </Select>
        </div>
      )}
      <div>
        <Label variant="console">Service profile (risk tier)</Label>
        <Select variant="console" value={form.service_profile_id} onChange={(e) => set("service_profile_id", e.target.value)}>
          <option value="">None (R0)</option>
          {serviceProfiles.map((p) => <option key={p.id} value={p.id}>{p.risk_tier.toUpperCase()} · {p.name}</option>)}
        </Select>
      </div>
      <div>
        <Label variant="console">Cancellation policy</Label>
        <Select variant="console" value={form.cancellation_policy_id} onChange={(e) => set("cancellation_policy_id", e.target.value)}>
          <option value="">None</option>
          {cancellationPolicies.map((p) => <option key={p.id} value={p.id}>{p.vertical} · {p.version_label}</option>)}
        </Select>
      </div>
      <div className="grid grid-cols-2 gap-3">
        <div>
          <Label variant="console">Billing classification</Label>
          <Select variant="console" value={form.billing_classification} onChange={(e) => set("billing_classification", e.target.value)}>
            {BILLING_CLASSIFICATIONS.map((c) => <option key={c} value={c}>{c}</option>)}
          </Select>
        </div>
        <div>
          <Label variant="console">Billing source</Label>
          <Select variant="console" value={form.billing_source} onChange={(e) => set("billing_source", e.target.value)}>
            {BILLING_SOURCES.map((s) => <option key={s} value={s}>{s}</option>)}
          </Select>
        </div>
      </div>
      <div><Label variant="console">Currency</Label><Input variant="console" value={form.currency} onChange={(e) => set("currency", e.target.value.toUpperCase())} maxLength={3} /></div>
    </ActionModal>
  );
}

export function AddLineModal({ catalogLines, onCreate, onClose }) {
  const [form, setForm] = useState({ catalog_line_id: catalogLines[0]?.id || "", quantity: "1", is_addon: false, is_complimentary: false });
  const set = (k, v) => setForm((f) => ({ ...f, [k]: v }));
  return (
    <ActionModal title="Add order line" onClose={onClose} disabled={!form.catalog_line_id}
      onSubmit={() => onCreate({ catalog_line_id: form.catalog_line_id, quantity: form.quantity, is_addon: form.is_addon, is_complimentary: form.is_complimentary })}
    >
      <div>
        <Label variant="console">Catalog line</Label>
        <Select variant="console" value={form.catalog_line_id} onChange={(e) => set("catalog_line_id", e.target.value)}>
          {catalogLines.length === 0 && <option value="">No lines in this catalog version</option>}
          {catalogLines.map((l) => <option key={l.id} value={l.id}>{l.name} — {l.unit_price} {l.currency}</option>)}
        </Select>
      </div>
      <div><Label variant="console">Quantity</Label><Input variant="console" type="number" min="0.01" step="0.01" value={form.quantity} onChange={(e) => set("quantity", e.target.value)} /></div>
      <label className="flex items-center gap-2 text-sm text-slate-600 dark:text-slate-300">
        <input type="checkbox" checked={form.is_addon} onChange={(e) => set("is_addon", e.target.checked)} className="h-4 w-4" /> Add-on
      </label>
      <label className="flex items-center gap-2 text-sm text-slate-600 dark:text-slate-300">
        <input type="checkbox" checked={form.is_complimentary} onChange={(e) => set("is_complimentary", e.target.checked)} className="h-4 w-4" /> Complimentary (priced at $0)
      </label>
    </ActionModal>
  );
}

export function CapacityHoldModal({ onCreate, onClose }) {
  const [form, setForm] = useState({ resource_type: "", quantity: "1", hold_minutes: "30", region: "" });
  const set = (k, v) => setForm((f) => ({ ...f, [k]: v }));
  return (
    <ActionModal title="Soft-hold capacity" onClose={onClose} disabled={!form.resource_type.trim()}
      onSubmit={() => onCreate({ resource_type: form.resource_type.trim(), quantity: Number(form.quantity) || 1, hold_minutes: Number(form.hold_minutes) || 30, region: form.region.trim() || null })}
    >
      <div><Label variant="console">Resource type</Label><Input variant="console" value={form.resource_type} onChange={(e) => set("resource_type", e.target.value)} placeholder="e.g. backup_contribution, dual_recording" /></div>
      <div className="grid grid-cols-2 gap-3">
        <div><Label variant="console">Quantity</Label><Input variant="console" type="number" min="1" value={form.quantity} onChange={(e) => set("quantity", e.target.value)} /></div>
        <div><Label variant="console">Hold (minutes)</Label><Input variant="console" type="number" min="1" value={form.hold_minutes} onChange={(e) => set("hold_minutes", e.target.value)} /></div>
      </div>
      <div><Label variant="console">Region (optional)</Label><Input variant="console" value={form.region} onChange={(e) => set("region", e.target.value)} /></div>
    </ActionModal>
  );
}

export function PaymentScheduleModal({ onCreate, onClose }) {
  const [form, setForm] = useState({ milestone: "deposit", amount: "", due_at: "", required_before_ready: true });
  const set = (k, v) => setForm((f) => ({ ...f, [k]: v }));
  return (
    <ActionModal title="Add payment milestone" onClose={onClose} disabled={!form.amount}
      onSubmit={() => onCreate({ milestone: form.milestone.trim() || "deposit", amount: form.amount, due_at: form.due_at ? new Date(form.due_at).toISOString() : null, required_before_ready: form.required_before_ready })}
    >
      <div><Label variant="console">Milestone</Label><Input variant="console" value={form.milestone} onChange={(e) => set("milestone", e.target.value)} placeholder="deposit, final, overage…" /></div>
      <div><Label variant="console">Amount</Label><Input variant="console" type="number" min="0.01" step="0.01" value={form.amount} onChange={(e) => set("amount", e.target.value)} /></div>
      <div><Label variant="console">Due at</Label><Input variant="console" type="datetime-local" value={form.due_at} onChange={(e) => set("due_at", e.target.value)} /></div>
      <label className="flex items-center gap-2 text-sm text-slate-600 dark:text-slate-300">
        <input type="checkbox" checked={form.required_before_ready} onChange={(e) => set("required_before_ready", e.target.checked)} className="h-4 w-4" /> Required before READY
      </label>
    </ActionModal>
  );
}

export function ReadinessCheckModal({ requiredChecks, onCreate, onClose }) {
  const [form, setForm] = useState({ check_code: requiredChecks[0] || "", status: "pass", evidence_reference: "" });
  const set = (k, v) => setForm((f) => ({ ...f, [k]: v }));
  return (
    <ActionModal title="Record readiness check" onClose={onClose} disabled={!form.check_code.trim()}
      onSubmit={() => onCreate({ check_code: form.check_code.trim(), status: form.status, evidence_reference: form.evidence_reference.trim() || null })}
    >
      <div>
        <Label variant="console">Check</Label>
        <Input variant="console" list="zk-check-codes" value={form.check_code} onChange={(e) => set("check_code", e.target.value)} placeholder="backup_contribution" />
        <datalist id="zk-check-codes">{requiredChecks.map((c) => <option key={c} value={c} />)}</datalist>
      </div>
      <div>
        <Label variant="console">Result</Label>
        <Select variant="console" value={form.status} onChange={(e) => set("status", e.target.value)}>
          {READINESS_RESULTS.map((r) => <option key={r} value={r}>{r}</option>)}
        </Select>
      </div>
      <div><Label variant="console">Evidence reference</Label><Input variant="console" value={form.evidence_reference} onChange={(e) => set("evidence_reference", e.target.value)} placeholder="Link, ticket, note…" /></div>
    </ActionModal>
  );
}

export function IncidentModal({ orders, onCreate, onClose }) {
  const [form, setForm] = useState({ severity: "sev3", cause_domain: "mixed_unknown", affected_service: "", impact_description: "", event_order_id: orders[0]?.id || "" });
  const set = (k, v) => setForm((f) => ({ ...f, [k]: v }));
  return (
    <ActionModal title="Open incident" onClose={onClose}
      onSubmit={() => onCreate({ severity: form.severity, cause_domain: form.cause_domain, affected_service: form.affected_service.trim() || null, impact_description: form.impact_description.trim() || null, event_order_id: form.event_order_id || null })}
    >
      <div className="grid grid-cols-2 gap-3">
        <div>
          <Label variant="console">Severity</Label>
          <Select variant="console" value={form.severity} onChange={(e) => set("severity", e.target.value)}>
            {SEVERITIES.map((s) => <option key={s} value={s}>{s.toUpperCase()}</option>)}
          </Select>
        </div>
        <div>
          <Label variant="console">Cause domain</Label>
          <Select variant="console" value={form.cause_domain} onChange={(e) => set("cause_domain", e.target.value)}>
            {CAUSE_DOMAINS.map((c) => <option key={c} value={c}>{c.replaceAll("_", " ")}</option>)}
          </Select>
        </div>
      </div>
      <div><Label variant="console">Affected service</Label><Input variant="console" value={form.affected_service} onChange={(e) => set("affected_service", e.target.value)} placeholder="ingest, recording, delivery…" /></div>
      <div><Label variant="console">Impact description</Label><Textarea variant="console" rows={3} value={form.impact_description} onChange={(e) => set("impact_description", e.target.value)} /></div>
    </ActionModal>
  );
}

export function RemedyModal({ orders, incidents, onCreate, onClose }) {
  const [form, setForm] = useState({
    incident_id: incidents[0]?.id || "", event_order_id: orders[0]?.id || "",
    remedy_type: "credit", amount: "", reason_code: "", policy_version: "",
  });
  const set = (k, v) => setForm((f) => ({ ...f, [k]: v }));
  return (
    <ActionModal title="Propose remedy" onClose={onClose} disabled={!form.incident_id || !form.event_order_id || !form.amount || !form.reason_code.trim()}
      onSubmit={() => onCreate({ incident_id: form.incident_id, event_order_id: form.event_order_id, remedy_type: form.remedy_type, amount: form.amount, reason_code: form.reason_code.trim(), policy_version: form.policy_version.trim() || null })}
    >
      <div>
        <Label variant="console">Incident</Label>
        <Select variant="console" value={form.incident_id} onChange={(e) => set("incident_id", e.target.value)}>
          {incidents.length === 0 && <option value="">No incidents for this event</option>}
          {incidents.map((inc) => <option key={inc.id} value={inc.id}>{inc.affected_service || inc.cause_domain} · {inc.severity.toUpperCase()}</option>)}
        </Select>
      </div>
      <div>
        <Label variant="console">Order</Label>
        <Select variant="console" value={form.event_order_id} onChange={(e) => set("event_order_id", e.target.value)}>
          {orders.length === 0 && <option value="">No orders for this event</option>}
          {orders.map((o) => <option key={o.id} value={o.id}>v{o.order_version} · {o.total_amount} {o.currency}</option>)}
        </Select>
      </div>
      <div>
        <Label variant="console">Type</Label>
        <Select variant="console" value={form.remedy_type} onChange={(e) => set("remedy_type", e.target.value)}>
          {REMEDY_TYPES.map((t) => <option key={t} value={t}>{t.replaceAll("_", " ")}</option>)}
        </Select>
      </div>
      <div><Label variant="console">Amount</Label><Input variant="console" type="number" min="0.01" step="0.01" value={form.amount} onChange={(e) => set("amount", e.target.value)} /></div>
      <div><Label variant="console">Reason code</Label><Input variant="console" value={form.reason_code} onChange={(e) => set("reason_code", e.target.value)} placeholder="platform_degradation" /></div>
      <div><Label variant="console">Policy version (optional)</Label><Input variant="console" value={form.policy_version} onChange={(e) => set("policy_version", e.target.value)} /></div>
      <p className="text-xs text-amber-600 dark:text-amber-400">
        Maker-checker: whoever approves this must be a different user than you — you cannot approve your own request.
      </p>
    </ActionModal>
  );
}
