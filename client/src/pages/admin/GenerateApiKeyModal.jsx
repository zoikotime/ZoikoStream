import { useState } from "react";
import { FiCheck, FiCopy } from "react-icons/fi";
import Modal from "../../ui/Modal";
import { ConsoleButton as Button } from "../../ui/Button";
import { Input, Label } from "../../ui/forms";
import { notify } from "../../ui/Toast";
import api, { errMsg } from "../../api";

// Two-phase: form (label) -> reveal (the raw key, shown exactly once — the backend never
// returns it again after this response).
export default function GenerateApiKeyModal({ open, onClose, orgId, onCreated }) {
  const [label, setLabel] = useState("");
  const [saving, setSaving] = useState(false);
  const [created, setCreated] = useState(null);
  const [copied, setCopied] = useState(false);

  // Render-phase reset (not an effect) each time the modal opens.
  const [wasOpen, setWasOpen] = useState(false);
  if (open && !wasOpen) {
    setWasOpen(true);
    setLabel("");
    setCreated(null);
    setCopied(false);
  } else if (!open && wasOpen) {
    setWasOpen(false);
  }

  const close = () => {
    if (saving) return;
    onClose();
    if (created) onCreated?.();
  };

  const submit = async (e) => {
    e.preventDefault();
    if (!label.trim()) return notify.error("Label is required");
    setSaving(true);
    try {
      const { data } = await api.post(`/admin/organizations/${orgId}/api-keys`, { label: label.trim() });
      setCreated(data);
    } catch (e2) {
      notify.error(errMsg(e2));
    } finally {
      setSaving(false);
    }
  };

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(created.key);
      setCopied(true);
      notify.success("Copied to clipboard");
      setTimeout(() => setCopied(false), 1600);
    } catch {
      notify.error("Couldn't copy — select and copy manually");
    }
  };

  return (
    <Modal
      open={open}
      onClose={close}
      title={created ? "Key Generated" : "Generate API Key"}
      className="max-w-md"
      footer={
        created ? (
          <Button size="sm" onClick={close}>Done</Button>
        ) : (
          <>
            <Button variant="secondary" size="sm" onClick={close} disabled={saving}>Cancel</Button>
            <Button size="sm" onClick={submit} loading={saving}>Generate</Button>
          </>
        )
      }
    >
      {created ? (
        <div className="space-y-3">
          <p className="text-sm text-slate-600 dark:text-slate-300">
            Copy this key now — it won't be shown again.
          </p>
          <div className="flex items-center gap-2 rounded-lg border border-slate-200 bg-slate-50 px-3 py-2.5 dark:border-slate-700 dark:bg-slate-800">
            <code className="min-w-0 flex-1 truncate font-mono text-sm text-slate-800 dark:text-slate-100">{created.key}</code>
            <Button variant="secondary" size="sm" iconOnly title="Copy key" leftIcon={copied ? FiCheck : FiCopy} onClick={copy} />
          </div>
        </div>
      ) : (
        <form onSubmit={submit}>
          <Label>Label</Label>
          <Input variant="console" value={label} onChange={(e) => setLabel(e.target.value)} placeholder="Production integration" autoFocus />
        </form>
      )}
    </Modal>
  );
}
