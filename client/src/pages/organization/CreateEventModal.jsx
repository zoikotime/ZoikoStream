import { useEffect, useState } from "react";
import { FiGlobe, FiLock, FiEyeOff } from "react-icons/fi";
import { cx } from "../../ui/tokens";
import Modal from "../../ui/Modal";
import Button from "../../ui/Button";
import { notify } from "../../ui/Toast";
import api, { errMsg } from "../../api";
import InviteRoleInline from "../../components/organization/InviteRoleInline";

const CATEGORIES = ["Webinar", "Conference", "Product Launch", "Workshop", "Q&A Session", "Internal"];
const TIMEZONES = ["UTC", "America/New_York", "America/Los_Angeles", "Europe/London", "Europe/Berlin", "Asia/Kolkata", "Asia/Singapore"];

const VISIBILITY = [
  { value: "public", label: "Public", desc: "Anyone with the link can watch", icon: FiGlobe },
  { value: "private", label: "Private", desc: "Only invited people can watch", icon: FiLock },
  { value: "unlisted", label: "Unlisted", desc: "Not listed publicly, link still works", icon: FiEyeOff },
];

const EMPTY = {
  title: "",
  description: "",
  category: CATEGORIES[0],
  scheduled_date: "",
  start_time: "",
  end_time: "",
  timezone: "UTC",
  visibility: "public",
  registration_required: false,
  host_id: "",
  moderator_id: "",
};

const input =
  "w-full rounded-xl border border-slate-200 bg-white px-3.5 py-2.5 text-sm text-slate-800 shadow-sm outline-none transition placeholder:text-slate-400 focus:border-emerald-400 focus:ring-2 focus:ring-emerald-500/20 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-100";
const label = "mb-1.5 block text-sm font-medium text-slate-700 dark:text-slate-300";

function Section({ title, children }) {
  return (
    <section className="border-b border-slate-100 py-5 first:pt-0 last:border-0 dark:border-slate-800">
      <h3 className="mb-4 text-sm font-semibold uppercase tracking-wide text-slate-400">{title}</h3>
      {children}
    </section>
  );
}

function Toggle({ checked, onChange, label: text }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      onClick={() => onChange(!checked)}
      className="flex w-full items-center justify-between gap-3 rounded-xl border border-slate-200 px-3.5 py-2.5 text-sm text-slate-700 transition hover:bg-slate-50 dark:border-slate-700 dark:text-slate-200 dark:hover:bg-slate-800"
    >
      {text}
      <span className={cx("relative h-5 w-9 shrink-0 rounded-full transition", checked ? "bg-emerald-500" : "bg-slate-300 dark:bg-slate-600")}>
        <span className={cx("absolute top-0.5 h-4 w-4 rounded-full bg-white shadow transition", checked ? "left-[18px]" : "left-0.5")} />
      </span>
    </button>
  );
}

// Events created here start as "draft" or "scheduled" — going live happens from the
// host studio, not this form.
export default function CreateEventModal({ open, onClose, onCreated }) {
  const [form, setForm] = useState(EMPTY);
  const [members, setMembers] = useState([]);
  const [saving, setSaving] = useState(false);
  const set = (key, value) => setForm((f) => ({ ...f, [key]: value }));

  useEffect(() => {
    if (!open) return;
    api
      .get("/organization/users")
      .then((res) => setMembers(res.data.items))
      .catch(() => setMembers([]));
  }, [open]);

  const hosts = members.filter((m) => m.role === "host" && m.is_active);
  const moderators = members.filter((m) => m.role === "moderator" && m.is_active);
  const canPublish = form.title.trim().length > 0;

  const submit = async (mode) => {
    setSaving(true);
    try {
      const res = await api.post("/streams", {
        title: form.title,
        description: form.description || null,
        category: form.category,
        scheduled_date: form.scheduled_date || null,
        start_time: form.start_time || null,
        end_time: form.end_time || null,
        timezone: form.timezone,
        visibility: form.visibility,
        registration_required: form.registration_required,
        host_id: form.host_id || null,
        moderator_id: form.moderator_id || null,
        status: mode === "draft" ? "draft" : "scheduled",
      });
      notify.success(mode === "draft" ? "Draft saved" : `"${form.title}" published`);
      setForm(EMPTY);
      onCreated?.(res.data);
      onClose();
    } catch (err) {
      notify.error(errMsg(err, "Failed to create event"));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Create Event"
      className="max-w-3xl"
      footer={
        <>
          <Button variant="secondary" size="sm" onClick={onClose} disabled={saving}>Cancel</Button>
          <Button variant="secondary" size="sm" onClick={() => submit("draft")} disabled={!canPublish || saving}>Save Draft</Button>
          <Button size="sm" onClick={() => submit("publish")} disabled={!canPublish || saving} title={canPublish ? undefined : "Add a title first"}>
            Publish Event
          </Button>
        </>
      }
    >
      <div className="max-h-[65vh] overflow-y-auto pr-1">
        <Section title="Basic Information">
          <div className="space-y-4">
            <div>
              <label className={label}>Event Title</label>
              <input className={input} value={form.title} onChange={(e) => set("title", e.target.value)} placeholder="e.g. Q3 Product Launch" />
            </div>
            <div>
              <label className={label}>Description</label>
              <textarea rows={3} className={input} value={form.description} onChange={(e) => set("description", e.target.value)} placeholder="What is this event about?" />
            </div>
            <div>
              <label className={label}>Category</label>
              <select className={input} value={form.category} onChange={(e) => set("category", e.target.value)}>
                {CATEGORIES.map((c) => <option key={c}>{c}</option>)}
              </select>
            </div>
          </div>
        </Section>

        <Section title="Schedule">
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <div>
              <label className={label}>Date</label>
              <input type="date" className={input} value={form.scheduled_date} onChange={(e) => set("scheduled_date", e.target.value)} />
            </div>
            <div>
              <label className={label}>Timezone</label>
              <select className={input} value={form.timezone} onChange={(e) => set("timezone", e.target.value)}>
                {TIMEZONES.map((t) => <option key={t}>{t}</option>)}
              </select>
            </div>
            <div>
              <label className={label}>Start Time</label>
              <input type="time" className={input} value={form.start_time} onChange={(e) => set("start_time", e.target.value)} />
            </div>
            <div>
              <label className={label}>End Time</label>
              <input type="time" className={input} value={form.end_time} onChange={(e) => set("end_time", e.target.value)} />
            </div>
          </div>
        </Section>

        <Section title="Visibility">
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
            {VISIBILITY.map(({ value, label: l, desc, icon: Icon }) => (
              <button
                key={value}
                type="button"
                onClick={() => set("visibility", value)}
                className={cx(
                  "flex flex-col items-start gap-1 rounded-xl border p-3.5 text-left transition",
                  form.visibility === value
                    ? "border-emerald-500 bg-emerald-50/60 ring-1 ring-emerald-500/30 dark:bg-emerald-500/10"
                    : "border-slate-200 hover:border-slate-300 dark:border-slate-700 dark:hover:border-slate-600"
                )}
              >
                <Icon className={cx("text-lg", form.visibility === value ? "text-emerald-600 dark:text-emerald-400" : "text-slate-400")} />
                <span className="text-sm font-medium text-slate-800 dark:text-slate-100">{l}</span>
                <span className="text-xs text-slate-500 dark:text-slate-400">{desc}</span>
              </button>
            ))}
          </div>
        </Section>

        <Section title="Registration">
          <Toggle checked={form.registration_required} onChange={(v) => set("registration_required", v)} label="Registration Required" />
        </Section>

        <Section title="Assignments">
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <div>
              <label className={label}>Host</label>
              <select className={input} value={form.host_id} onChange={(e) => set("host_id", e.target.value)}>
                <option value="">Unassigned</option>
                {hosts.map((h) => <option key={h.id} value={h.id}>{h.full_name}</option>)}
              </select>
              <InviteRoleInline role="host" />
              {hosts.length === 0 && (
                <p className="mt-1 text-xs text-slate-400">No hosts yet.</p>
              )}
            </div>
            <div>
              <label className={label}>Moderator</label>
              <select className={input} value={form.moderator_id} onChange={(e) => set("moderator_id", e.target.value)}>
                <option value="">Unassigned</option>
                {moderators.map((m) => <option key={m.id} value={m.id}>{m.full_name}</option>)}
              </select>
              <InviteRoleInline role="moderator" />
              {moderators.length === 0 && (
                <p className="mt-1 text-xs text-slate-400">No moderators yet.</p>
              )}
            </div>
          </div>
        </Section>
      </div>
    </Modal>
  );
}
