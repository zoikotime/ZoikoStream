import { useEffect, useState } from "react";
import Modal from "../../ui/Modal";
import { ConsoleButton as Button } from "../../ui/Button";
import { Input, Select, Label, Switch } from "../../ui/forms";
import { notify } from "../../ui/Toast";
import api, { errMsg } from "../../api";
import { ROLES, roleLabel } from "./roleInfo";

// Edit an existing platform user's role/name/active state. The admin API has no user
// creation route (accounts are created through org signup/invitations), so this is
// edit-only — no create mode.
export default function UserModal({ open, onClose, user, onSaved }) {
  const [form, setForm] = useState({ full_name: "", role: "viewer", is_active: true });
  const [saving, setSaving] = useState(false);
  const set = (key, value) => setForm((f) => ({ ...f, [key]: value }));

  useEffect(() => {
    if (!open || !user) return;
    setForm({ full_name: user.full_name || "", role: user.role || "viewer", is_active: !!user.is_active });
  }, [open, user]);

  const close = () => !saving && onClose();

  const submit = async (e) => {
    e.preventDefault();
    if (!user) return;
    if (!form.full_name.trim()) return notify.error("Name is required");
    setSaving(true);
    try {
      await api.patch(`/admin/users/${user.id}`, {
        full_name: form.full_name.trim(),
        role: form.role,
        is_active: form.is_active,
      });
      notify.success(`${form.full_name} updated`);
      onSaved?.();
      onClose();
    } catch (e2) {
      notify.error(errMsg(e2));
    } finally {
      setSaving(false);
    }
  };

  if (!user) return null;

  return (
    <Modal
      open={open}
      onClose={close}
      title="Edit User"
      className="max-w-md"
      footer={
        <>
          <Button variant="secondary" size="sm" onClick={close} disabled={saving}>Cancel</Button>
          <Button size="sm" onClick={submit} loading={saving}>Save changes</Button>
        </>
      }
    >
      <form onSubmit={submit} className="space-y-4">
        <div>
          <Label>Full name</Label>
          <Input variant="console" value={form.full_name} onChange={(e) => set("full_name", e.target.value)} />
        </div>
        <div className="grid grid-cols-2 gap-4">
          <div>
            <Label>Email</Label>
            <Input variant="console" value={user.email} disabled className="opacity-60" />
          </div>
          <div>
            <Label>Organization</Label>
            <Input variant="console" value={user.organization_name || "—"} disabled className="opacity-60" />
          </div>
        </div>
        <div>
          <Label>Role</Label>
          <Select variant="console" value={form.role} onChange={(e) => set("role", e.target.value)}>
            {ROLES.map((r) => <option key={r} value={r}>{roleLabel(r)}</option>)}
          </Select>
        </div>
        <Switch
          checked={form.is_active}
          onChange={(v) => set("is_active", v)}
          accent="violet"
          label={<span className="text-sm font-medium text-slate-700 dark:text-slate-200">Account active</span>}
        />
      </form>
    </Modal>
  );
}
