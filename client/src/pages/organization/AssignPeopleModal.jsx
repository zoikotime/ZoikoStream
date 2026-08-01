import { useEffect, useState } from "react";
import Modal from "../../ui/Modal";
import { ConsoleButton as Button } from "../../ui/Button";
import { notify } from "../../ui/Toast";
import api, { errMsg } from "../../api";
import MemberPicker from "./MemberPicker";

// PATCH /events/{id}/{hosts|moderators|speakers} replaces the WHOLE assignee set in one
// call, so this modal works off a local selection and submits it in full rather than
// diffing adds/removes against the server.
export const ROLE_PATH = { Host: "hosts", Moderator: "moderators", Speaker: "speakers" };

export default function AssignPeopleModal({ open, onClose, eventId, role, assigned, onSaved }) {
  const [selected, setSelected] = useState(() => new Set());
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (open) setSelected(new Set(assigned.map((u) => u.id)));
    // `assigned` is a fresh array every render; only re-seed when the modal actually opens.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const toggle = (id) => {
    setSelected((s) => {
      const next = new Set(s);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const save = async () => {
    setSaving(true);
    try {
      const { data } = await api.patch(`/events/${eventId}/${ROLE_PATH[role]}`, {
        user_ids: [...selected],
      });
      notify.success(`${role}s updated`);
      onSaved(data);
      onClose();
    } catch (e) {
      notify.error(errMsg(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={`Manage ${role}s`}
      size="md"
      footer={
        <>
          <Button variant="secondary" size="sm" onClick={onClose} disabled={saving}>
            Cancel
          </Button>
          <Button size="sm" onClick={save} loading={saving}>
            Save
          </Button>
        </>
      }
    >
      <MemberPicker selected={selected} onToggle={toggle} />
    </Modal>
  );
}
