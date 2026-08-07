import { useState } from "react";
import { FiMail, FiPlus, FiTrash2, FiUser } from "react-icons/fi";
import Modal from "../../ui/Modal";
import { ConsoleButton as Button } from "../../ui/Button";
import { notify } from "../../ui/Toast";
import api, { errMsg } from "../../api";

const field =
  "w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-800 outline-none focus:border-emerald-400 focus:ring-2 focus:ring-emerald-500/20 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-100";
const isEmail = (v) => /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(v);
const empty = () => ({ name: "", email: "" });

// POST /events/{id}/invite-viewers — for a private event this IS the access grant (the
// visibility gate in routers/events.py accepts a valid registration token from any
// invited row); for public/unlisted it's a courtesy email of the watch link.
export default function InviteViewersModal({ open, onClose, eventId, eventVisibility, onInvited }) {
  const [rows, setRows] = useState([empty()]);
  const [sending, setSending] = useState(false);

  const setRow = (i, patch) => setRows((r) => r.map((row, idx) => (idx === i ? { ...row, ...patch } : row)));
  const addRow = () => setRows((r) => [...r, empty()]);
  const removeRow = (i) => setRows((r) => (r.length > 1 ? r.filter((_, idx) => idx !== i) : r));

  const valid = rows.some((r) => r.name.trim() && isEmail(r.email));

  const close = () => {
    setRows([empty()]);
    onClose();
  };

  const send = async () => {
    const invites = rows
      .filter((r) => r.name.trim() && isEmail(r.email))
      .map((r) => ({ name: r.name.trim(), email: r.email.trim().toLowerCase() }));
    if (!invites.length) return;
    setSending(true);
    try {
      const { data } = await api.post(`/events/${eventId}/invite-viewers`, { invites });
      notify.success(`Invited ${data.length} viewer${data.length === 1 ? "" : "s"}`);
      onInvited?.(data);
      close();
    } catch (e) {
      notify.error(errMsg(e));
    } finally {
      setSending(false);
    }
  };

  return (
    <Modal open={open} onClose={close} title="Invite viewers" size="lg">
      <div className="space-y-4 p-5">
        {eventVisibility === "private" && (
          <p className="rounded-lg bg-violet-50 px-3 py-2 text-xs text-violet-700 dark:bg-violet-500/10 dark:text-violet-300">
            This event is private — an invite is the only way to let someone outside your organization watch it.
          </p>
        )}
        <div className="space-y-2">
          {rows.map((row, i) => (
            <div key={i} className="flex items-center gap-2">
              <div className="relative flex-1">
                <FiUser className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-400" />
                <input
                  className={`${field} pl-8`}
                  placeholder="Name"
                  value={row.name}
                  onChange={(e) => setRow(i, { name: e.target.value })}
                />
              </div>
              <div className="relative flex-1">
                <FiMail className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-400" />
                <input
                  type="email"
                  className={`${field} pl-8`}
                  placeholder="email@example.com"
                  value={row.email}
                  onChange={(e) => setRow(i, { email: e.target.value })}
                />
              </div>
              <button
                type="button"
                aria-label="Remove row"
                onClick={() => removeRow(i)}
                disabled={rows.length === 1}
                className="shrink-0 rounded-lg p-2 text-slate-400 transition hover:bg-rose-50 hover:text-rose-600 disabled:opacity-30 dark:hover:bg-rose-500/10 dark:hover:text-rose-400"
              >
                <FiTrash2 />
              </button>
            </div>
          ))}
        </div>
        <button
          type="button"
          onClick={addRow}
          className="inline-flex items-center gap-1.5 text-sm font-medium text-emerald-600 hover:text-emerald-500 dark:text-emerald-400"
        >
          <FiPlus /> Add another
        </button>
      </div>
      <div className="flex justify-end gap-2 border-t border-slate-100 px-5 py-4 dark:border-slate-800">
        <Button variant="secondary" size="sm" onClick={close}>Cancel</Button>
        <Button size="sm" loading={sending} disabled={!valid} onClick={send}>Send invites</Button>
      </div>
    </Modal>
  );
}
