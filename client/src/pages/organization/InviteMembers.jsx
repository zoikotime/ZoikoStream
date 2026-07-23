import { useEffect, useState } from "react";
import {
  FiVideo,
  FiMic,
  FiUser,
  FiEye,
  FiSend,
  FiRefreshCw,
  FiX,
} from "react-icons/fi";
import { cx } from "../../ui/tokens";
import Card from "../../ui/Card";
import Button from "../../ui/Button";
import { notify } from "../../ui/Toast";
import api, { errMsg } from "../../api";

const ROLES = [
  { role: "host", label: "Host", icon: FiVideo, accent: "violet", desc: "Full control of the event" },
  { role: "moderator", label: "Moderator", icon: FiMic, accent: "blue", desc: "Manage chat, polls & Q&A" },
  { role: "speaker", label: "Speaker", icon: FiUser, accent: "emerald", desc: "Present on stage" },
  { role: "viewer", label: "Viewer", icon: FiEye, accent: "amber", desc: "Watch and interact" },
];
const ROLE_LABEL = Object.fromEntries(ROLES.map((r) => [r.role, r.label]));

const ACCENT_ICON = {
  violet: "bg-violet-100 text-violet-600 dark:bg-violet-500/15 dark:text-violet-400",
  blue: "bg-blue-100 text-blue-600 dark:bg-blue-500/15 dark:text-blue-400",
  emerald: "bg-emerald-100 text-emerald-600 dark:bg-emerald-500/15 dark:text-emerald-400",
  amber: "bg-amber-100 text-amber-600 dark:bg-amber-500/15 dark:text-amber-400",
};

const STATUS_PILL = {
  Active: "bg-emerald-100 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-400",
  Disabled: "bg-rose-100 text-rose-700 dark:bg-rose-500/15 dark:text-rose-400",
};

const input =
  "w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-800 shadow-sm outline-none transition placeholder:text-slate-400 focus:border-emerald-400 focus:ring-2 focus:ring-emerald-500/20 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-100";
const isEmail = (v) => /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(v);

function InviteCard({ role, label, icon: Icon, accent, desc, onSend }) {
  const [form, setForm] = useState({ name: "", email: "", role });
  const [sending, setSending] = useState(false);
  const set = (k, v) => setForm((f) => ({ ...f, [k]: v }));
  const valid = form.name.trim() && isEmail(form.email);

  const submit = async () => {
    setSending(true);
    const ok = await onSend({ ...form });
    setSending(false);
    if (ok) setForm({ name: "", email: "", role });
  };

  return (
    <Card className="flex flex-col" padding="md">
      <div className="mb-4 flex items-center gap-3">
        <span className={cx("grid h-10 w-10 shrink-0 place-items-center rounded-xl", ACCENT_ICON[accent])}>
          <Icon className="text-lg" />
        </span>
        <div>
          <h3 className="font-semibold text-slate-900 dark:text-white">Invite {label}</h3>
          <p className="text-xs text-slate-500 dark:text-slate-400">{desc}</p>
        </div>
      </div>

      <div className="flex flex-1 flex-col gap-3">
        <input className={input} placeholder="Full name" value={form.name} onChange={(e) => set("name", e.target.value)} />
        <input className={input} type="email" placeholder="Email address" value={form.email} onChange={(e) => set("email", e.target.value)} />
        <select className={input} value={form.role} onChange={(e) => set("role", e.target.value)}>
          {ROLES.map((r) => <option key={r.role} value={r.role}>{r.label}</option>)}
        </select>
        <Button size="sm" className="mt-auto w-full" disabled={!valid || sending} onClick={submit} title={valid ? undefined : "Enter a name and valid email"}>
          <FiSend className="text-base" /> {sending ? "Sending…" : "Send Invitation"}
        </Button>
      </div>
    </Card>
  );
}

const th = "px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-slate-400 whitespace-nowrap";
const td = "px-4 py-3 text-sm text-slate-600 dark:text-slate-300 whitespace-nowrap";

export default function InviteMembers() {
  const [members, setMembers] = useState([]);
  const [loading, setLoading] = useState(true);

  const load = () => {
    api
      .get("/organization/members")
      .then((res) => setMembers(res.data))
      .catch((err) => notify.error(errMsg(err, "Failed to load members")))
      .finally(() => setLoading(false));
  };

  useEffect(load, []);

  const send = async ({ name, email, role }) => {
    try {
      const res = await api.post("/organization/members", { full_name: name, email, role });
      setMembers((list) => [res.data, ...list]);
      notify.success(`Invitation sent to ${name}`);
      return true;
    } catch (err) {
      notify.error(errMsg(err, "Failed to send invitation"));
      return false;
    }
  };

  const resend = (id) => {
    api
      .post(`/organization/members/${id}/resend`)
      .then((res) => {
        setMembers((list) => list.map((m) => (m.id === id ? res.data : m)));
        notify.success("Credentials resent");
      })
      .catch((err) => notify.error(errMsg(err, "Failed to resend credentials")));
  };

  const remove = (id) => {
    if (!window.confirm("Remove this member? They will no longer be able to sign in.")) return;
    api
      .delete(`/organization/members/${id}`)
      .then(() => {
        setMembers((list) => list.map((m) => (m.id === id ? { ...m, is_active: false } : m)));
        notify.success("Member removed");
      })
      .catch((err) => notify.error(errMsg(err, "Failed to remove member")));
  };

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight text-slate-900 dark:text-white">Invite Members</h1>
        <p className="text-sm text-slate-500 dark:text-slate-400">Invite people to your organization by role</p>
      </div>

      {/* Invite cards */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {ROLES.map((r) => (
          <InviteCard key={r.role} {...r} onSend={send} />
        ))}
      </div>

      {/* Members table */}
      <Card padding="none" className="overflow-hidden">
        <div className="flex items-center justify-between border-b border-slate-100 px-5 py-4 dark:border-slate-800">
          <h2 className="font-semibold text-slate-900 dark:text-white">Members</h2>
          <span className="text-sm text-slate-400">{members.length} total</span>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[640px]">
            <thead className="border-b border-slate-100 dark:border-slate-800">
              <tr>
                <th className={th}>Name</th>
                <th className={th}>Email</th>
                <th className={th}>Role</th>
                <th className={th}>Status</th>
                <th className={`${th} text-right`}>Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
              {members.map((m) => {
                const status = m.is_active ? "Active" : "Disabled";
                return (
                  <tr key={m.id} className="hover:bg-slate-50 dark:hover:bg-slate-800/50">
                    <td className={cx(td, "font-medium text-slate-800 dark:text-slate-100")}>{m.full_name}</td>
                    <td className={td}>{m.email}</td>
                    <td className={td}>{ROLE_LABEL[m.role] || m.role}</td>
                    <td className={td}>
                      <span className={cx("rounded-full px-2.5 py-0.5 text-xs font-semibold", STATUS_PILL[status])}>{status}</span>
                    </td>
                    <td className={cx(td, "text-right")}>
                      <div className="flex justify-end gap-2">
                        <button
                          onClick={() => resend(m.id)}
                          className="inline-flex items-center gap-1 rounded-lg border border-slate-200 px-2.5 py-1 text-xs font-medium text-slate-600 transition hover:bg-slate-50 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800"
                        >
                          <FiRefreshCw /> Resend credentials
                        </button>
                        {m.is_active && (
                          <button
                            onClick={() => remove(m.id)}
                            className="inline-flex items-center gap-1 rounded-lg border border-rose-200 px-2.5 py-1 text-xs font-medium text-rose-600 transition hover:bg-rose-50 dark:border-rose-500/30 dark:text-rose-400 dark:hover:bg-rose-500/10"
                          >
                            <FiX /> Remove
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                );
              })}
              {!loading && members.length === 0 && (
                <tr><td colSpan={5} className="px-4 py-12 text-center text-sm text-slate-400">No members yet.</td></tr>
              )}
              {loading && (
                <tr><td colSpan={5} className="px-4 py-12 text-center text-sm text-slate-400">Loading…</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}
