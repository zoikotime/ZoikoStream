// Lets an org admin invite a new Host/Moderator by email right where they're assigning
// one (Create Event modal, event Team tab) instead of leaving the flow to go invite
// someone from Users first. Sends the same invite Users > Invite Member does -- the
// invitee still has to accept before they show up in the assignment dropdown.
import { useState } from "react";
import { cx } from "../../ui/tokens";
import api, { errMsg } from "../../api";
import { notify } from "../../ui/Toast";

const isEmail = (v) => /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(v);

export default function InviteRoleInline({ role }) {
  const [open, setOpen] = useState(false);
  const [email, setEmail] = useState("");
  const [sending, setSending] = useState(false);

  if (!open) {
    return (
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="mt-1.5 text-xs font-medium text-emerald-600 hover:underline dark:text-emerald-400"
      >
        + Invite a new {role}
      </button>
    );
  }

  const send = async () => {
    if (!isEmail(email)) return;
    setSending(true);
    try {
      await api.post("/organization/invitations", { email: email.trim().toLowerCase(), role });
      notify.success(`Invite sent to ${email.trim()} — assign them here once they accept`);
      setEmail("");
      setOpen(false);
    } catch (err) {
      notify.error(errMsg(err, "Failed to send invite"));
    } finally {
      setSending(false);
    }
  };

  return (
    <div className="mt-1.5 flex gap-1.5">
      <input
        type="email"
        autoFocus
        value={email}
        onChange={(e) => setEmail(e.target.value)}
        onKeyDown={(e) => e.key === "Enter" && (e.preventDefault(), send())}
        placeholder={`new-${role}@company.com`}
        className="min-w-0 flex-1 rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 text-xs text-slate-700 outline-none focus:border-emerald-400 focus:ring-2 focus:ring-emerald-500/20 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200"
      />
      <button
        type="button"
        onClick={send}
        disabled={!isEmail(email) || sending}
        className={cx(
          "shrink-0 rounded-lg px-2.5 py-1.5 text-xs font-semibold text-white transition",
          isEmail(email) && !sending ? "bg-emerald-600 hover:bg-emerald-500" : "bg-emerald-600/50"
        )}
      >
        {sending ? "…" : "Send"}
      </button>
    </div>
  );
}
