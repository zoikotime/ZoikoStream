import { useMemo, useState } from "react";
import { FiGlobe, FiLock, FiEyeOff, FiMail, FiX } from "react-icons/fi";
import { cx, focusRing } from "../../ui/tokens";
import Modal from "../../ui/Modal";
import { ConsoleButton as Button } from "../../ui/Button";
import { Input, Textarea, Select, Label, Switch, Note } from "../../ui/forms";
import { notify } from "../../ui/Toast";
import api from "../../api";
import useMutation from "../../hooks/useMutation";
import {
  CATEGORIES, TIMEZONES, STREAM_QUALITY, VISIBILITY_HELP,
} from "../../data/events";

// ONE modal for Create and Edit. Same fields, same validation, same endpoints — an edit form
// that drifts from its create form is how two different events end up possible.
//   create: POST  /events
//   edit:   PATCH /events/{id}   (only changed fields, so a concurrent edit elsewhere is
//                                 not clobbered by fields this form never touched)

const VISIBILITY = [
  { value: "public", label: "Public", icon: FiGlobe },
  { value: "unlisted", label: "Unlisted", icon: FiEyeOff },
  { value: "private", label: "Private", icon: FiLock },
  { value: "invite_only", label: "Invite only", icon: FiMail },
];

const FEATURES = [
  { key: "chat_enabled", label: "Live chat" },
  { key: "qa_enabled", label: "Q&A" },
  { key: "polls_enabled", label: "Polls" },
  { key: "raise_hand_enabled", label: "Raise hand" },
  { key: "allow_screen_share", label: "Screen sharing" },
  { key: "waiting_room_enabled", label: "Waiting room" },
  { key: "recording_enabled", label: "Record this event" },
  { key: "replay_enabled", label: "Publish replay afterwards" },
  { key: "captions_enabled", label: "Live captions" },
  { key: "translation_enabled", label: "Live translation" },
  { key: "auto_start_recording", label: "Start recording automatically" },
  { key: "auto_end_event", label: "End event automatically" },
];

const EMPTY = {
  title: "",
  description: "",
  short_description: "",
  category: "",
  tags: [],
  thumbnail: "",
  banner_image: "",
  location: "",
  language: "",
  date: "",
  start: "",
  end: "",
  timezone: "UTC",
  visibility: "public",
  registration_required: false,
  registration_limit: "",
  max_participants: "",
  access_password: "",
  stream_quality: "1080p",
  chat_enabled: true,
  qa_enabled: true,
  polls_enabled: false,
  raise_hand_enabled: true,
  allow_screen_share: true,
  waiting_room_enabled: false,
  recording_enabled: true,
  replay_enabled: false,
  captions_enabled: false,
  translation_enabled: false,
  auto_start_recording: false,
  auto_end_event: false,
};

function Section({ title, hint, children }) {
  return (
    <section className="border-b border-slate-100 py-5 first:pt-0 last:border-0 dark:border-slate-800">
      <h3 className="text-[11px] font-semibold uppercase tracking-wider text-slate-400">{title}</h3>
      {hint && <p className="mb-4 mt-1 text-xs text-slate-500 dark:text-slate-400">{hint}</p>}
      <div className={hint ? "" : "mt-4"}>{children}</div>
    </section>
  );
}

// ── date/time <-> ISO ────────────────────────────────────────────────────────
// The two <input type="date"> + <input type="time"> controls are native (keyboard- and
// screen-reader-accessible, localized by the browser, no picker dependency).

const toISO = (date, time) => {
  if (!date) return null;
  const d = new Date(`${date}T${time || "00:00"}`);
  return isNaN(d) ? null : d.toISOString();
};

/** ISO -> the { date, time } pair the inputs need, in the viewer's local zone (which is
 *  what the browser controls display, so round-tripping matches what was shown). */
const fromISO = (iso) => {
  if (!iso) return { date: "", time: "" };
  const d = new Date(iso);
  if (isNaN(d)) return { date: "", time: "" };
  const pad = (n) => String(n).padStart(2, "0");
  return {
    date: `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`,
    time: `${pad(d.getHours())}:${pad(d.getMinutes())}`,
  };
};

/** Existing event -> form state. Nulls become "" so every control stays controlled. */
function toForm(event) {
  if (!event) return EMPTY;
  const s = fromISO(event.start_time);
  const e = fromISO(event.end_time);
  return {
    ...EMPTY,
    ...Object.fromEntries(
      Object.keys(EMPTY)
        .filter((k) => !["date", "start", "end", "tags", "access_password"].includes(k))
        .map((k) => [k, event[k] ?? EMPTY[k]])
    ),
    tags: event.tags || [],
    date: s.date,
    start: s.time,
    end: e.time,
    // Never pre-filled: the server stores a bcrypt hash and cannot return the passphrase.
    // Blank means "leave unchanged" (see the Access section's note).
    access_password: "",
  };
}

const numOrNull = (v) => {
  const n = Number(String(v).trim());
  return String(v).trim() === "" || isNaN(n) ? null : n;
};

/** Free-text tag input. Enter or comma commits; Backspace on an empty field removes the
 *  last chip — the interaction people already expect, with no dependency. */
function TagInput({ tags, onChange }) {
  const [draft, setDraft] = useState("");

  const commit = () => {
    const next = draft.split(",").map((t) => t.trim()).filter(Boolean);
    if (!next.length) return;
    onChange([...new Set([...tags, ...next])].slice(0, 20));
    setDraft("");
  };

  return (
    <div>
      <div className="mb-2 flex flex-wrap gap-1.5" role="list" aria-label="Tags">
        {tags.map((t) => (
          <span
            key={t}
            role="listitem"
            className="inline-flex items-center gap-1 rounded-full bg-slate-100 px-2.5 py-1 text-xs font-medium text-slate-600 dark:bg-slate-800 dark:text-slate-300"
          >
            #{t}
            <button
              type="button"
              onClick={() => onChange(tags.filter((x) => x !== t))}
              aria-label={`Remove tag ${t}`}
              className={cx("rounded text-slate-400 hover:text-rose-500", focusRing)}
            >
              <FiX className="text-xs" aria-hidden="true" />
            </button>
          </span>
        ))}
      </div>
      <Input
        variant="console"
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === ",") {
            e.preventDefault();
            commit();
          } else if (e.key === "Backspace" && !draft && tags.length) {
            onChange(tags.slice(0, -1));
          }
        }}
        onBlur={commit}
        placeholder="Add a tag and press Enter"
        aria-label="Add a tag"
      />
    </div>
  );
}

export default function EventFormModal({ open, onClose, onSaved, event = null }) {
  const editing = !!event;
  const [form, setForm] = useState(() => toForm(event));
  const [errors, setErrors] = useState({});
  const set = (key, value) => setForm((f) => ({ ...f, [key]: value }));

  // Re-seed whenever a DIFFERENT event is opened (or the dialog is reopened), so a
  // cancelled edit never leaves the previous event's values behind.
  //
  // Render-phase reset rather than an effect: React applies it before painting, so the
  // form never flashes the previous event's values for one frame. Same pattern as
  // components/admin/DataTable's page reset.
  const [seed, setSeed] = useState({ open, event });
  if (open !== seed.open || event !== seed.event) {
    setSeed({ open, event });
    if (open) {
      setForm(toForm(event));
      setErrors({});
    }
  }

  const save = useMutation({
    onDone: (res) => {
      onSaved?.(res?.data);
      onClose();
    },
  });

  const validate = (status) => {
    const next = {};
    if (status !== "draft" && !form.title.trim()) next.title = "A title is required to publish.";
    if (form.end && !form.start) next.end = "Set a start time first.";
    const startISO = toISO(form.date, form.start);
    const endISO = toISO(form.date, form.end);
    if (startISO && endISO && new Date(endISO) <= new Date(startISO)) {
      next.end = "End time must be after the start time.";
    }
    const limit = numOrNull(form.registration_limit);
    if (limit !== null && limit < 0) next.registration_limit = "Cannot be negative.";
    const cap = numOrNull(form.max_participants);
    if (cap !== null && cap < 1) next.max_participants = "Must be at least 1.";
    if (form.access_password && form.access_password.length < 4) {
      next.access_password = "Use at least 4 characters.";
    }
    setErrors(next);
    return Object.keys(next).length === 0;
  };

  const payload = (status) => {
    const body = {
      title: form.title.trim() || null,
      description: form.description || null,
      short_description: form.short_description || null,
      category: form.category || null,
      tags: form.tags,
      thumbnail: form.thumbnail.trim() || null,
      banner_image: form.banner_image.trim() || null,
      location: form.location.trim() || null,
      language: form.language.trim() || null,
      timezone: form.timezone || null,
      start_time: toISO(form.date, form.start),
      end_time: toISO(form.date, form.end),
      visibility: form.visibility,
      registration_required: form.registration_required,
      registration_limit: numOrNull(form.registration_limit),
      max_participants: numOrNull(form.max_participants),
      stream_quality: form.stream_quality,
      ...Object.fromEntries(FEATURES.map(({ key }) => [key, form[key]])),
    };
    // Only send the passphrase when the admin actually typed one. Omitting the key is what
    // tells the server "leave it alone" (schemas/event.py EventUpdate documents the three
    // states: absent = unchanged, "" = clear, value = set).
    if (form.access_password) body.access_password = form.access_password;
    if (status) body.status = status;
    return body;
  };

  const submit = (status) => {
    if (!validate(status ?? event?.status ?? "draft")) {
      return notify.error("Please fix the highlighted fields");
    }
    const body = payload(status);
    return save.run(
      () => (editing ? api.patch(`/events/${event.id}`, body) : api.post("/events", body)),
      {
        success: editing
          ? "Event updated"
          : status === "draft"
            ? "Draft saved"
            : `"${body.title}" ${status === "scheduled" ? "scheduled" : "published"}`,
      }
    );
  };

  const canPublish = form.title.trim().length > 0;
  const scheduled = !!form.date;

  // On edit, keep the event's current status — publishing is a lifecycle action that lives
  // on the detail page, not something a field edit should trigger as a side effect.
  const footer = useMemo(
    () =>
      editing ? (
        <>
          <Button variant="secondary" size="sm" onClick={onClose} disabled={save.busy}>Cancel</Button>
          <Button size="sm" loading={save.busy} disabled={save.busy} onClick={() => submit(null)}>
            Save changes
          </Button>
        </>
      ) : (
        <>
          <Button variant="secondary" size="sm" onClick={onClose} disabled={save.busy}>Cancel</Button>
          <Button variant="secondary" size="sm" loading={save.busy} disabled={save.busy} onClick={() => submit("draft")}>
            Save draft
          </Button>
          <Button
            size="sm"
            loading={save.busy}
            disabled={!canPublish || save.busy}
            title={canPublish ? undefined : "Add a title first"}
            onClick={() => submit(scheduled ? "scheduled" : "published")}
          >
            {scheduled ? "Schedule event" : "Publish event"}
          </Button>
        </>
      ),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [editing, save.busy, canPublish, scheduled, form]
  );

  return (
    <Modal
      open={open}
      onClose={save.busy ? () => {} : onClose}
      title={editing ? `Edit "${event?.title || "event"}"` : "Create event"}
      size="xl"
      footer={footer}
    >
      {/* Scroll region is the BODY, so the footer actions stay reachable without scrolling —
          which matters most on a phone, where this form is tallest. */}
      <div className="max-h-[65vh] space-y-0 overflow-y-auto pr-1">
        <Section title="Basic information">
          <div className="space-y-4">
            <div>
              <Label variant="console" htmlFor="zk-ev-title">Event title</Label>
              <Input
                id="zk-ev-title"
                variant="console"
                value={form.title}
                error={errors.title}
                onChange={(e) => set("title", e.target.value)}
                placeholder="e.g. Q3 Product Launch"
                maxLength={200}
              />
              <Note error={errors.title} hint={!errors.title && "Required before the event can be published."} />
            </div>
            <div>
              <Label variant="console" htmlFor="zk-ev-short">Short description</Label>
              <Input
                id="zk-ev-short"
                variant="console"
                value={form.short_description}
                onChange={(e) => set("short_description", e.target.value)}
                placeholder="One line, shown on the attendee page"
                maxLength={300}
              />
            </div>
            <div>
              <Label variant="console" htmlFor="zk-ev-desc">Description</Label>
              <Textarea
                id="zk-ev-desc"
                variant="console"
                rows={3}
                value={form.description}
                onChange={(e) => set("description", e.target.value)}
                placeholder="What is this event about?"
              />
            </div>
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              <div>
                <Label variant="console" htmlFor="zk-ev-cat">Category</Label>
                <Select id="zk-ev-cat" variant="console" value={form.category} onChange={(e) => set("category", e.target.value)}>
                  <option value="">No category</option>
                  {CATEGORIES.map((c) => <option key={c} value={c}>{c}</option>)}
                </Select>
              </div>
              <div>
                <Label variant="console" htmlFor="zk-ev-loc">Location</Label>
                <Input
                  id="zk-ev-loc"
                  variant="console"
                  value={form.location}
                  onChange={(e) => set("location", e.target.value)}
                  placeholder="Online, or New York, USA"
                  maxLength={200}
                />
              </div>
            </div>
            <div>
              <Label variant="console">Tags</Label>
              <TagInput tags={form.tags} onChange={(t) => set("tags", t)} />
            </div>
          </div>
        </Section>

        <Section
          title="Media"
          hint="Image URLs. This deployment has no upload endpoint yet, so paste a hosted image address — the attendee page uses the banner as its poster."
        >
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <div>
              <Label variant="console" htmlFor="zk-ev-thumb">Thumbnail URL</Label>
              <Input
                id="zk-ev-thumb"
                variant="console"
                type="url"
                value={form.thumbnail}
                onChange={(e) => set("thumbnail", e.target.value)}
                placeholder="https://…"
                maxLength={500}
              />
            </div>
            <div>
              <Label variant="console" htmlFor="zk-ev-banner">Banner URL</Label>
              <Input
                id="zk-ev-banner"
                variant="console"
                type="url"
                value={form.banner_image}
                onChange={(e) => set("banner_image", e.target.value)}
                placeholder="https://…"
                maxLength={500}
              />
            </div>
          </div>
        </Section>

        <Section title="Schedule" hint="Leave the date blank to keep this event unscheduled.">
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <div>
              <Label variant="console" htmlFor="zk-ev-date">Date</Label>
              <Input id="zk-ev-date" variant="console" type="date" value={form.date} onChange={(e) => set("date", e.target.value)} />
            </div>
            <div>
              <Label variant="console" htmlFor="zk-ev-tz">Time zone</Label>
              <Select id="zk-ev-tz" variant="console" value={form.timezone} onChange={(e) => set("timezone", e.target.value)}>
                {TIMEZONES.map((t) => <option key={t} value={t}>{t}</option>)}
              </Select>
            </div>
            <div>
              <Label variant="console" htmlFor="zk-ev-start">Start time</Label>
              <Input id="zk-ev-start" variant="console" type="time" value={form.start} onChange={(e) => set("start", e.target.value)} />
            </div>
            <div>
              <Label variant="console" htmlFor="zk-ev-end">End time</Label>
              <Input
                id="zk-ev-end"
                variant="console"
                type="time"
                value={form.end}
                error={errors.end}
                onChange={(e) => set("end", e.target.value)}
              />
              <Note error={errors.end} />
            </div>
          </div>
        </Section>

        <Section title="Viewer access">
          <fieldset>
            <legend className="sr-only">Event visibility</legend>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
              {VISIBILITY.map(({ value, label, icon: Icon }) => {
                const active = form.visibility === value;
                return (
                  <button
                    key={value}
                    type="button"
                    role="radio"
                    aria-checked={active}
                    onClick={() => set("visibility", value)}
                    className={cx(
                      "flex flex-col items-start gap-1 rounded-lg border p-3 text-left transition",
                      focusRing,
                      active
                        ? "border-violet-500 bg-violet-50/60 ring-1 ring-violet-500/30 dark:bg-violet-500/10"
                        : "border-slate-200 hover:border-slate-300 dark:border-slate-700 dark:hover:border-slate-600"
                    )}
                  >
                    <Icon
                      className={cx("text-lg", active ? "text-violet-600 dark:text-violet-400" : "text-slate-400")}
                      aria-hidden="true"
                    />
                    <span className="text-sm font-medium text-slate-800 dark:text-slate-100">{label}</span>
                    <span className="text-xs text-slate-500 dark:text-slate-400">{VISIBILITY_HELP[value]}</span>
                  </button>
                );
              })}
            </div>
          </fieldset>

          <div className="mt-4 space-y-4">
            <Switch
              accent="violet"
              checked={form.registration_required}
              onChange={(v) => set("registration_required", v)}
              label="Registration required"
            />
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              <div>
                <Label variant="console" htmlFor="zk-ev-reglimit">Registration limit</Label>
                <Input
                  id="zk-ev-reglimit"
                  variant="console"
                  type="number"
                  min={0}
                  inputMode="numeric"
                  value={form.registration_limit}
                  error={errors.registration_limit}
                  onChange={(e) => set("registration_limit", e.target.value)}
                  placeholder="No limit"
                />
                <Note error={errors.registration_limit} />
              </div>
              <div>
                <Label variant="console" htmlFor="zk-ev-maxp">Max concurrent participants</Label>
                <Input
                  id="zk-ev-maxp"
                  variant="console"
                  type="number"
                  min={1}
                  inputMode="numeric"
                  value={form.max_participants}
                  error={errors.max_participants}
                  onChange={(e) => set("max_participants", e.target.value)}
                  placeholder="No limit"
                />
                <Note error={errors.max_participants} />
              </div>
            </div>
            <div>
              <Label variant="console" htmlFor="zk-ev-pwd">Passphrase</Label>
              <Input
                id="zk-ev-pwd"
                variant="console"
                type="password"
                autoComplete="new-password"
                value={form.access_password}
                error={errors.access_password}
                onChange={(e) => set("access_password", e.target.value)}
                placeholder={
                  editing && event?.password_protected ? "Set — type to replace" : "Optional"
                }
              />
              <Note
                error={errors.access_password}
                hint={
                  !errors.access_password &&
                  (editing && event?.password_protected
                    ? "Leave blank to keep the current passphrase. Clear it from Settings on the event page."
                    : "Attendees must enter this before playback starts. Your own organization is exempt.")
                }
              />
            </div>
          </div>
        </Section>

        <Section title="Broadcast features" hint="What the host and audience can use during the event.">
          <div className="mb-4">
            <Label variant="console" htmlFor="zk-ev-quality">Stream quality</Label>
            <Select
              id="zk-ev-quality"
              variant="console"
              className="sm:max-w-xs"
              value={form.stream_quality}
              onChange={(e) => set("stream_quality", e.target.value)}
            >
              {STREAM_QUALITY.map((q) => <option key={q.value} value={q.value}>{q.label}</option>)}
            </Select>
            <p className="mt-1.5 text-xs text-slate-500 dark:text-slate-400">
              The encoder target. The host console can override it for a single broadcast.
            </p>
          </div>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            {FEATURES.map(({ key, label }) => (
              <Switch key={key} accent="violet" checked={form[key]} onChange={(v) => set(key, v)} label={label} />
            ))}
          </div>
        </Section>
      </div>
    </Modal>
  );
}
