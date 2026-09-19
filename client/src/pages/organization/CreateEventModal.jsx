import { useState } from "react";
import { FiGlobe, FiLock, FiEyeOff } from "react-icons/fi";
import { cx } from "../../ui/tokens";
import Modal from "../../ui/Modal";
import { ConsoleButton as Button } from "../../ui/Button";
import TimeField from "../../ui/TimeField";
import { Input, Textarea, Select, Label, Switch } from "../../ui/forms";
import TimezonePicker from "../../components/organization/TimezonePicker";
import { notify } from "../../ui/Toast";
import api, { errMsg } from "../../api";
import MemberPicker from "./MemberPicker";
import { useAuth } from "../../auth/AuthContext";
import { EVENT_CONSOLES } from "../../auth/destination";

// Category is free-form on the server (`EventCreate.category` is `str | None`, max_length=100
// — no enum, no DB constraint), so this list is purely the UI's menu and can grow without a
// migration. It classifies the event; it does not configure it.

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

const FEATURES = [
  { key: "chat_enabled", label: "Enable Chat" },
  { key: "polls_enabled", label: "Enable Polls" },
  { key: "qa_enabled", label: "Enable Q&A" },
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

// The host console's route, from the one place that defines it.
const HOST_CONSOLE = EVENT_CONSOLES.find((c) => c.capability === "can_host").path;

/**
 * Claim a tab NOW, to be pointed somewhere once the server has answered.
 *
 * Chrome only honours window.open while the click that triggered it still holds transient
 * activation. The event create and the host assignment below are both awaited, and by the
 * time they resolve that activation is normally gone — so opening the console *after* them
 * is silently blocked, intermittently, depending on how fast the network was. Claiming the
 * tab inside the click and navigating it later is the only approach that behaves the same
 * way every time.
 *
 * Deliberately NO "noopener" in the feature string: that flag makes window.open return null
 * by design — withholding the handle is precisely what it does — which is incompatible with
 * holding the reference this strategy depends on. The same protection is applied on the next
 * line by severing `opener` on the new window directly, which is the property noopener would
 * have suppressed. The destination is this same app on this same origin in any case.
 *
 * Returns null when the popup was blocked anyway, so the caller can say so rather than
 * promising a tab that never appeared.
 */
function claimTab() {
  try {
    const tab = window.open("", "_blank");
    if (tab) tab.opener = null;
    return tab || null;
  } catch {
    return null;  // popups blocked outright, or window access denied
  }
}

export default function CreateEventModal({ open, onClose, onCreated }) {
  // The signed-in account id from GET /auth/me, which AuthContext is the only writer of.
  // Compared by ID, never by email or name.
  const { user } = useAuth();
  const userId = user?.id;
  const [form, setForm] = useState(EMPTY);
  const [hostIds, setHostIds] = useState(() => new Set());
  const [saving, setSaving] = useState(null); // "draft" | "scheduled" | null
  const set = (key, value) => setForm((f) => ({ ...f, [key]: value }));
  // Category classifies the event and nothing more. It used to clear chat/polls/Q&A and force
  // visibility to private for Funeral / Memorial, mirroring a server-side clamp; both are
  // retired, so changing the category must leave every other choice exactly as the organiser
  // left it.
  const setCategory = (value) => set("category", value);
  const toggleHost = (id) => {
    setHostIds((s) => {
      const next = new Set(s);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

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
    // Decided BEFORE any await, from the same state the click saw: scheduled (not a draft)
    // and the signed-in account explicitly on the host list. Nothing else opens a tab.
    const selfHosting = status === "scheduled" && !!userId && hostIds.has(userId);
    // Claimed synchronously, while the click still carries activation. Closed again below
    // if anything fails, so a blocked or abandoned attempt never leaves a blank tab behind.
    const consoleTab = selfHosting ? claimTab() : null;

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
        // registration_required / registration_limit / expected_audience are deliberately
        // ABSENT rather than sent as hardcoded falsy values. schemas/event.EventCreate
        // declares `registration_required: bool = False` and the other two as optional
        // (`int | None = Field(None, ge=0)`), so omitting them lets the server's own
        // defaults apply — which keeps this a UI change and leaves the API contract alone.
        // All three remain settable through PATCH /events/{id} (EventUpdate accepts them).
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
      // Both awaits are behind us, so the event exists and the creator is really on it.
      // The id comes from the CREATED EVENT in the response, never from local state.
      //
      // This opens a tab; it grants nothing. /host/dashboard resolves access on arrival
      // through the backend (services/moderation.resolve_ctx), so a URL aimed at somebody
      // else's event is refused there exactly as it is today.
      let opened = false;
      if (selfHosting) {
        const hostUrl =
          `${window.location.origin}${HOST_CONSOLE}?event=${encodeURIComponent(data.id)}`;
        if (consoleTab) {
          // replace(), not assignment: the blank entry never becomes a back-button target
          // in the new tab.
          consoleTab.location.replace(hostUrl);
          opened = true;
        } else {
          // The pre-claim was blocked. One direct attempt, which can still succeed when the
          // requests came back fast enough to remain inside the click's activation window.
          // `noopener,noreferrer` is free here because no handle is needed.
          opened = !!window.open(hostUrl, "_blank", "noopener,noreferrer");
        }
      }

      notify.success(
        status === "draft"
          ? "Draft saved"
          : selfHosting && opened
            ? `"${data.title}" scheduled. Producer Console opened in a new tab.`
            : selfHosting
              // Never promise a tab that is not there.
              ? `"${data.title}" scheduled. Allow pop-ups to open the Producer Console.`
              : `"${data.title}" scheduled`
      );
      // The Organization tab stays where it is: list refreshed, modal closed, no navigation.
      onCreated?.();
      close();
    } catch (e) {
      // Nothing was created, or the host assignment did not stick — so there is nothing to
      // produce. Close the tab we claimed rather than stranding a blank one.
      consoleTab?.close();
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
              <Label htmlFor="event-timezone">Timezone</Label>
              {/* Searchable, because the list is ~65 zones across eight regions and the plain
                  <select> meant scrolling a list taller than the modal to reach one. The VALUE
                  is unchanged — still the IANA identifier this form already submitted. */}
              <TimezonePicker
                id="event-timezone"
                value={form.timezone}
                onChange={(zone) => set("timezone", zone)}
              />
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
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
            {VISIBILITY.map(({ value, label: l, desc, icon: Icon }) => {
              const active = form.visibility === value;
              return (
                <button
                  key={value}
                  type="button"
                  onClick={() => set("visibility", value)}
                  className={cx(
                    "flex flex-col items-start gap-1 rounded-lg border p-3 text-left transition",
                    active
                      ? "border-violet-500 bg-violet-50/60 ring-1 ring-violet-500/30 dark:bg-violet-500/10"
                      : "border-slate-200 hover:border-slate-300 dark:border-slate-700 dark:hover:border-slate-600",
                  )}
                >
                  <Icon className={cx("text-lg", active ? "text-violet-600 dark:text-violet-400" : "text-slate-400")} />
                  <span className="text-sm font-medium text-slate-800 dark:text-slate-100">{l}</span>
                  <span className="text-xs text-slate-500 dark:text-slate-400">{desc}</span>
                </button>
              );
            })}
          </div>
        </Section>

        <Section title="Features">
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            {FEATURES.map(({ key, label: l }) => (
              <Switch
                key={key}
                accent="violet"
                checked={form[key]}
                onChange={(v) => set(key, v)}
                label={l}
              />
            ))}
          </div>
        </Section>

        <Section title="Hosts">
          <MemberPicker selected={hostIds} onToggle={toggleHost} />
        </Section>
      </div>
    </Modal>
  );
}
