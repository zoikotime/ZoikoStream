import { useState } from "react";
import {
  FiImage,
  FiUploadCloud,
  FiGlobe,
  FiLock,
  FiKey,
  FiCheck,
} from "react-icons/fi";
import { cx } from "../../ui/tokens";
import Modal from "../../ui/Modal";
import Button from "../../ui/Button";
import { notify } from "../../ui/Toast";

// ponytail: dummy option data — swap for org config / team endpoints later.
const CATEGORIES = ["Webinar", "Conference", "Product Launch", "Workshop", "Q&A Session", "Internal"];
const TIMEZONES = ["UTC", "America/New_York", "America/Los_Angeles", "Europe/London", "Europe/Berlin", "Asia/Kolkata", "Asia/Singapore"];
const TEAM = ["Ava Chen", "Marcus Reed", "Priya Nair", "Leo Fischer", "Sofia Alvarez", "Noah Kim"];

const VISIBILITY = [
  { value: "Public", label: "Public", desc: "Anyone with the link can watch", icon: FiGlobe },
  { value: "Private", label: "Private", desc: "Only invited people can watch", icon: FiLock },
  { value: "Password", label: "Password Protected", desc: "Requires a password to join", icon: FiKey },
];

const FEATURES = [
  { key: "chat", label: "Enable Chat" },
  { key: "polls", label: "Enable Polls" },
  { key: "qa", label: "Enable Q&A" },
  { key: "recording", label: "Enable Recording" },
];

const EMPTY = {
  title: "",
  description: "",
  category: CATEGORIES[0],
  thumbnail: "",
  banner: "",
  date: "",
  start: "",
  end: "",
  timezone: "UTC",
  visibility: "Public",
  password: "",
  registration: false,
  chat: true,
  polls: false,
  qa: true,
  recording: true,
  host: TEAM[0],
  moderator: "",
  speakers: [],
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

// Accessible on/off switch.
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

// File "upload" dropzone — no backend, just records the chosen file name.
function Upload({ icon: Icon, title, value, onFile }) {
  return (
    <label className="flex cursor-pointer flex-col items-center justify-center gap-1.5 rounded-xl border border-dashed border-slate-300 bg-slate-50 px-4 py-6 text-center transition hover:border-emerald-400 hover:bg-emerald-50/40 dark:border-slate-700 dark:bg-slate-800/50 dark:hover:border-emerald-500/50">
      <Icon className="text-xl text-slate-400" />
      <span className="text-sm font-medium text-slate-600 dark:text-slate-300">{value || title}</span>
      <span className="text-xs text-slate-400">PNG or JPG, up to 5MB</span>
      <input
        type="file"
        accept="image/*"
        className="hidden"
        onChange={(e) => onFile(e.target.files?.[0]?.name || "")}
      />
    </label>
  );
}

export default function CreateEventModal({ open, onClose }) {
  const [form, setForm] = useState(EMPTY);
  const set = (key, value) => setForm((f) => ({ ...f, [key]: value }));

  const toggleSpeaker = (name) =>
    set("speakers", form.speakers.includes(name) ? form.speakers.filter((s) => s !== name) : [...form.speakers, name]);

  const canPublish = form.title.trim().length > 0;

  const submit = (mode) => {
    // ponytail: no backend — log the payload and toast. Wire to POST /organization/events later.
    console.log(`${mode} event`, form);
    notify.success(mode === "draft" ? "Draft saved" : `"${form.title}" published`);
    setForm(EMPTY);
    onClose();
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Create Event"
      className="max-w-3xl"
      footer={
        <>
          <Button variant="secondary" size="sm" onClick={onClose}>Cancel</Button>
          <Button variant="secondary" size="sm" onClick={() => submit("draft")}>Save Draft</Button>
          <Button size="sm" onClick={() => submit("publish")} disabled={!canPublish} title={canPublish ? undefined : "Add a title first"}>
            Publish Event
          </Button>
        </>
      }
    >
      <div className="max-h-[65vh] overflow-y-auto pr-1">
        {/* Basic Information */}
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

        {/* Media */}
        <Section title="Media">
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <Upload icon={FiImage} title="Upload thumbnail" value={form.thumbnail} onFile={(n) => set("thumbnail", n)} />
            <Upload icon={FiUploadCloud} title="Upload banner" value={form.banner} onFile={(n) => set("banner", n)} />
          </div>
        </Section>

        {/* Schedule */}
        <Section title="Schedule">
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <div>
              <label className={label}>Date</label>
              <input type="date" className={input} value={form.date} onChange={(e) => set("date", e.target.value)} />
            </div>
            <div>
              <label className={label}>Timezone</label>
              <select className={input} value={form.timezone} onChange={(e) => set("timezone", e.target.value)}>
                {TIMEZONES.map((t) => <option key={t}>{t}</option>)}
              </select>
            </div>
            <div>
              <label className={label}>Start Time</label>
              <input type="time" className={input} value={form.start} onChange={(e) => set("start", e.target.value)} />
            </div>
            <div>
              <label className={label}>End Time</label>
              <input type="time" className={input} value={form.end} onChange={(e) => set("end", e.target.value)} />
            </div>
          </div>
        </Section>

        {/* Visibility */}
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
          {form.visibility === "Password" && (
            <input
              type="text"
              className={cx(input, "mt-3")}
              value={form.password}
              onChange={(e) => set("password", e.target.value)}
              placeholder="Set event password"
            />
          )}
        </Section>

        {/* Registration */}
        <Section title="Registration">
          <Toggle checked={form.registration} onChange={(v) => set("registration", v)} label="Registration Required" />
        </Section>

        {/* Features */}
        <Section title="Features">
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            {FEATURES.map(({ key, label: l }) => (
              <Toggle key={key} checked={form[key]} onChange={(v) => set(key, v)} label={l} />
            ))}
          </div>
        </Section>

        {/* Assignments */}
        <Section title="Assignments">
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <div>
              <label className={label}>Host</label>
              <select className={input} value={form.host} onChange={(e) => set("host", e.target.value)}>
                {TEAM.map((p) => <option key={p}>{p}</option>)}
              </select>
            </div>
            <div>
              <label className={label}>Moderator</label>
              <select className={input} value={form.moderator} onChange={(e) => set("moderator", e.target.value)}>
                <option value="">Select a moderator</option>
                {TEAM.map((p) => <option key={p}>{p}</option>)}
              </select>
            </div>
          </div>
          <div className="mt-4">
            <label className={label}>Speakers</label>
            <div className="flex flex-wrap gap-2">
              {TEAM.map((p) => {
                const on = form.speakers.includes(p);
                return (
                  <button
                    key={p}
                    type="button"
                    onClick={() => toggleSpeaker(p)}
                    className={cx(
                      "inline-flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-sm transition",
                      on
                        ? "border-emerald-500 bg-emerald-500 text-white"
                        : "border-slate-200 text-slate-600 hover:border-slate-300 dark:border-slate-700 dark:text-slate-300"
                    )}
                  >
                    {on && <FiCheck className="text-xs" />} {p}
                  </button>
                );
              })}
            </div>
          </div>
        </Section>
      </div>
    </Modal>
  );
}
