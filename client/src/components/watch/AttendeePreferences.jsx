// client/src/components/watch/AttendeePreferences.jsx
// The attendee's own settings: language, text size, contrast, motion, captions, reminder default
// and which notifications they want.
//
// Persisted to users.preferences via PATCH /attendee/preferences, which whitelists every key
// (services/attendee.clean_preferences) — an option offered here but unknown there is silently
// dropped, which is why the two lists are cross-referenced in data/attendee.js.
//
// The ACCESSIBILITY switches are applied immediately, to <html>, by useAttendeePreferences. That
// placement matters: a class on a page wrapper would miss modals, toasts and the player chrome,
// all of which render through portals or fixed positioning.
//
// Dark mode is deliberately NOT here. It already exists as a first-class control in the header
// (ThemeContext, persisted to localStorage), and a second switch for it would be two sources of
// truth for one setting.
import { FiCheck, FiGlobe, FiLoader, FiType, FiX } from "react-icons/fi";
import { cx, focusRing } from "../../ui/tokens";
import { Select, Switch, Label } from "../../ui/forms";
import SectionCard from "../admin/SectionCard";
import useAttendeePreferences from "../../hooks/useAttendeePreferences";
import { LANGUAGES, NOTIFY_SETTINGS, REMINDER_OFFSETS, TEXT_SIZES } from "../../data/attendee";

export default function AttendeePreferences({ onClose }) {
  const { prefs, saving, error, set, setNotify } = useAttendeePreferences();

  return (
    <SectionCard
      title="Your settings"
      subtitle="Applied everywhere you watch, on every device"
      icon={FiGlobe}
      action={
        <div className="flex items-center gap-2">
          {saving && (
            <span className="inline-flex items-center gap-1 text-xs text-slate-400">
              <FiLoader className="animate-spin motion-reduce:animate-none" aria-hidden="true" /> saving
            </span>
          )}
          {!saving && !error && (
            <span className="inline-flex items-center gap-1 text-xs text-emerald-600 dark:text-emerald-400">
              <FiCheck aria-hidden="true" /> saved
            </span>
          )}
          {onClose && (
            <button
              type="button"
              onClick={onClose}
              aria-label="Close settings"
              className={cx("grid h-7 w-7 place-items-center rounded-lg text-slate-400 hover:text-slate-700 dark:hover:text-white", focusRing)}
            >
              <FiX />
            </button>
          )}
        </div>
      }
    >
      {error && (
        <p role="alert" className="mb-3 rounded-lg bg-rose-50 px-3 py-2 text-xs text-rose-700 dark:bg-rose-500/10 dark:text-rose-300">
          {error}
        </p>
      )}

      <div className="grid gap-5 sm:grid-cols-2">
        <div className="space-y-3">
          <p className="text-[11px] font-semibold uppercase tracking-wide text-slate-400">
            Reading &amp; display
          </p>

          <div>
            <Label variant="console">Language</Label>
            <Select
              variant="console"
              value={prefs.language}
              onChange={(e) => set({ language: e.target.value })}
              aria-label="Language"
            >
              {LANGUAGES.map((l) => <option key={l.value} value={l.value}>{l.label}</option>)}
            </Select>
            {/* Honest: the preference is stored and sent, but the interface itself is not
                translated yet. Claiming otherwise would be a setting that visibly does nothing. */}
            <p className="mt-1 text-[11px] text-slate-400">
              Stored with your account. The interface isn't translated yet — this is used for
              captions and for organizers who localise their events.
            </p>
          </div>

          <div>
            <Label variant="console">
              <FiType className="mr-1 inline" aria-hidden="true" /> Text size
            </Label>
            <Select
              variant="console"
              value={prefs.text_size}
              onChange={(e) => set({ text_size: e.target.value })}
              aria-label="Text size"
            >
              {TEXT_SIZES.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
            </Select>
          </div>

          <Switch
            checked={!!prefs.high_contrast}
            onChange={(v) => set({ high_contrast: v })}
            label="Higher contrast"
            accent="violet"
          />
          <Switch
            checked={!!prefs.reduced_motion}
            onChange={(v) => set({ reduced_motion: v })}
            label="Reduce motion"
            accent="violet"
          />
          <p className="pl-1 text-[11px] text-slate-400">
            Your operating system's reduced-motion setting is already honoured. This is the explicit
            opt-in if yours says otherwise.
          </p>
          <Switch
            checked={!!prefs.captions}
            onChange={(v) => set({ captions: v })}
            label="Turn captions on when available"
            accent="violet"
          />
          <p className="pl-1 text-[11px] text-slate-400">
            Captions depend on the organizer enabling them for an event; this asks for them by
            default where they exist.
          </p>

          <div>
            <Label variant="console">Default reminder</Label>
            <Select
              variant="console"
              value={prefs.reminder_offset_minutes}
              onChange={(e) => set({ reminder_offset_minutes: Number(e.target.value) })}
              aria-label="Default reminder time"
            >
              {REMINDER_OFFSETS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
            </Select>
          </div>
        </div>

        <div className="space-y-2">
          <p className="text-[11px] font-semibold uppercase tracking-wide text-slate-400">
            Tell me when
          </p>
          {NOTIFY_SETTINGS.map((n) => (
            <Switch
              key={n.key}
              checked={prefs.notify?.[n.key] !== false}
              onChange={(v) => setNotify(n.key, v)}
              label={n.label}
              accent="violet"
            />
          ))}
          {/* The delivery boundary, stated once rather than implied per row. */}
          <p className="mt-2 text-[11px] text-slate-400">
            These appear while you have an event page open. This deployment has no push or SMS
            transport and no scheduled sender, so nothing here becomes an email or a phone alert.
          </p>
        </div>
      </div>
    </SectionCard>
  );
}
