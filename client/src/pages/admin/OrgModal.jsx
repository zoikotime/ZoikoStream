import { useState } from "react";
import Modal from "../../ui/Modal";
import { ConsoleButton as Button } from "../../ui/Button";
import { Input, Select, Label } from "../../ui/forms";
import { notify } from "../../ui/Toast";
import api, { errMsg } from "../../api";

const EMPTY = { name: "", domain: "", region: "", status: "active", plan_slug: "" };

// Create/edit an organization. Same modal for both — `org` null means create.
export default function OrgModal({ open, onClose, org, plans = [], onSaved }) {
  const [form, setForm] = useState(EMPTY);
  const [saving, setSaving] = useState(false);
  const set = (key, value) => setForm((f) => ({ ...f, [key]: value }));

  // Render-phase reset (not an effect) each time the modal opens.
  const [wasOpen, setWasOpen] = useState(false);
  if (open && !wasOpen) {
    setWasOpen(true);
    setForm(
      org
        ? {
            name: org.name || "",
            domain: org.domain || "",
            region: org.region || "",
            status: org.status || "active",
            plan_slug: (plans.find((p) => p.name === org.plan) || {}).slug || "",
          }
        : EMPTY
    );
  } else if (!open && wasOpen) {
    setWasOpen(false);
  }

  const close = () => !saving && onClose();

  const submit = async (e) => {
    e.preventDefault();
    if (!form.name.trim()) return notify.error("Organization name is required");
    setSaving(true);
    try {
      const payload = {
        name: form.name.trim(),
        domain: form.domain.trim() || null,
        region: form.region.trim() || null,
        status: form.status,
        plan_slug: form.plan_slug || null,
      };
      if (org) {
        await api.patch(`/admin/organizations/${org.id}`, payload);
        notify.success(`${form.name} updated`);
      } else {
        await api.post("/admin/organizations", payload);
        notify.success(`${form.name} created`);
      }
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
      title={org ? "Edit Organization" : "Add Organization"}
      className="max-w-md"
      footer={
        <>
          <Button variant="secondary" size="sm" onClick={close} disabled={saving}>Cancel</Button>
          <Button size="sm" onClick={submit} loading={saving}>{org ? "Save changes" : "Create organization"}</Button>
        </>
      }
    >
      <form onSubmit={submit} className="space-y-4">
        <div>
          <Label>Organization name</Label>
          <Input variant="console" value={form.name} onChange={(e) => set("name", e.target.value)} placeholder="Acme Corp" />
        </div>
        <div>
          <Label>Domain</Label>
          <Input variant="console" value={form.domain} onChange={(e) => set("domain", e.target.value)} placeholder="acme.com" />
        </div>
        <div className="grid grid-cols-2 gap-4">
          <div>
            <Label>Region</Label>
            <Input variant="console" value={form.region} onChange={(e) => set("region", e.target.value)} placeholder="US East" />
          </div>
          <div>
            <Label>Status</Label>
            <Select variant="console" value={form.status} onChange={(e) => set("status", e.target.value)}>
              <option value="active">Active</option>
              <option value="suspended">Suspended</option>
            </Select>
          </div>
        </div>
        <div>
          <Label>Plan {org ? "(switches subscription)" : "(starts a trial)"}</Label>
          <Select variant="console" value={form.plan_slug} onChange={(e) => set("plan_slug", e.target.value)}>
            <option value="">{org ? "Keep current plan" : "No plan yet"}</option>
            {plans.map((p) => (
              <option key={p.slug} value={p.slug}>{p.name} — ${p.price_monthly}/mo</option>
            ))}
          </Select>
        </div>
      </form>
    </Modal>
  );
}
