import { useState } from "react";
import Modal from "../../ui/Modal";
import { ConsoleButton as Button } from "../../ui/Button";
import { Input, Label, Textarea } from "../../ui/forms";
import { notify } from "../../ui/Toast";
import api, { errMsg } from "../../api";

// POST /events/{id}/speakers/{userId}/invite — separate from PATCH .../speakers (which only
// manages the EventAssignment eligibility list). Re-sending an invite here resets whatever
// backstage progress the speaker already made (see crud.upsert_contributor_invite) — worth
// warning about since it's easy to click "Invite" again thinking it's harmless.
const EMPTY = {
  join_window_start: "", join_window_end: "", expires_at: "",
  consent_notice: "", support_contact: "",
};

const toISO = (local) => (local ? new Date(local).toISOString() : null);

export default function ContributorInviteModal({ open, onClose, eventId, speaker, onInvited }) {
  const [form, setForm] = useState(EMPTY);
  const [saving, setSaving] = useState(false);
  const [wasOpen, setWasOpen] = useState(open);
  const set = (k, v) => setForm((f) => ({ ...f, [k]: v }));

  if (open !== wasOpen) {
    setWasOpen(open);
    if (open) setForm(EMPTY);
  }

  const send = async () => {
    setSaving(true);
    try {
      await api.post(`/events/${eventId}/speakers/${speaker.id}/invite`, {
        join_window_start: toISO(form.join_window_start),
        join_window_end: toISO(form.join_window_end),
        expires_at: toISO(form.expires_at),
        consent_notice: form.consent_notice || null,
        support_contact: form.support_contact || null,
      });
      notify.success(`Backstage invite sent to ${speaker.full_name}`);
      onInvited?.();
      onClose();
    } catch (e) {
      notify.error(errMsg(e));
    } finally {
      setSaving(false);
    }
  };

  if (!speaker) return null;

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={`Invite ${speaker.full_name} to the backstage`}
      size="md"
      footer={
        <>
          <Button variant="secondary" size="sm" onClick={onClose} disabled={saving}>
            Cancel
          </Button>
          <Button size="sm" onClick={send} loading={saving}>
            Send invite
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <p className="text-sm text-slate-500 dark:text-slate-400">
          Sends an email with a link to the backstage device-check and consent step. Leaving
          the join window blank means the invite is open until you revoke it.
        </p>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <div>
            <Label>Join window opens</Label>
            <Input variant="console" type="datetime-local" value={form.join_window_start}
              onChange={(e) => set("join_window_start", e.target.value)} />
          </div>
          <div>
            <Label>Join window closes</Label>
            <Input variant="console" type="datetime-local" value={form.join_window_end}
              onChange={(e) => set("join_window_end", e.target.value)} />
          </div>
        </div>
        <div>
          <Label>Invite expires</Label>
          <Input variant="console" type="datetime-local" value={form.expires_at}
            onChange={(e) => set("expires_at", e.target.value)} />
        </div>
        <div>
          <Label>Consent notice (optional)</Label>
          <Textarea variant="console" rows={3} value={form.consent_notice}
            placeholder="Shown before they consent to appear on camera — e.g. recording/retention terms specific to this event."
            onChange={(e) => set("consent_notice", e.target.value)} />
        </div>
        <div>
          <Label>Support contact (optional)</Label>
          <Input variant="console" value={form.support_contact}
            placeholder="Who to reach if something goes wrong, e.g. an email or phone number"
            onChange={(e) => set("support_contact", e.target.value)} />
        </div>
      </div>
    </Modal>
  );
}
