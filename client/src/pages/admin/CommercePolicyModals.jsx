import { useState } from "react";
import Modal from "../../ui/Modal";
import { ConsoleButton as Button } from "../../ui/Button";
import { Input, Textarea, Label, Select } from "../../ui/forms";
import { notify } from "../../ui/Toast";
import api, { errMsg } from "../../api";
import { VERTICALS } from "../../data/commerce";

const RISK_TIERS = ["r0", "r1", "r2", "r3"];

// The R2/R3 evidence requirements straight from doc Section 10/F — checked here becomes
// a mandatory readiness gate an event with this profile must pass (crud.commercial's
// required_readiness_checks / evaluate_readiness).
const REQUIRE_FLAGS = [
  ["requires_backup_contribution", "Backup contribution path"],
  ["requires_dual_recording", "Independent dual recording"],
  ["requires_preview_return", "Preview/return feed"],
  ["requires_command_owner", "Named command owner"],
  ["requires_reserved_capacity", "Reserved capacity (R3)"],
  ["requires_change_freeze", "Change freeze (R3)"],
  ["requires_full_rehearsal", "Full rehearsal (R3)"],
];

export function ServiceProfileModal({ onClose, onSaved }) {
  const [form, setForm] = useState({
    version_label: "", risk_tier: "r2", name: "", description: "",
    assured_event_eligible: false,
    requires_backup_contribution: false, requires_dual_recording: false, requires_preview_return: false,
    requires_command_owner: false, requires_reserved_capacity: false, requires_change_freeze: false,
    requires_full_rehearsal: false,
  });
  const [saving, setSaving] = useState(false);
  const set = (k, v) => setForm((f) => ({ ...f, [k]: v }));

  const submit = async (e) => {
    e.preventDefault();
    if (!form.version_label.trim() || !form.name.trim()) return notify.error("Version label and name are required");
    setSaving(true);
    try {
      await api.post("/commercial/service-profiles", { ...form, version_label: form.version_label.trim(), name: form.name.trim() });
      notify.success("Service profile created as draft");
      onSaved();
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
      title="New service profile"
      size="md"
      footer={
        <>
          <Button variant="secondary" size="sm" onClick={onClose} disabled={saving}>Cancel</Button>
          <Button size="sm" onClick={submit} loading={saving}>Create draft</Button>
        </>
      }
    >
      <form onSubmit={submit} className="space-y-4">
        <div className="grid grid-cols-2 gap-3">
          <div>
            <Label variant="console">Risk tier</Label>
            <Select variant="console" value={form.risk_tier} onChange={(e) => set("risk_tier", e.target.value)}>
              {RISK_TIERS.map((t) => <option key={t} value={t}>{t.toUpperCase()}</option>)}
            </Select>
          </div>
          <div>
            <Label variant="console">Version label</Label>
            <Input variant="console" value={form.version_label} onChange={(e) => set("version_label", e.target.value)} placeholder="v1" />
          </div>
        </div>
        <div>
          <Label variant="console">Name</Label>
          <Input variant="console" value={form.name} onChange={(e) => set("name", e.target.value)} placeholder="Managed R2" />
        </div>
        <div>
          <Label variant="console">Description</Label>
          <Textarea variant="console" rows={2} value={form.description} onChange={(e) => set("description", e.target.value)} />
        </div>
        <div className="space-y-2 rounded-lg border border-slate-200 p-3 dark:border-slate-700">
          <p className="text-xs font-semibold uppercase tracking-wide text-slate-400">Required readiness evidence</p>
          {REQUIRE_FLAGS.map(([key, label]) => (
            <label key={key} className="flex items-center gap-2 text-sm text-slate-600 dark:text-slate-300">
              <input type="checkbox" checked={form[key]} onChange={(e) => set(key, e.target.checked)} className="h-4 w-4" />
              {label}
            </label>
          ))}
          <label className="flex items-center gap-2 border-t border-slate-100 pt-2 text-sm text-slate-600 dark:border-slate-800 dark:text-slate-300">
            <input type="checkbox" checked={form.assured_event_eligible} onChange={(e) => set("assured_event_eligible", e.target.checked)} className="h-4 w-4" />
            Assured Event eligible
          </label>
        </div>
      </form>
    </Modal>
  );
}

export function CancellationPolicyModal({ onClose, onSaved }) {
  const [form, setForm] = useState({
    version_label: "", vertical: VERTICALS[0].value, risk_tier: "",
    lead_time_min_hours: "0", lead_time_max_hours: "", refund_percentage: "", nonrecoverable_cost_percentage: "",
  });
  const [saving, setSaving] = useState(false);
  const set = (k, v) => setForm((f) => ({ ...f, [k]: v }));

  const submit = async (e) => {
    e.preventDefault();
    if (!form.version_label.trim() || form.refund_percentage === "") {
      return notify.error("Version label and refund percentage are required");
    }
    setSaving(true);
    try {
      await api.post("/commercial/cancellation-policies", {
        version_label: form.version_label.trim(), vertical: form.vertical,
        risk_tier: form.risk_tier || null,
        lead_time_min_hours: Number(form.lead_time_min_hours) || 0,
        lead_time_max_hours: form.lead_time_max_hours === "" ? null : Number(form.lead_time_max_hours),
        refund_percentage: form.refund_percentage,
        nonrecoverable_cost_percentage: form.nonrecoverable_cost_percentage || null,
      });
      notify.success("Cancellation policy created as draft");
      onSaved();
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
      title="New cancellation policy"
      size="md"
      footer={
        <>
          <Button variant="secondary" size="sm" onClick={onClose} disabled={saving}>Cancel</Button>
          <Button size="sm" onClick={submit} loading={saving}>Create draft</Button>
        </>
      }
    >
      <form onSubmit={submit} className="space-y-4">
        <div className="grid grid-cols-2 gap-3">
          <div>
            <Label variant="console">Vertical</Label>
            <Select variant="console" value={form.vertical} onChange={(e) => set("vertical", e.target.value)}>
              {VERTICALS.map((v) => <option key={v.value} value={v.value}>{v.label}</option>)}
            </Select>
          </div>
          <div>
            <Label variant="console">Risk tier</Label>
            <Select variant="console" value={form.risk_tier} onChange={(e) => set("risk_tier", e.target.value)}>
              <option value="">All tiers</option>
              {RISK_TIERS.map((t) => <option key={t} value={t}>{t.toUpperCase()}</option>)}
            </Select>
          </div>
        </div>
        <div>
          <Label variant="console">Version label</Label>
          <Input variant="console" value={form.version_label} onChange={(e) => set("version_label", e.target.value)} placeholder="v1" />
        </div>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <Label variant="console">Lead time from (hours)</Label>
            <Input variant="console" type="number" min="0" value={form.lead_time_min_hours} onChange={(e) => set("lead_time_min_hours", e.target.value)} />
          </div>
          <div>
            <Label variant="console">Lead time to (hours, optional)</Label>
            <Input variant="console" type="number" min="0" value={form.lead_time_max_hours} onChange={(e) => set("lead_time_max_hours", e.target.value)} placeholder="No upper bound" />
          </div>
        </div>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <Label variant="console">Refund %</Label>
            <Input variant="console" type="number" min="0" max="100" step="0.01" value={form.refund_percentage} onChange={(e) => set("refund_percentage", e.target.value)} />
          </div>
          <div>
            <Label variant="console">Nonrecoverable cost % (optional)</Label>
            <Input variant="console" type="number" min="0" max="100" step="0.01" value={form.nonrecoverable_cost_percentage} onChange={(e) => set("nonrecoverable_cost_percentage", e.target.value)} />
          </div>
        </div>
        <p className="text-xs text-slate-500 dark:text-slate-400">
          Applies when a cancellation's notice period falls in [from, to) hours before the event start.
        </p>
      </form>
    </Modal>
  );
}
