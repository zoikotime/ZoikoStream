import { useEffect, useState } from "react";
import Modal from "../../ui/Modal";
import { ConsoleButton as Button } from "../../ui/Button";
import { Input, Textarea, Select, Label } from "../../ui/forms";
import { notify } from "../../ui/Toast";
import api, { errMsg } from "../../api";

const EMPTY = { version: "", title: "", notes: "", channel: "production" };

// Publish a new changelog entry. Create-only — releases are an append-only log.
export default function ReleaseModal({ open, onClose, onSaved }) {
  const [form, setForm] = useState(EMPTY);
  const [saving, setSaving] = useState(false);
  const set = (key, value) => setForm((f) => ({ ...f, [key]: value }));

  useEffect(() => {
    if (open) setForm(EMPTY);
  }, [open]);

  const close = () => !saving && onClose();

  const submit = async (e) => {
    e.preventDefault();
    if (!form.version.trim()) return notify.error("Version is required");
    if (!form.title.trim()) return notify.error("Title is required");
    setSaving(true);
    try {
      await api.post("/admin/releases", {
        version: form.version.trim(),
        title: form.title.trim(),
        notes: form.notes.trim() || null,
        channel: form.channel,
      });
      notify.success(`${form.version} published`);
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
      title="Publish Release"
      className="max-w-md"
      footer={
        <>
          <Button variant="secondary" size="sm" onClick={close} disabled={saving}>Cancel</Button>
          <Button size="sm" onClick={submit} loading={saving}>Publish</Button>
        </>
      }
    >
      <form onSubmit={submit} className="space-y-4">
        <div className="grid grid-cols-2 gap-4">
          <div>
            <Label>Version</Label>
            <Input variant="console" value={form.version} onChange={(e) => set("version", e.target.value)} placeholder="v1.4.0" />
          </div>
          <div>
            <Label>Channel</Label>
            <Select variant="console" value={form.channel} onChange={(e) => set("channel", e.target.value)}>
              <option value="production">Production</option>
              <option value="staging">Staging</option>
              <option value="beta">Beta</option>
            </Select>
          </div>
        </div>
        <div>
          <Label>Title</Label>
          <Input variant="console" value={form.title} onChange={(e) => set("title", e.target.value)} placeholder="Faster stream startup" />
        </div>
        <div>
          <Label>Notes</Label>
          <Textarea variant="console" rows={4} value={form.notes} onChange={(e) => set("notes", e.target.value)} placeholder="What changed…" />
        </div>
      </form>
    </Modal>
  );
}
