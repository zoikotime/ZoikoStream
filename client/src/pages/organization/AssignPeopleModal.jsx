import { useState } from "react";
import Modal from "../../ui/Modal";
import { ConsoleButton as Button } from "../../ui/Button";
import { notify } from "../../ui/Toast";
import api, { errMsg } from "../../api";
import MemberPicker from "./MemberPicker";
import { ROLE_PATH } from "./roleConfig";

// This modal works off a local selection and submits it in full — see roleConfig.js for why
// the endpoint requires that.

export default function AssignPeopleModal({ open, onClose, eventId, role, assigned, onSaved }) {
  // Seeded once, at mount, from the props that are the source of truth.
  //
  // EventDetails mounts this conditionally and keys it by role, so "the modal opened" and
  // "this component mounted" are the same event — which is why the initial selection belongs
  // in useState. The effect this replaces re-seeded on `open`, and since the only call site
  // mounts with open={true}, that bought one guaranteed cascading re-render per open and
  // nothing else.
  const [selected, setSelected] = useState(() => new Set(assigned.map((u) => u.id)));
  const [saving, setSaving] = useState(false);

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
