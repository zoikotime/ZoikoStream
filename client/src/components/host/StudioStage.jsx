// client/src/components/host/StudioStage.jsx
// Main broadcast monitor: live/preview state, overlays (LIVE, REC, viewers,
// elapsed) and a speaker filmstrip. Owns its own elapsed-time counter.
import { useEffect, useState } from "react";
import { FiMonitor, FiVideoOff, FiUsers, FiMic, FiMicOff } from "react-icons/fi";
import { cx, ACCENT } from "../../ui/tokens";
import { participants, initials } from "../../data/host";

const pad = (n) => String(n).padStart(2, "0");
const fmtElapsed = (s) => `${pad(Math.floor(s / 3600))}:${pad(Math.floor(s / 60) % 60)}:${pad(s % 60)}`;

// Everyone on stage (i.e. not a plain attendee) shows in the filmstrip.
const stagePeople = participants.filter((p) => p.role !== "Attendee");

export default function StudioStage({ live, camera, screenShare, recording, event, videoRef }) {
  const [secs, setSecs] = useState(0);

  // While live, tick the elapsed counter off the start time. setSecs is only
  // called inside timer callbacks (never synchronously in the effect body).
  useEffect(() => {
    if (!live) return;
    const start = Date.now();
    const kick = setTimeout(() => setSecs(0), 0); // reset on a fresh go-live
    const t = setInterval(() => setSecs(Math.floor((Date.now() - start) / 1000)), 1000);
    return () => {
      clearTimeout(kick);
      clearInterval(t);
    };
  }, [live]);

  return (
    <div className="space-y-4">
      {/* Main preview monitor */}
      <div className="relative aspect-video w-full overflow-hidden rounded-2xl bg-gradient-to-br from-slate-900 via-slate-800 to-slate-900 ring-1 ring-slate-200 dark:ring-slate-800">
        <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_50%_35%,rgba(139,92,246,0.18),transparent_60%)]" />

        {/* Top overlays */}
        <div className="absolute inset-x-0 top-0 flex items-start justify-between p-4">
          <span
            className={cx(
              "inline-flex items-center gap-1.5 rounded-md px-2.5 py-1 text-xs font-bold tracking-wide",
              live ? "bg-rose-600 text-white" : "bg-white/10 text-slate-200 backdrop-blur"
            )}
          >
            {live && <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-white" />}
            {live ? "LIVE" : "PREVIEW"}
          </span>
          <div className="flex items-center gap-2">
            {recording && (
              <span className="inline-flex items-center gap-1.5 rounded-md bg-black/40 px-2.5 py-1 text-xs font-semibold text-white backdrop-blur">
                <span className="h-2 w-2 animate-pulse rounded-full bg-rose-500" /> REC {fmtElapsed(secs)}
              </span>
            )}
            <span className="inline-flex items-center gap-1.5 rounded-md bg-black/40 px-2.5 py-1 text-xs font-semibold text-white backdrop-blur">
              <FiUsers /> {event.viewers.toLocaleString()}
            </span>
          </div>
        </div>

        {/* Center content. The <video> element is always mounted so its ref is stable —
            LiveKit can attach a track to it the instant one publishes, regardless of
            whether that happens before or after `live`/`camera` state re-renders. An
            opaque placeholder layers on top until there's actually a feed to show. */}
        <div className="absolute inset-0 grid place-items-center">
          <video ref={videoRef} autoPlay muted playsInline className="absolute inset-0 h-full w-full object-cover" />
          {screenShare ? (
            <div className="absolute inset-0 grid place-items-center bg-slate-900">
              <div className="flex flex-col items-center gap-3 text-slate-300">
                <FiMonitor className="text-5xl text-emerald-400" />
                <p className="text-sm font-medium">You're sharing your screen</p>
              </div>
            </div>
          ) : !(live && camera) && camera ? (
            <div className="absolute inset-0 grid place-items-center bg-slate-900">
              <div className="flex flex-col items-center gap-3">
                <span className="grid h-24 w-24 place-items-center rounded-full bg-gradient-to-br from-emerald-500 to-teal-600 text-3xl font-bold text-white shadow-lg">
                  {initials(stagePeople[0]?.name)}
                </span>
                <p className="text-sm font-medium text-slate-200">
                  {live ? "Connecting camera…" : "Ready — click Go Live to start"}
                </p>
              </div>
            </div>
          ) : !camera ? (
            <div className="absolute inset-0 grid place-items-center bg-slate-900">
              <div className="flex flex-col items-center gap-3 text-slate-400">
                <FiVideoOff className="text-5xl" />
                <p className="text-sm font-medium">Your camera is off</p>
              </div>
            </div>
          ) : null}
        </div>

        {/* Bottom overlay: event name + elapsed */}
        <div className="absolute inset-x-0 bottom-0 flex items-center justify-between p-4">
          <p className="truncate text-sm font-semibold text-white drop-shadow">{event.name}</p>
          {live && (
            <span className="rounded-md bg-black/40 px-2 py-1 text-xs font-medium tabular-nums text-white backdrop-blur">
              {fmtElapsed(secs)}
            </span>
          )}
        </div>
      </div>

      {/* Speaker filmstrip */}
      <div className="grid grid-cols-3 gap-3 sm:grid-cols-5">
        {stagePeople.slice(0, 5).map((p) => (
          <div
            key={p.id}
            className="relative aspect-video overflow-hidden rounded-xl bg-slate-800 ring-1 ring-slate-200 dark:ring-slate-800"
          >
            <div className="absolute inset-0 grid place-items-center">
              <span className={cx("grid h-10 w-10 place-items-center rounded-full text-sm font-semibold", ACCENT[p.accent].chip)}>
                {initials(p.name)}
              </span>
            </div>
            <div className="absolute inset-x-0 bottom-0 flex items-center justify-between gap-1 bg-gradient-to-t from-black/70 to-transparent px-2 py-1">
              <span className="truncate text-[11px] font-medium text-white">{p.name.split(" ")[0]}</span>
              {p.muted ? <FiMicOff className="shrink-0 text-rose-400" /> : <FiMic className="shrink-0 text-emerald-400" />}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
