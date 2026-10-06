import { useState } from "react";
import Modal from "../../ui/Modal";
import { ConsoleButton as Button } from "../../ui/Button";
import { Input, Select, Label } from "../../ui/forms";
import { notify } from "../../ui/Toast";
import api, { errMsg } from "../../api";

const EMPTY = { name: "", domain: "", region: "", status: "active", plan_slug: "" };

// Create/edit an organization. Same modal for both — `org` null means create.
export default function OrgModal({ open, onClose, org, plans = [], onSaved }) {
  // Mounted only while open and keyed by the org being edited (Organizations.jsx), so this
  // initial state IS the per-open reset. `plans` cannot arrive later: it comes from the same
  // fetch as the rows, and opening the dialog requires a row.
  const [form, setForm] = useState(() =>
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
  const [saving, setSaving] = useState(false);
  const set = (key, value) => setForm((f) => ({ ...f, [key]: value }));

  const close = () => !saving && onClose();

  const submit = async (e) => {
    e.preventDefault();
    if (!form.name.trim()) return notify.error("Organization name is required");
    setSaving(true);
    try {
      const payload = {
        name: form.name.trim(),
        region: form.region.trim() || null,
        status: form.status,
        plan_slug: form.plan_slug || null,
      };
      // The custom domain is sent only when it changed: the server then runs the full
      // lifecycle (validation, uniqueness, a new verification token, audit), and an edit to
      // the name alone never touches it.
      const domain = form.domain.trim();
      if (domain !== (org?.domain || "")) payload.domain = domain || null;
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
          <Label>Custom domain</Label>
          <Input variant="console" value={form.domain} onChange={(e) => set("domain", e.target.value)} placeholder="events.acme.com" />
          <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
            The organization&apos;s event-page hostname. Changing it restarts verification: the
            organization must publish new DNS records before it serves anything.
          </p>
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
              /* price_monthly is null until an approved price is published — don't render
                 "$null/mo" or imply the plan is free. */
              <option key={p.slug} value={p.slug}>
                {p.pricing_state === "PUBLISHED"
                  ? `${p.name} — $${p.price_monthly}/mo`
                  : p.pricing_state === "CUSTOM"
                    ? `${p.name} — custom pricing`
                    : `${p.name} — price not published`}
              </option>
            ))}
          </Select>
        </div>
      </form>
    </Modal>
  );
}
