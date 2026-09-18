// client/src/components/watch/WatchHeader.jsx
// Viewer Portal top section: event banner with title, host, live status and a live viewer
// count, plus Share / Add to calendar / Follow. Those three are deliberately backend-free —
// Share uses the Web Share API (clipboard fallback), the calendar button builds an .ics on
// the client from the event's own start time, and Follow is a local preference. No API
// exists for any of them and none is invented here.
import { useState } from "react";
import { FiCalendar, FiClock, FiUsers, FiShare2, FiHeart, FiCheck, FiDownload } from "react-icons/fi";
import { cx } from "../../ui/tokens";
import { fmtDate } from "../../data/events";
import { initials } from "../../data/watch";
import RocketIllustration from "./RocketIllustration";

// Literal gradient per accent — Tailwind JIT can't compile interpolated names. These stay
// dark in both themes on purpose: it's cover art, like every other streaming platform's, and
// the white text on it is legible either way.
const BANNER = {
  violet: "from-violet-700 via-indigo-800 to-slate-950",
  emerald: "from-violet-700 via-fuchsia-800 to-slate-950",
  blue: "from-blue-700 via-sky-800 to-slate-950",
  amber: "from-amber-600 via-orange-700 to-slate-950",
  indigo: "from-indigo-700 via-violet-800 to-slate-950",
  rose: "from-rose-700 via-pink-800 to-slate-950",
};

// One shared skin for the three banner actions, so hover/active/focus behave identically.
const ACTION =
  "inline-flex items-center gap-2 rounded-xl border border-white/20 bg-white/10 px-3.5 py-2 text-sm font-semibold text-white backdrop-blur transition duration-150 hover:border-white/35 hover:bg-white/20 active:scale-[0.97] motion-reduce:transition-none motion-reduce:active:scale-100";

function StatusPill({ status }) {
  if (status === "Live")
    return (
      <span className="inline-flex items-center gap-1.5 rounded-full bg-rose-600 px-3 py-1 text-xs font-bold uppercase tracking-wide text-white shadow-lg shadow-rose-900/40">
        <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-white" /> Live
      </span>
    );
  if (status === "Completed")
    return <span className="rounded-full bg-white/15 px-3 py-1 text-xs font-semibold text-white backdrop-blur">Ended · Replay</span>;
  return <span className="rounded-full bg-white/15 px-3 py-1 text-xs font-semibold text-white backdrop-blur">Starting soon</span>;
}

// Minimal RFC-5545 file, built and downloaded entirely in the browser.
function downloadIcs(event) {
  const start = event.startISO ? new Date(event.startISO) : null;
  if (!start || isNaN(start)) return;
  const stamp = (d) => d.toISOString().replace(/[-:]/g, "").replace(/\.\d{3}/, "");
  const endFromPayload = event.endISO ? new Date(event.endISO) : null;
  const end = endFromPayload && !isNaN(endFromPayload) && endFromPayload > start
    ? endFromPayload
    : new Date(start.getTime() + 60 * 60 * 1000); // no end_time on this event — 1h block
  const body = [
    "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//ZoikoStream//Viewer//EN", "BEGIN:VEVENT",
    `UID:${event.id}@zoikostream`, `DTSTAMP:${stamp(new Date())}`,
    `DTSTART:${stamp(start)}`, `DTEND:${stamp(end)}`,
    `SUMMARY:${String(event.name).replace(/\r?\n/g, " ")}`,
    `URL:${window.location.origin}${window.location.pathname}`,
    "END:VEVENT", "END:VCALENDAR",
  ].join("\r\n");
  const url = URL.createObjectURL(new Blob([body], { type: "text/calendar;charset=utf-8" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = `${String(event.name).replace(/[^\w.-]+/g, "-").toLowerCase() || "event"}.ics`;
  a.click();
  URL.revokeObjectURL(url);
}

export default function WatchHeader({ event, viewers }) {
  const live = event.status === "Live";
  const [copied, setCopied] = useState(false);
  const [following, setFollowing] = useState(() => localStorage.getItem(`zk_follow_${event.id}`) === "1");

  const share = async () => {
    const url = window.location.href;
    if (navigator.share) {
      try {
        await navigator.share({ title: event.name, url });
        return;
      } catch { /* dismissed — fall through to copy */ }
    }
    try {
      await navigator.clipboard.writeText(url);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch { /* clipboard blocked (insecure context) — nothing useful to do */ }
  };

  const toggleFollow = () => {
    setFollowing((f) => {
      localStorage.setItem(`zk_follow_${event.id}`, f ? "0" : "1");
      return !f;
    });
  };

  return (
    <div className={cx("relative overflow-hidden bg-gradient-to-br", BANNER[event.accent] || BANNER.emerald)}>
      {/* Ambient light + a soft brand glow. Both are pointer-events-none decoration. */}
      <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_top_right,rgba(255,255,255,0.18),transparent_55%)]" />
      <div className="pointer-events-none absolute -right-24 -top-24 h-72 w-72 rounded-full bg-fuchsia-500/25 blur-3xl" />
      {/* Decorative only. Absolutely positioned, so it never contributed to the hero's
          height — but at h-40 it visually demanded a tall banner. Shrunk and softened to sit
          beside the compact header instead of defining it. */}
      <RocketIllustration className="pointer-events-none absolute right-6 top-1/2 hidden h-20 w-20 -translate-y-1/2 opacity-60 sm:block lg:right-10" />
      <div className="pointer-events-none absolute inset-x-0 bottom-0 h-px bg-gradient-to-r from-transparent via-white/25 to-transparent" />

      {/* Compact two-row event header rather than a promotional banner. Row 1 carries the
          badges + title on the left and the three actions on the right; row 2 is the
          metadata line. Horizontal padding is unchanged so the header stays aligned with the
          player and panel below. */}
      <div className="relative mx-auto max-w-7xl px-4 py-6 sm:px-6 lg:px-8">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between lg:gap-6">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <StatusPill status={event.status} />
              {event.category && (
                <span className="rounded-full bg-white/15 px-3 py-1 text-xs font-semibold text-white backdrop-blur">{event.category}</span>
              )}
              {viewers != null && (live || event.viewers != null) && (
                <span className="inline-flex items-center gap-1.5 rounded-full bg-black/30 px-3 py-1 text-xs font-semibold text-white backdrop-blur">
                  <FiUsers aria-hidden /> <span className="zk-tnum">{viewers.toLocaleString()}</span> {live ? "watching" : "views"}
                </span>
              )}
            </div>

            <h1 className="mt-2 max-w-3xl text-2xl font-bold leading-[1.15] tracking-tight text-white sm:text-[30px]">{event.name}</h1>
          </div>

          {/* Actions — wrap on tablet, full-width touch targets on mobile. */}
          <div className="flex flex-wrap items-center gap-2 sm:gap-3">
            <button onClick={share} className={ACTION} aria-label="Share this event">
              {copied ? <FiCheck aria-hidden /> : <FiShare2 aria-hidden />}
              {copied ? "Link copied" : "Share"}
            </button>
            {event.startISO && (
              <button onClick={() => downloadIcs(event)} className={ACTION} aria-label="Download a calendar invite">
                <FiDownload aria-hidden /> <span className="hidden sm:inline">Add to calendar</span><span className="sm:hidden">Calendar</span>
              </button>
            )}
            <button
              onClick={toggleFollow}
              aria-pressed={following}
              title={following ? "Stop following this event on this device" : "Follow this event on this device"}
              className={cx(
                ACTION,
                following && "border-transparent bg-white text-slate-900 hover:bg-white/90 hover:border-transparent"
              )}
            >
              <FiHeart aria-hidden className={following ? "fill-current" : undefined} />
              {following ? "Following" : "Follow"}
            </button>
          </div>
        </div>

        {/* Row 2 — the metadata line. Same fields, same sources, one row where it fits and
            wrapping cleanly where it does not. */}
        <div className="mt-3 flex flex-wrap items-center gap-x-5 gap-y-2 text-[13px] text-white/85">
          <span className="inline-flex min-w-0 items-center gap-2">
            Hosted by
            <span className="grid h-7 w-7 shrink-0 place-items-center rounded-full bg-white/20 text-[11px] font-semibold text-white ring-1 ring-white/25">
              {initials(event.host)}
            </span>
            <span className="truncate font-semibold text-white">{event.host}</span>
            <span className="shrink-0 rounded-full bg-white/15 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-white/90 backdrop-blur">Host</span>
          </span>
          {event.date && (
            <span className="inline-flex items-center gap-2"><FiCalendar aria-hidden /> {fmtDate(event.date)}</span>
          )}
          {event.start && (
            <span className="inline-flex items-center gap-2">
              <FiClock aria-hidden /> {event.start}{event.end && `–${event.end}`}
            </span>
          )}
        </div>
      </div>
    </div>
  );
}
