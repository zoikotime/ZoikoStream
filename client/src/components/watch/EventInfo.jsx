// client/src/components/watch/EventInfo.jsx
// Left column of the attendee watch page, in three exports that stack around the player:
// the event identity block, the action row below it, and the security row below that.
// Three exports, one file — they share the date/security helpers and none is big enough
// to earn a module of its own.
//
// Every value here is server-supplied. This component contains no copy about the event,
// only labels, and it renders nothing for a field the backend didn't send.
import { useState } from "react";
import {
  FiCalendar, FiMapPin, FiPlay, FiShare2, FiLink, FiBell, FiCheck,
  FiShield, FiCheckCircle, FiGlobe, FiType,
} from "react-icons/fi";
import { cx } from "../../ui/tokens";
import { downloadIcs } from "../../utils/export";
import { notify } from "../../ui/Toast";

// Dates render in the EVENT's timezone, not the attendee's. Someone deciding whether to
// join at 9am needs the organizer's 9am — showing them their own local time silently
// reschedules the event in their head.
function inZone(iso, timeZone, opts) {
  if (!iso) return null;
  try {
    return new Intl.DateTimeFormat(undefined, { ...opts, timeZone: timeZone || undefined }).format(new Date(iso));
  } catch {
    // An IANA zone the browser doesn't recognize must not blank the whole line.
    return new Intl.DateTimeFormat(undefined, opts).format(new Date(iso));
  }
}

const formatDate = (iso, tz) => inZone(iso, tz, { month: "short", day: "numeric", year: "numeric" });
const formatTime = (iso, tz) => inZone(iso, tz, { hour: "numeric", minute: "2-digit", timeZoneName: "short" });

const actionBtn =
  "inline-flex items-center justify-center gap-2 rounded-xl px-4 py-3 text-sm font-semibold transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500 focus-visible:ring-offset-2 focus-visible:ring-offset-white dark:focus-visible:ring-offset-black disabled:cursor-not-allowed disabled:opacity-50";
const quietBtn =
  "border border-slate-200 bg-white text-slate-700 hover:bg-slate-50 dark:border-white/10 dark:bg-white/[0.03] dark:text-neutral-200 dark:hover:bg-white/[0.07]";

// ── identity ──────────────────────────────────────────────────────────────────

export default function EventInfo({ event, security }) {
  const date = formatDate(event.start_time, event.timezone);
  const time = formatTime(event.start_time, event.timezone);

  // The badge states the platform's real verdict and the event's real visibility — it is
  // not a decorative "verified" chip.
  const verified = !!security?.verified_event;

  return (
    <div className="space-y-5">
      <span
        className={cx(
          "inline-flex items-center gap-2 rounded-full border px-3 py-1 text-xs font-medium",
          verified
            ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:border-emerald-400/25 dark:text-emerald-400"
            : "border-slate-300 bg-slate-100 text-slate-600 dark:border-white/15 dark:bg-white/5 dark:text-neutral-300"
        )}
      >
        <span className={cx("h-1.5 w-1.5 rounded-full", verified ? "bg-emerald-500" : "bg-slate-400")} />
        {verified ? "Verified event" : "Event"} · {event.visibility} link
      </span>

      <div className="space-y-3">
        <h1 className="text-3xl font-bold tracking-tight text-slate-900 sm:text-[38px] sm:leading-[1.15] dark:text-white">
          {event.title || "Untitled event"}
        </h1>
        {(event.short_description || event.description) && (
          <p className="max-w-2xl text-[15px] leading-relaxed text-slate-600 dark:text-neutral-400">
            {event.short_description || event.description}
          </p>
        )}
      </div>

      <div className="flex flex-wrap items-center gap-x-5 gap-y-2 text-sm">
        {date && (
          <span className="inline-flex items-center gap-2 text-slate-600 dark:text-neutral-400">
            <FiCalendar className="text-violet-500 dark:text-violet-400" aria-hidden />
            <span className="font-semibold text-slate-900 dark:text-white">{date}</span>
            {time && <span>· {time}</span>}
          </span>
        )}
        {event.location && (
          <span className="inline-flex items-center gap-2 text-slate-600 dark:text-neutral-400">
            <FiMapPin className="text-violet-500 dark:text-violet-400" aria-hidden />
            {event.location}
          </span>
        )}
        {event.category && (
          <span className="rounded-md bg-slate-100 px-2 py-0.5 text-xs font-medium text-slate-600 dark:bg-white/[0.06] dark:text-neutral-300">
            {event.category}
          </span>
        )}
      </div>

      {/* Only shown when the organizer actually enabled one of them. */}
      {(event.captions_enabled || event.translation_enabled) && (
        <p className="inline-flex items-center gap-2 text-sm text-slate-500 dark:text-neutral-400">
          <FiType className="text-violet-500 dark:text-violet-400" aria-hidden />
          {[event.captions_enabled && "Captions", event.translation_enabled && "live translation"]
            .filter(Boolean)
            .join(" & ")}{" "}
          available
        </p>
      )}

      {event.tags?.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {event.tags.map((tag) => (
            <span
              key={tag}
              className="rounded-md border border-slate-200 px-2 py-0.5 text-xs text-slate-500 dark:border-white/10 dark:text-neutral-400"
            >
              #{tag}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

// ── actions ───────────────────────────────────────────────────────────────────

export function EventActions({ event, organizer, isLive, onWatch }) {
  const [copied, setCopied] = useState(false);
  const shareUrl = window.location.href;

  const addToCalendar = (alarmMinutes) => {
    const ok = downloadIcs(
      {
        id: event.id,
        title: event.title,
        description: event.short_description || event.description,
        location: event.location || organizer?.name,
        url: shareUrl,
        start: event.start_time,
        end: event.end_time,
        alarmMinutes,
      },
      `${event.slug || "event"}.ics`
    );
    if (!ok) notify.error("This event has no scheduled start time yet.");
  };

  const copyLink = async () => {
    try {
      await navigator.clipboard.writeText(shareUrl);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // Clipboard is permission-gated and blocked outright on insecure origins.
      notify.error("Your browser blocked clipboard access.");
    }
  };

  const share = async () => {
    // Native share sheet where the platform has one (mobile, Safari); clipboard elsewhere.
    if (navigator.share) {
      try {
        await navigator.share({ title: event.title, text: event.short_description || "", url: shareUrl });
      } catch {
        // Dismissing the sheet throws AbortError — that's a choice, not a failure.
      }
      return;
    }
    copyLink();
  };

  return (
    <div className="space-y-3">
      <div className="flex flex-col gap-3 sm:flex-row">
        {isLive ? (
          <button onClick={onWatch} className={cx(actionBtn, "flex-1 bg-violet-600 text-white hover:bg-violet-500")}>
            <FiPlay /> Watch live
          </button>
        ) : (
          // Pre-live there is nothing to watch, so the primary action becomes the reminder.
          // The .ics carries a VALARM, so it is a real reminder the attendee's own calendar
          // fires — not a button pointing at a notification service this platform lacks.
          <button
            onClick={() => addToCalendar(15)}
            className={cx(actionBtn, "flex-1 bg-violet-600 text-white hover:bg-violet-500")}
          >
            <FiBell /> Remind me
          </button>
        )}

        <button onClick={() => addToCalendar(null)} className={cx(actionBtn, quietBtn)}>
          <FiCalendar /> Add to calendar
        </button>

        <div className="flex gap-3">
          <button onClick={share} aria-label="Share event" className={cx(actionBtn, quietBtn, "w-12 px-0")}>
            <FiShare2 />
          </button>
          <button
            onClick={copyLink}
            aria-label={copied ? "Link copied" : "Copy event link"}
            className={cx(actionBtn, quietBtn, "w-12 px-0")}
          >
            {copied ? <FiCheck className="text-emerald-500" /> : <FiLink />}
          </button>
        </div>
      </div>

      {/* Registration is stored config the organizer set, and it is NOT enforced: there is
          no registration record on this platform to validate against. Saying so beats a
          Register button wired to an endpoint that doesn't exist. */}
      {event.registration_required && (
        <p className="text-xs text-slate-500 dark:text-neutral-500">
          The organizer marked this event as registration-required.
        </p>
      )}
    </div>
  );
}

// ── security row ──────────────────────────────────────────────────────────────

// Each `on` is a fact the SERVER checked (services/viewer._security_out). None is
// hardcoded true, so a deployment without LiveKit shows an honest warning instead of a
// reassuring lie — which is the only thing that makes the row worth showing at all.
function securityItems(s) {
  return [
    {
      id: "access",
      icon: FiShield,
      on: s.encrypted && s.transport_secure,
      label: "Secure access",
      copy: s.encrypted && s.transport_secure
        ? "Your access is protected and encrypted"
        : "This deployment is not serving encrypted media",
    },
    {
      id: "verified",
      icon: FiCheckCircle,
      on: s.verified_event,
      label: s.verified_event ? "Verified event" : "Unverified organizer",
      copy: s.verified_event
        ? "You're viewing an official ZoikoStream event"
        : "This organizer has not been verified by ZoikoStream",
    },
    {
      id: "delivery",
      icon: FiGlobe,
      on: s.edge_delivery,
      label: "Global delivery",
      copy: s.edge_delivery
        ? `Optimized streaming for your connection${s.region ? ` · ${s.region}` : ""}`
        : "Streaming is not configured on this deployment",
    },
  ];
}

export function SecurityRow({ security }) {
  if (!security) return null;
  return (
    <div className="grid grid-cols-1 gap-x-6 gap-y-4 border-t border-slate-200 pt-5 sm:grid-cols-3 dark:border-white/10">
      {securityItems(security).map(({ id, icon: Icon, label, copy, on }) => (
        <div key={id} className="flex gap-2.5">
          <Icon
            className={cx(
              "mt-0.5 shrink-0 text-base",
              on ? "text-emerald-500 dark:text-emerald-400" : "text-amber-500 dark:text-amber-400"
            )}
            aria-hidden
          />
          <div className="space-y-0.5">
            <p className="text-sm font-semibold text-slate-900 dark:text-white">{label}</p>
            <p className="text-xs leading-relaxed text-slate-500 dark:text-neutral-500">{copy}</p>
          </div>
        </div>
      ))}
    </div>
  );
}
