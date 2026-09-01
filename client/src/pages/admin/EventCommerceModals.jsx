import { useState } from "react";
import Modal from "../../ui/Modal";
import { ConsoleButton as Button } from "../../ui/Button";
import { Input, Textarea, Label, Select } from "../../ui/forms";
import { notify } from "../../ui/Toast";
import { errMsg } from "../../api";
import { cx } from "../../ui/tokens";
import { fmtDateTime } from "../../data/events";
import { money } from "../../utils/money";

const BILLING_CLASSIFICATIONS = ["commercial", "internal", "demo", "pilot", "sponsored", "complimentary", "qa", "sandbox"];
const BILLING_SOURCES = ["direct_zoikostream", "zoiko_one", "partner", "contract"];
const CAUSE_DOMAINS = ["zoiko_platform", "customer_venue", "contribution_device", "third_party_provider", "audience_device", "force_majeure", "mixed_unknown"];
const SEVERITIES = ["sev1", "sev2", "sev3", "sev4"];
const READINESS_RESULTS = ["not_started", "in_progress", "pass", "conditional_pass", "fail"];
const REMEDY_TYPES = ["credit", "refund", "fee_waiver"];

// ── Tax determination (doc L4/L6) ─────────────────────────────────────────────────────────
// An order cannot be charged or invoiced until a determination is recorded: NULL tax means
// UNDETERMINED, not zero, and both order_payable_amount and issue_invoice fail closed on it.
// There was no UI for this at all, which meant the entire checkout and invoice path terminated
// at a 400 that only a hand-crafted API call could clear.
//
// No treatment vocabulary is hard-coded: the codes belong to Finance/Tax. These are common
// examples offered as datalist suggestions, and any value is accepted.
const TAX_TREATMENT_SUGGESTIONS = [
  "standard_rate", "zero_rated", "exempt", "reverse_charge", "out_of_scope",
];

export function TaxDeterminationModal({ order, onClose, onCreate }) {
  const [form, setForm] = useState({
    tax_amount: "", treatment: "", jurisdiction: "", source: "",
    rule_version: "", exemption_reason: "",
  });
  const [saving, setSaving] = useState(false);
  const set = (k, v) => setForm((f) => ({ ...f, [k]: v }));
  const isZero = form.tax_amount !== "" && Number(form.tax_amount) === 0;

  const submit = async (e) => {
    e?.preventDefault();
    if (form.tax_amount === "" || Number(form.tax_amount) < 0) {
      return notify.error("A tax amount of 0 or more is required");
    }
    if (!form.treatment.trim() || !form.jurisdiction.trim() || !form.source.trim()) {
      return notify.error("Treatment, jurisdiction and source are all required");
    }
    if (isZero && !form.exemption_reason.trim()) {
      return notify.error("A zero-tax determination must state why it is zero");
    }
    setSaving(true);
    try {
      await onCreate({
        tax_amount: form.tax_amount,
        treatment: form.treatment.trim(),
        jurisdiction: form.jurisdiction.trim(),
        source: form.source.trim(),
        rule_version: form.rule_version.trim() || null,
        exemption_reason: form.exemption_reason.trim() || null,
      });
      onClose();
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal
      open
      onClose={() => !saving && onClose()}
      title="Record tax determination"
      size="md"
      footer={
        <>
          <Button variant="secondary" size="sm" onClick={onClose} disabled={saving}>Cancel</Button>
          <Button size="sm" onClick={submit} loading={saving}>Record determination</Button>
        </>
      }
    >
      <form onSubmit={submit} className="space-y-4">
        <p className="rounded-lg bg-slate-50 px-3 py-2 text-xs text-slate-500 dark:bg-slate-800/50 dark:text-slate-400">
          Until this is recorded the order's total is provisional — it cannot be charged or
          invoiced. Zero tax is a valid outcome, but only as an explicit determination with a
          stated reason; it is never reached by leaving this blank.
        </p>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <Label variant="console">Tax amount ({order?.currency || "—"})</Label>
            <Input variant="console" type="number" min="0" step="0.01" value={form.tax_amount}
                   onChange={(e) => set("tax_amount", e.target.value)} placeholder="0.00" />
            <p className="mt-1 text-xs text-slate-400">
              Subtotal {order?.subtotal ?? "—"} {order?.currency || ""}
            </p>
          </div>
          <div>
            <Label variant="console">Treatment</Label>
            <Input variant="console" list="zk-tax-treatments" value={form.treatment}
                   onChange={(e) => set("treatment", e.target.value)} placeholder="standard_rate" />
            <datalist id="zk-tax-treatments">
              {TAX_TREATMENT_SUGGESTIONS.map((t) => <option key={t} value={t} />)}
            </datalist>
          </div>
        </div>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <Label variant="console">Jurisdiction</Label>
            <Input variant="console" value={form.jurisdiction} onChange={(e) => set("jurisdiction", e.target.value)} placeholder="GB" />
          </div>
          <div>
            <Label variant="console">Source</Label>
            <Input variant="console" value={form.source} onChange={(e) => set("source", e.target.value)} placeholder="finance_manual" />
          </div>
        </div>
        <div>
          <Label variant="console">Rule version</Label>
          <Input variant="console" value={form.rule_version} onChange={(e) => set("rule_version", e.target.value)} placeholder="optional" />
        </div>
        <div>
          <Label variant="console">
            Exemption / zero-rating reason {isZero && <span className="text-rose-500">(required for zero)</span>}
          </Label>
          <Textarea variant="console" rows={2} value={form.exemption_reason}
                    onChange={(e) => set("exemption_reason", e.target.value)}
                    placeholder={isZero ? "e.g. reverse-charge: customer accounts for VAT" : "optional"} />
        </div>
      </form>
    </Modal>
  );
}

// ── Reschedule (doc Section 9) ────────────────────────────────────────────────────────────

export function RescheduleModal({ onClose, onCreate }) {
  const [form, setForm] = useState({ new_start_time: "", new_end_time: "", reason: "" });
  const [saving, setSaving] = useState(false);
  const set = (k, v) => setForm((f) => ({ ...f, [k]: v }));

  const submit = async (e) => {
    e?.preventDefault();
    if (!form.new_start_time) return notify.error("A new start time is required");
    if (form.reason.trim().length < 3) return notify.error("A reason is required");
    setSaving(true);
    try {
      await onCreate({
        new_start_time: new Date(form.new_start_time).toISOString(),
        new_end_time: form.new_end_time ? new Date(form.new_end_time).toISOString() : null,
        reason: form.reason.trim(),
      });
      onClose();
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal
      open
      onClose={() => !saving && onClose()}
      title="Reschedule event"
      size="md"
      footer={
        <>
          <Button variant="secondary" size="sm" onClick={onClose} disabled={saving}>Cancel</Button>
          <Button size="sm" onClick={submit} loading={saving}>Reschedule</Button>
        </>
      }
    >
      <form onSubmit={submit} className="space-y-4">
        <p className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-300">
          Capacity held for the current window will be <strong>released</strong> — a reservation
          covers a specific time and does not carry over. It must be re-held against the new
          window before the event can be confirmed again. The original window is preserved in
          the reschedule history, and no fee is invented: raise a change order if one applies.
        </p>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <Label variant="console">New start</Label>
            <Input variant="console" type="datetime-local" value={form.new_start_time} onChange={(e) => set("new_start_time", e.target.value)} />
          </div>
          <div>
            <Label variant="console">New end</Label>
            <Input variant="console" type="datetime-local" value={form.new_end_time} onChange={(e) => set("new_end_time", e.target.value)} />
          </div>
        </div>
        <div>
          <Label variant="console">Reason</Label>
          <Textarea variant="console" rows={3} value={form.reason} onChange={(e) => set("reason", e.target.value)}
                    placeholder="Why is this event moving?" />
        </div>
      </form>
    </Modal>
  );
}

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

// ── Commercial operations modals (disputes, change orders, reconciliation, governance) ────
// All build on ActionModal above, so the save/error/close cycle and the console styling are
// identical to every other modal in this file.

const DISPUTE_EVIDENCE_FIELDS = [
  ["narrative", "What happened", "Chronology of the engagement and the delivery"],
  ["service_documentation", "Service documentation", "Contract, order reference, delivery evidence"],
  ["customer_communication", "Customer communication", "Emails or tickets showing agreement"],
];

export function DisputeEvidenceModal({ dispute, onCreate, onClose }) {
  const [form, setForm] = useState({ narrative: "", service_documentation: "", customer_communication: "" });
  const set = (k, v) => setForm((f) => ({ ...f, [k]: v }));
  // The API takes a free-form object; these three fields are the shape a card network actually
  // asks for. At least one must be filled — the backend rejects an empty evidence object, and
  // submitting nothing would burn the one submission the case gets.
  const filled = Object.values(form).filter((v) => v.trim()).length;

  return (
    <ActionModal
      title="Submit dispute evidence"
      submitLabel="Submit evidence"
      disabled={filled === 0}
      onClose={onClose}
      onSubmit={() =>
        onCreate({
          evidence: Object.fromEntries(
            Object.entries(form).filter(([, v]) => v.trim()).map(([k, v]) => [k, v.trim()])
          ),
        })
      }
    >
      <p className="rounded-lg bg-slate-50 px-3 py-2 text-xs text-slate-500 dark:bg-slate-800/50 dark:text-slate-400">
        Evidence is <strong>appended</strong>, never overwritten — an earlier submission stays
        readable alongside this one. Submitting does not resolve the case: the card network
        decides won or lost.
        {dispute?.evidence_due_by && (
          <> Deadline <strong>{fmtDateTime(dispute.evidence_due_by)}</strong>.</>
        )}
      </p>
      {DISPUTE_EVIDENCE_FIELDS.map(([key, label, placeholder]) => (
        <div key={key}>
          <Label variant="console">{label}</Label>
          <Textarea variant="console" rows={3} value={form[key]} placeholder={placeholder}
                     onChange={(e) => set(key, e.target.value)} />
        </div>
      ))}
    </ActionModal>
  );
}

export function DisputeResolveModal({ dispute, onCreate, onClose }) {
  const [won, setWon] = useState(null);
  const choice = (value, label, active) => (
    <button
      type="button"
      onClick={() => setWon(value)}
      className={cx(
        "rounded-lg border px-3 py-3 text-sm font-medium transition",
        active
          ? value
            ? "border-emerald-400 bg-emerald-50 text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-300"
            : "border-rose-400 bg-rose-50 text-rose-700 dark:bg-rose-500/10 dark:text-rose-300"
          : "border-slate-200 text-slate-600 hover:border-slate-300 dark:border-slate-700 dark:text-slate-300"
      )}
    >
      {label}
    </button>
  );

  return (
    <ActionModal
      title="Record dispute outcome"
      submitLabel="Record outcome"
      disabled={won === null}
      onClose={onClose}
      onSubmit={() => onCreate({ won })}
    >
      <p className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-300">
        Record only what the card network has actually decided. <strong>Won</strong> returns the
        payment to paid; <strong>lost</strong> reverses it and the money is gone. Neither edits
        the original invoice or the delivered event record.
      </p>
      <div className="grid grid-cols-2 gap-3">
        {choice(true, "Won — funds retained", won === true)}
        {choice(false, "Lost — funds reversed", won === false)}
      </div>
      {dispute && (
        <p className="text-xs text-slate-400">
          {dispute.provider} · {dispute.provider_dispute_ref} · {dispute.reason_code}
        </p>
      )}
    </ActionModal>
  );
}

// ── Change orders ─────────────────────────────────────────────────────────────────────────
// The delta is DERIVED from the line operations server-side; this form never sends an amount.
// The preview below is computed locally purely so the operator sees the impact before
// submitting — if it ever disagreed with the server, the server refuses rather than accepting
// our number.

export function ChangeOrderModal({ catalogLines, orderLines, currency, onCreate, onClose }) {
  const [adds, setAdds] = useState([]);
  const [removeIds, setRemoveIds] = useState([]);
  const [reason, setReason] = useState("");
  const [lineId, setLineId] = useState(catalogLines[0]?.id || "");
  const [qty, setQty] = useState("1");

  const addLine = () => {
    const line = catalogLines.find((l) => l.id === lineId);
    if (!line || Number(qty) <= 0) return;
    setAdds((a) => [
      ...a,
      { catalog_line_id: line.id, quantity: qty, _name: line.name,
        _total: Number(line.unit_price) * Number(qty) },
    ]);
    setQty("1");
  };
  const toggleRemove = (id) =>
    setRemoveIds((r) => (r.includes(id) ? r.filter((x) => x !== id) : [...r, id]));

  const delta =
    adds.reduce((s, a) => s + a._total, 0) -
    orderLines.filter((l) => removeIds.includes(l.id)).reduce((s, l) => s + Number(l.line_total), 0);
  const hasOps = adds.length > 0 || removeIds.length > 0;

  return (
    <ActionModal
      title="Raise change order"
      submitLabel="Create change order"
      disabled={!hasOps || reason.trim().length < 3}
      onClose={onClose}
      onSubmit={() =>
        onCreate({
          changes: {
            add_lines: adds.map(({ catalog_line_id, quantity }) => ({ catalog_line_id, quantity })),
            remove_line_ids: removeIds,
          },
          // Omitted on purpose: the server computes it from the catalog-priced lines. Sending a
          // figure that disagrees is refused rather than silently substituted.
          price_delta: null,
          reason: reason.trim(),
        })
      }
    >
      <p className="rounded-lg bg-slate-50 px-3 py-2 text-xs text-slate-500 dark:bg-slate-800/50 dark:text-slate-400">
        Every added line is priced from this order&apos;s own published catalog version. A change
        that <strong>reduces</strong> the order needs an approved discount or price-override
        exception first, and the tax determination is cleared so it must be re-determined.
      </p>

      <div>
        <Label variant="console">Add a line</Label>
        <div className="flex gap-2">
          <Select variant="console" value={lineId} onChange={(e) => setLineId(e.target.value)}>
            {catalogLines.map((l) => (
              <option key={l.id} value={l.id}>{l.name} — {l.unit_price} {l.currency}</option>
            ))}
          </Select>
          <Input variant="console" type="number" min="1" step="1" value={qty}
                 onChange={(e) => setQty(e.target.value)} className="w-24" />
          <Button variant="secondary" size="sm" type="button" onClick={addLine}>Add</Button>
        </div>
      </div>

      {adds.length > 0 && (
        <ul className="space-y-1 rounded-lg border border-slate-200 p-2 dark:border-slate-700">
          {adds.map((a, i) => (
            <li key={i} className="flex items-center justify-between text-sm">
              <span className="text-slate-600 dark:text-slate-300">+ {a._name} × {a.quantity}</span>
              <span className="flex items-center gap-2">
                <span className="font-medium text-emerald-600">+{money(a._total, currency)}</span>
                <button type="button" className="text-xs text-slate-400 hover:text-rose-500"
                        onClick={() => setAdds((x) => x.filter((_, j) => j !== i))}>remove</button>
              </span>
            </li>
          ))}
        </ul>
      )}

      {orderLines.length > 0 && (
        <div>
          <Label variant="console">Remove existing lines</Label>
          <ul className="space-y-1">
            {orderLines.map((l) => (
              <li key={l.id}>
                <label className="flex items-center justify-between gap-2 text-sm text-slate-600 dark:text-slate-300">
                  <span className="flex items-center gap-2">
                    <input type="checkbox" className="h-4 w-4" checked={removeIds.includes(l.id)}
                           onChange={() => toggleRemove(l.id)} />
                    {l.description || l.service_code}
                  </span>
                  <span className={removeIds.includes(l.id) ? "font-medium text-rose-600" : "text-slate-400"}>
                    {removeIds.includes(l.id) ? "−" : ""}{money(l.line_total, currency)}
                  </span>
                </label>
              </li>
            ))}
          </ul>
        </div>
      )}

      <div>
        <Label variant="console">Reason</Label>
        <Textarea variant="console" rows={2} value={reason} onChange={(e) => setReason(e.target.value)}
                   placeholder="Why is the scope changing?" />
      </div>

      {hasOps && (
        <div className="flex items-center justify-between rounded-lg border border-slate-200 px-3 py-2 dark:border-slate-700">
          <span className="text-sm text-slate-500 dark:text-slate-400">Estimated impact</span>
          <span className={cx("text-sm font-semibold", delta < 0 ? "text-rose-600" : "text-emerald-600")}>
            {delta >= 0 ? "+" : "−"}{money(Math.abs(delta), currency)}
          </span>
        </div>
      )}
    </ActionModal>
  );
}

// ── Reconciliation: manual settlement matching (doc P5) ───────────────────────────────────

export function SettlementMatchModal({ settlement, payments, onCreate, onClose }) {
  const [paymentId, setPaymentId] = useState("");
  const [notes, setNotes] = useState("");
  const [confirmed, setConfirmed] = useState(false);

  return (
    <ActionModal
      title="Match settlement to a payment"
      submitLabel="Match settlement"
      disabled={!paymentId || !confirmed}
      onClose={onClose}
      onSubmit={() => onCreate({ payment_id: paymentId, notes: notes.trim() || null })}
    >
      <p className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-300">
        Attribution is never automatic. Pick the payment this money belongs to deliberately —
        the server refuses a match whose amount or currency disagrees, and matching does not
        itself move the payment&apos;s state.
      </p>
      <dl className="grid grid-cols-2 gap-2 text-sm">
        <dt className="text-slate-400">Provider</dt>
        <dd className="text-slate-700 dark:text-slate-200">{settlement.provider}</dd>
        <dt className="text-slate-400">Reference</dt>
        <dd className="truncate text-slate-700 dark:text-slate-200">{settlement.provider_payment_ref || "—"}</dd>
        <dt className="text-slate-400">Amount</dt>
        <dd className="text-slate-700 dark:text-slate-200">
          {settlement.amount != null ? money(settlement.amount, settlement.currency) : "—"}
        </dd>
        <dt className="text-slate-400">Received</dt>
        <dd className="text-slate-700 dark:text-slate-200">{fmtDateTime(settlement.received_at)}</dd>
      </dl>
      <p className="text-xs text-slate-400">{settlement.reason}</p>

      <div>
        <Label variant="console">Match to payment</Label>
        <Select variant="console" value={paymentId} onChange={(e) => setPaymentId(e.target.value)}>
          <option value="">Select a payment…</option>
          {payments.map((p) => (
            <option key={p.id} value={p.id}>
              {money(p.amount, p.currency)} · {p.state} · {p.provider_payment_ref || p.id.slice(0, 8)}
            </option>
          ))}
        </Select>
        {payments.length === 0 && (
          <p className="mt-1 text-xs text-amber-600">
            No candidate payments loaded — open the order this money belongs to first.
          </p>
        )}
      </div>
      <div>
        <Label variant="console">Resolution notes</Label>
        <Textarea variant="console" rows={2} value={notes} onChange={(e) => setNotes(e.target.value)} />
      </div>
      <label className="flex items-start gap-2 text-sm text-slate-600 dark:text-slate-300">
        <input type="checkbox" className="mt-0.5 h-4 w-4" checked={confirmed}
               onChange={(e) => setConfirmed(e.target.checked)} />
        I have verified this settlement belongs to the selected payment.
      </label>
    </ActionModal>
  );
}

// ── Governance: exception decisions and period close ───────────────────────────────────────

export function ExceptionDecisionModal({ exception, decision, onCreate, onClose }) {
  const [notes, setNotes] = useState("");
  const approving = decision === "approve";
  return (
    <ActionModal
      title={approving ? "Approve exception" : "Decline exception"}
      submitLabel={approving ? "Approve" : "Decline"}
      // A decline must say why; an approval note is encouraged but the rationale already lives
      // on the request itself.
      disabled={!approving && notes.trim().length < 3}
      onClose={onClose}
      onSubmit={() => onCreate({ notes: notes.trim() || null })}
    >
      <p className="rounded-lg bg-slate-50 px-3 py-2 text-xs text-slate-500 dark:bg-slate-800/50 dark:text-slate-400">
        Maker-checker: the server refuses this if you are the person who raised it. Approving
        activates a narrowly-scoped override — it is not a blanket permission, and it stops
        clearing anything once its expiry passes.
      </p>
      <dl className="grid grid-cols-2 gap-2 text-sm">
        <dt className="text-slate-400">Type</dt>
        <dd className="text-slate-700 dark:text-slate-200">{exception.exception_type}</dd>
        <dt className="text-slate-400">Gate</dt>
        <dd className="text-slate-700 dark:text-slate-200">{exception.overridden_gate || "—"}</dd>
        <dt className="text-slate-400">Exposure</dt>
        <dd className="text-slate-700 dark:text-slate-200">{exception.amount_exposure ?? "—"}</dd>
      </dl>
      <p className="rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-600 dark:border-slate-700 dark:text-slate-300">
        {exception.rationale}
      </p>
      <div>
        <Label variant="console">
          {approving ? "Approval notes" : "Reason for declining"}
          {!approving && <span className="text-rose-500"> (required)</span>}
        </Label>
        <Textarea variant="console" rows={3} value={notes} onChange={(e) => setNotes(e.target.value)} />
      </div>
    </ActionModal>
  );
}

export function PeriodModal({ onCreate, onClose }) {
  const [form, setForm] = useState({ label: "", period_start: "", period_end: "" });
  const set = (k, v) => setForm((f) => ({ ...f, [k]: v }));
  const valid = form.label.trim() && form.period_start && form.period_end
    && new Date(form.period_end) > new Date(form.period_start);
  return (
    <ActionModal
      title="Open financial period"
      submitLabel="Open period"
      disabled={!valid}
      onClose={onClose}
      onSubmit={() =>
        onCreate({
          label: form.label.trim(),
          period_start: new Date(form.period_start).toISOString(),
          period_end: new Date(form.period_end).toISOString(),
        })
      }
    >
      <div>
        <Label variant="console">Label</Label>
        <Input variant="console" value={form.label} onChange={(e) => set("label", e.target.value)}
               placeholder="2026-08" maxLength={20} />
      </div>
      <div className="grid grid-cols-2 gap-3">
        <div>
          <Label variant="console">Period start</Label>
          <Input variant="console" type="date" value={form.period_start}
                 onChange={(e) => set("period_start", e.target.value)} />
        </div>
        <div>
          <Label variant="console">Period end</Label>
          <Input variant="console" type="date" value={form.period_end}
                 onChange={(e) => set("period_end", e.target.value)} />
        </div>
      </div>
    </ActionModal>
  );
}

export function PeriodCloseModal({ period, blockers, onCreate, onClose }) {
  const [confirmed, setConfirmed] = useState(false);
  const total = blockers.reduce((s, b) => s + b.count, 0);
  return (
    <ActionModal
      title={`Close period ${period.label}`}
      submitLabel="Close period"
      disabled={!confirmed}
      onClose={onClose}
      onSubmit={onCreate}
    >
      <p className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-300">
        Closing freezes this period&apos;s snapshot and files an exception for every open
        finding. There is <strong>no reopen</strong> — a correction after close belongs to a
        later period.
      </p>
      {total === 0 ? (
        <p className="text-sm text-emerald-600 dark:text-emerald-400">
          No open reconciliation findings. This period closes clean.
        </p>
      ) : (
        <div>
          <p className="text-sm text-slate-600 dark:text-slate-300">
            {total} finding{total === 1 ? "" : "s"} will be filed as exceptions on this period:
          </p>
          <ul className="mt-2 space-y-1">
            {blockers.filter((b) => b.count > 0).map((b) => (
              <li key={b.label} className="flex items-center justify-between text-sm">
                <span className="text-slate-600 dark:text-slate-300">{b.label}</span>
                <span className="font-medium text-amber-600">{b.count}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
      <label className="flex items-start gap-2 text-sm text-slate-600 dark:text-slate-300">
        <input type="checkbox" className="mt-0.5 h-4 w-4" checked={confirmed}
               onChange={(e) => setConfirmed(e.target.checked)} />
        I understand this period cannot be reopened.
      </label>
    </ActionModal>
  );
}
