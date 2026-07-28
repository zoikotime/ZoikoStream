import { useEffect, useState } from "react";
import Modal from "../../ui/Modal";
import { ConsoleButton as Button } from "../../ui/Button";
import { Input, Textarea, Label, Switch } from "../../ui/forms";
import { notify } from "../../ui/Toast";
import api, { errMsg } from "../../api";

const EMPTY = { key: "", name: "", description: "", enabled: false };

// Create a new feature flag. No edit-key mode — flags are toggled/edited via the row
// switch and edit action; this modal is create-only.
export default function FeatureFlagModal({ open, onClose, onSaved }) {
  const [form, setForm] = useState(EMPTY);
  const [saving, setSaving] = useState(false);
  const set = (key, value) => setForm((f) => ({ ...f, [key]: value }));

  useEffect(() => {
    if (open) setForm(EMPTY);
  }, [open]);

  const close = () => !saving && onClose();

  const submit = async (e) => {
    e.preventDefault();
    if (!form.key.trim()) return notify.error("Key is required");
    if (!/^[a-z0-9_.-]+$/.test(form.key.trim())) return notify.error("Key must be lowercase letters, numbers, _ . -");
    if (!form.name.trim()) return notify.error("Name is required");
    setSaving(true);
    try {
      await api.post("/admin/feature-flags", {
        key: form.key.trim(),
        name: form.name.trim(),
        description: form.description.trim() || null,
        enabled: form.enabled,
      });
      notify.success(`${form.name} created`);
      onSaved?.();
      onClose();
    } catch (e2) {
      notify.error(errMsg(e2));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal
      open={open}
      onClose={close}
      title="New Feature Flag"
      className="max-w-md"
      footer={
        <>
          <Button variant="secondary" size="sm" onClick={close} disabled={saving}>Cancel</Button>
          <Button size="sm" onClick={submit} loading={saving}>Create flag</Button>
        </>
      }
    >
      <form onSubmit={submit} className="space-y-4">
        <div>
          <Label>Key</Label>
          <Input variant="console" value={form.key} onChange={(e) => set("key", e.target.value)} placeholder="new_billing_flow" />
        </div>
        <div>
          <Label>Name</Label>
          <Input variant="console" value={form.name} onChange={(e) => set("name", e.target.value)} placeholder="New Billing Flow" />
        </div>
        <div>
          <Label>Description</Label>
          <Textarea variant="console" rows={3} value={form.description} onChange={(e) => set("description", e.target.value)} placeholder="What this flag controls…" />
        </div>
        <Switch
          checked={form.enabled}
          onChange={(v) => set("enabled", v)}
          accent="violet"
          label={<span className="text-sm font-medium text-slate-700 dark:text-slate-200">Enabled at creation</span>}
        />
      </form>
    </Modal>
  );
}
