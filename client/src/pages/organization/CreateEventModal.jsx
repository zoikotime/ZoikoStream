import { useMemo, useState } from "react";
import { FiGlobe, FiLock, FiEyeOff } from "react-icons/fi";
import { cx } from "../../ui/tokens";
import { TIMEZONE_GROUPS, tzLabel } from "../../data/timezones";
import Modal from "../../ui/Modal";
import { ConsoleButton as Button } from "../../ui/Button";
import TimeField from "../../ui/TimeField";
import { Input, Textarea, Select, Label, Switch } from "../../ui/forms";
import { notify } from "../../ui/Toast";
import api, { errMsg } from "../../api";
import MemberPicker from "./MemberPicker";

// Must match crud.event.CATEGORY_MIN_RISK_TIER's key exactly — that's the server's own
// canonical way of identifying a memorial-tier event (it drives the r2 risk floor, dual
// recording, legal hold, and the chat/Q&A/polls restriction below). Free-form on the
// server otherwise (`EventCreate.category` is `str | None`, max_length=100 — no enum, no
// DB constraint), so this list is purely the UI's menu and can grow without a migration.
const MEMORIAL_CATEGORY = "Funeral / Memorial";

const CATEGORIES = [
  "Webinar",
  "Conference",
  "Corporate Event",
  "Product Launch",
  "Workshop / Training",
  "Entertainment",
  "Sports",
  "Wedding / Celebration",
  "Funeral / Memorial",
  "Community / Charity",
  "Other",
];
// Timezone list lives in data/timezones.js — the option VALUE stays an IANA identifier
// (which is what the API stores and what Intl.DateTimeFormat needs) while the label reads
// as the abbreviation people actually schedule by.

// Matches the backend Visibility enum (public | private | unlisted).
const VISIBILITY = [
  { value: "public", label: "Public", desc: "Anyone with the link can watch", icon: FiGlobe },
  { value: "unlisted", label: "Unlisted", desc: "Only people with the link", icon: FiEyeOff },
  { value: "private", label: "Private", desc: "Only invited people can watch", icon: FiLock },
];

// `interaction: true` marks the features the memorial category locks off (BRD Sec.
// 11.3/19, non-waivable) — the server forces these False on write regardless of what's
// submitted (crud.event._enforce_memorial_features), this just keeps the form honest
// about it up front instead of silently reverting the toggle after save.
const FEATURES = [
  { key: "chat_enabled", label: "Enable Chat", interaction: true },
  { key: "polls_enabled", label: "Enable Polls", interaction: true },
  { key: "qa_enabled", label: "Enable Q&A", interaction: true },
  { key: "recording_enabled", label: "Enable Recording" },
];

const EMPTY = {
  title: "",
  description: "",
  category: CATEGORIES[0],
  date: "",
  start: "",
  end: "",
  timezone: "UTC",
  visibility: "public",
  registration_required: false,
  registration_limit: "",
  expected_audience: "",
  chat_enabled: false,
  polls_enabled: false,
  qa_enabled: false,
  recording_enabled: true,
};

function Section({ title, children }) {
  return (
    <section className="border-b border-slate-100 py-5 first:pt-0 last:border-0 dark:border-slate-800">
      <h3 className="mb-4 text-[11px] font-semibold uppercase tracking-wider text-slate-400">{title}</h3>
      {children}
    </section>
  );
}

// Combine a <input type=date> + <input type=time> into an ISO datetime (or null).
const toISO = (date, time) => {
  if (!date) return null;
  const d = new Date(`${date}T${time || "00:00"}`);
  return isNaN(d) ? null : d.toISOString();
};

export default function CreateEventModal({ open, onClose, onCreated }) {
  const [form, setForm] = useState(EMPTY);
  const [hostIds, setHostIds] = useState(() => new Set());
  const [saving, setSaving] = useState(null); // "draft" | "scheduled" | null
  const set = (key, value) => setForm((f) => ({ ...f, [key]: value }));
  const isMemorial = form.category === MEMORIAL_CATEGORY;
  // Switching TO the memorial category clears the toggles the server would force off
  // anyway (crud.event._enforce_memorial_features) — keeps the form honest immediately
  // rather than showing three switches that silently revert on save.
  const setCategory = (value) => {
    setForm((f) => ({
      ...f, category: value,
      // The server forces visibility to "private" for memorial events too
      // (crud.event._enforce_memorial_features / MEMORIAL_VISIBILITY) — never public or
      // unlisted, a "controlled family download" is the whole point of the category. Matches
      // the toggle-reset above so the form never shows a value the save would silently revert.
      ...(value === MEMORIAL_CATEGORY && {
        chat_enabled: false, polls_enabled: false, qa_enabled: false, visibility: "private",
      }),
    }));
  };
  const toggleHost = (id) => {
    setHostIds((s) => {
      const next = new Set(s);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  // Built once per mount: each label calls Intl for the zone's current GMT offset, and this
  // form re-renders on every keystroke — recomputing ~65 offsets that often is pure waste.
  const timezoneOptions = useMemo(
    () =>
      TIMEZONE_GROUPS.map(([group, zones]) => (
        <optgroup key={group} label={group}>
          {zones.map(([zone, abbr, places]) => (
            <option key={zone} value={zone}>
              {tzLabel(zone, abbr, places)}
            </option>
          ))}
        </optgroup>
      )),
    []
  );

  const canSchedule = form.title.trim().length > 0;

  const close = () => {
    setForm(EMPTY);
    setHostIds(new Set());
    onClose();
  };

  const submit = async (status) => {
    const start_time = toISO(form.date, form.start);
    const end_time = toISO(form.date, form.end);
    if (start_time && end_time && new Date(end_time) <= new Date(start_time)) {
      return notify.error("End time must be after start time");
    }
    setSaving(status);
    try {
      const { data } = await api.post("/events", {
        title: form.title.trim(),
        description: form.description || null,
        category: form.category || null,
        timezone: form.timezone || null,
        start_time,
        end_time,
        visibility: form.visibility,
        registration_required: form.registration_required,
        registration_limit: form.registration_required && form.registration_limit !== ""
          ? Number(form.registration_limit) : null,
        expected_audience: form.expected_audience !== "" ? Number(form.expected_audience) : null,
        chat_enabled: form.chat_enabled,
        polls_enabled: form.polls_enabled,
        qa_enabled: form.qa_enabled,
        recording_enabled: form.recording_enabled,
        status,
      });
      // Hosts are assigned right after creation (the event needs an id first) — the
      // backend emails each newly-added host, so this alone covers "invite the host".
      if (hostIds.size) {
        await api.patch(`/events/${data.id}/hosts`, { user_ids: [...hostIds] });
      }
      notify.success(status === "draft" ? "Draft saved" : `"${data.title}" scheduled`);
      onCreated?.();
      close();
    } catch (e) {
      notify.error(errMsg(e));
    } finally {
      setSaving(null);
    }
  };

  return (
    <Modal
      open={open}
      onClose={close}
      title="Create Event"
      className="max-w-2xl"
      footer={
        <>
          <Button variant="secondary" size="sm" onClick={close} disabled={!!saving}>Cancel</Button>
          <Button variant="secondary" size="sm" onClick={() => submit("draft")} loading={saving === "draft"} disabled={!!saving}>
            Save Draft
          </Button>
          <Button
            size="sm"
            onClick={() => submit("scheduled")}
            loading={saving === "scheduled"}
            disabled={!canSchedule || !!saving}
            title={canSchedule ? undefined : "Add a title first"}
          >
            Schedule Event
          </Button>
        </>
      }
    >
      {/* ponytail: media upload has no storage endpoint yet. Speakers are
          assigned from the event page after creation (see AssignPeopleModal) — hosts get
          a picker here too since that's the most common thing to set up-front. */}
      {/* Modal already height-caps its panel and scrolls its body (see ui/Modal), so the
          max-h + overflow that used to be here produced a SECOND scrollbar nested inside the
          first — two tracks, and neither one scrolled the whole form. */}
      <div>
        <Section title="Basic Information">
          <div className="space-y-4">
            <div>
              <Label>Event Title</Label>
              <Input variant="console" value={form.title} onChange={(e) => set("title", e.target.value)} placeholder="e.g. Q3 Product Launch" />
            </div>
            <div>
              <Label>Description</Label>
              <Textarea variant="console" rows={3} value={form.description} onChange={(e) => set("description", e.target.value)} placeholder="What is this event about?" />
            </div>
            <div>
              <Label>Category</Label>
              <Select variant="console" value={form.category} onChange={(e) => setCategory(e.target.value)}>
                {CATEGORIES.map((c) => <option key={c}>{c}</option>)}
              </Select>
            </div>
          </div>
        </Section>

        <Section title="Schedule">
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <div>
              <Label>Date</Label>
              <Input variant="console" type="date" value={form.date} onChange={(e) => set("date", e.target.value)} />
            </div>
            <div>
              <Label>Timezone</Label>
              <Select variant="console" value={form.timezone} onChange={(e) => set("timezone", e.target.value)}>
                {timezoneOptions}
              </Select>
            </div>
            <div>
              <Label>Start Time</Label>
              <TimeField label="start time" value={form.start} onChange={(v) => set("start", v)} />
            </div>
            <div>
              <Label>End Time</Label>
              <TimeField label="end time" value={form.end} onChange={(v) => set("end", v)} />
            </div>
          </div>
        </Section>

        <Section title="Visibility">
          <div className={cx("grid grid-cols-1 gap-3 sm:grid-cols-3", isMemorial && "pointer-events-none")}>
            {VISIBILITY.map(({ value, label: l, desc, icon: Icon }) => {
              // Memorial events are locked to Private server-side — this only reflects that
              // (disabled + visually inert), it never decides it; crud.event enforces it
              // regardless of what this form would have submitted.
              const locked = isMemorial && value !== "private";
              const active = isMemorial ? value === "private" : form.visibility === value;
              return (
                <button
                  key={value}
                  type="button"
                  disabled={locked}
                  onClick={() => set("visibility", value)}
                  className={cx(
                    "flex flex-col items-start gap-1 rounded-lg border p-3 text-left transition",
                    active
                      ? "border-violet-500 bg-violet-50/60 ring-1 ring-violet-500/30 dark:bg-violet-500/10"
                      : "border-slate-200 hover:border-slate-300 dark:border-slate-700 dark:hover:border-slate-600",
                    locked && "opacity-40",
                  )}
                >
                  <Icon className={cx("text-lg", active ? "text-violet-600 dark:text-violet-400" : "text-slate-400")} />
                  <span className="text-sm font-medium text-slate-800 dark:text-slate-100">{l}</span>
                  <span className="text-xs text-slate-500 dark:text-slate-400">{desc}</span>
                </button>
              );
            })}
          </div>
          {isMemorial && (
            <p className="mt-3 text-xs text-slate-500 dark:text-slate-400">
              Funeral / Memorial events are always Private — a controlled, family-only replay,
              never public or unlisted.
            </p>
          )}
        </Section>

        <Section title="Registration">
          <div className="space-y-4">
            <div>
              <Label>Expected Audience (optional)</Label>
              <Input
                variant="console"
                type="number"
                min="0"
                value={form.expected_audience}
                onChange={(e) => set("expected_audience", e.target.value)}
                placeholder="Peak concurrent viewers"
              />
              <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
                Events expected above 500 concurrent viewers need capacity approval before they can arm.
              </p>
            </div>
            <Switch accent="violet" checked={form.registration_required} onChange={(v) => set("registration_required", v)} label="Registration Required" />
            {form.registration_required && (
              <div>
                <Label>Capacity limit (optional)</Label>
                <Input
                  variant="console"
                  type="number"
                  min="1"
                  value={form.registration_limit}
                  onChange={(e) => set("registration_limit", e.target.value)}
                  placeholder="No limit"
                />
              </div>
            )}
          </div>
        </Section>

        <Section title="Features">
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            {FEATURES.map(({ key, label: l, interaction }) => (
              <div key={key} className={cx(interaction && isMemorial && "pointer-events-none opacity-40")}>
                <Switch
                  accent="violet"
                  checked={interaction && isMemorial ? false : form[key]}
                  onChange={(v) => set(key, v)}
                  label={l}
                />
              </div>
            ))}
          </div>
          {isMemorial && (
            <p className="mt-3 text-xs text-slate-500 dark:text-slate-400">
              Chat, Q&amp;A, and polls are unavailable for the Funeral / Memorial category.
            </p>
          )}
        </Section>

        <Section title="Hosts">
          <MemberPicker selected={hostIds} onToggle={toggleHost} />
        </Section>
      </div>
    </Modal>
  );
}
