import { useState } from "react";
import Modal from "../../ui/Modal";
import { ConsoleButton as Button } from "../../ui/Button";
import { Input, Textarea, Select, Label } from "../../ui/forms";
import { notify } from "../../ui/Toast";
import api, { errMsg } from "../../api";

const EMPTY = { org_id: "", subject: "", message: "", priority: "normal", requester_email: "" };

// Log a new support ticket against an organization. Create-only — status/priority are
// edited inline from the queue.
export default function SupportTicketModal({ open, onClose, organizations = [], onSaved }) {
  // Mounted only while open (the call site does `{modalOpen && ...}`), so this initial state
  // IS the per-open reset. Defaulting org_id here rather than in an effect is safe because the
  // "Log Ticket" button is disabled until `organizations` has loaded (Support.jsx) — the list
  // can never arrive after this mounts.
  const [form, setForm] = useState(() => ({ ...EMPTY, org_id: organizations[0]?.id || "" }));
  const [saving, setSaving] = useState(false);
  const set = (key, value) => setForm((f) => ({ ...f, [key]: value }));

  const close = () => !saving && onClose();

  const submit = async (e) => {
    e.preventDefault();
    if (!form.org_id) return notify.error("Choose an organization");
    if (!form.subject.trim()) return notify.error("Subject is required");
    if (!form.message.trim()) return notify.error("Message is required");
    setSaving(true);
    try {
      await api.post("/admin/support-tickets", {
        org_id: form.org_id,
        subject: form.subject.trim(),
        message: form.message.trim(),
        priority: form.priority,
        requester_email: form.requester_email.trim() || null,
      });
      notify.success("Ticket logged");
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
      title="Log Support Ticket"
      className="max-w-md"
      footer={
        <>
          <Button variant="secondary" size="sm" onClick={close} disabled={saving}>Cancel</Button>
          <Button size="sm" onClick={submit} loading={saving}>Log ticket</Button>
        </>
      }
    >
      <form onSubmit={submit} className="space-y-4">
        <div className="grid grid-cols-2 gap-4">
          <div>
            <Label>Organization</Label>
            <Select variant="console" value={form.org_id} onChange={(e) => set("org_id", e.target.value)}>
              {organizations.map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}
            </Select>
          </div>
          <div>
            <Label>Priority</Label>
            <Select variant="console" value={form.priority} onChange={(e) => set("priority", e.target.value)}>
              <option value="low">Low</option>
              <option value="normal">Normal</option>
              <option value="high">High</option>
              <option value="urgent">Urgent</option>
            </Select>
          </div>
        </div>
        <div>
          <Label>Subject</Label>
          <Input variant="console" value={form.subject} onChange={(e) => set("subject", e.target.value)} placeholder="Can't start a stream" />
        </div>
        <div>
          <Label>Message</Label>
          <Textarea variant="console" rows={4} value={form.message} onChange={(e) => set("message", e.target.value)} placeholder="Describe the issue…" />
        </div>
        <div>
          <Label>Requester email (optional)</Label>
          <Input variant="console" type="email" value={form.requester_email} onChange={(e) => set("requester_email", e.target.value)} placeholder="user@org.com" />
        </div>
      </form>
    </Modal>
  );
}
