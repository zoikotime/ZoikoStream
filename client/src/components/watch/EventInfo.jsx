// client/src/components/watch/EventInfo.jsx
// Below-the-video content for the Viewer Portal, now one card with About / Speakers /
// Schedule tabs instead of three stacked cards.
//
// What's real vs. filler is unchanged from before — the watch payload (schemas/event.py
// WatchOut) carries title, description, host_name, status and start_time and nothing else,
// so About and the host card are live data while the agenda and the speaker job titles are
// still the data/watch.js placeholders they always were. Tabs that would render empty are
// simply not offered.
import { useState } from "react";
import { FiCalendar, FiClock, FiGrid, FiMic } from "react-icons/fi";
import { cx, ACCENT } from "../../ui/tokens";
import Card from "../../ui/Card";
import { fmtDate } from "../../data/events";
import { speakerProfiles, agenda, initials } from "../../data/watch";

const TAB_BTN =
  "relative -mb-px shrink-0 border-b-2 px-1 pb-3 text-sm font-semibold transition duration-150 motion-reduce:transition-none";

function Meta({ icon: Icon, label, value }) {
  return (
    <div className="flex items-center gap-3 rounded-xl border border-slate-200 p-3 dark:border-slate-800">
      <span className="grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-emerald-100 text-emerald-600 dark:bg-emerald-500/15 dark:text-emerald-400">
        <Icon aria-hidden />
      </span>
      <div className="min-w-0">
        <p className="text-[11px] font-semibold uppercase tracking-wider text-slate-400">{label}</p>
        <p className="truncate text-sm font-medium text-slate-800 dark:text-slate-100">{value}</p>
      </div>
    </div>
  );
}

function SpeakerCard({ name, role }) {
  const p = speakerProfiles[name] || { title: role || "Speaker", org: "", accent: "emerald" };
  return (
    <div className="flex items-center gap-3 rounded-xl border border-slate-200 p-3 transition duration-150 hover:border-slate-300 hover:bg-slate-50 motion-reduce:transition-none dark:border-slate-800 dark:hover:border-slate-700 dark:hover:bg-slate-800/50">
      <span className={cx("grid h-12 w-12 shrink-0 place-items-center rounded-full text-sm font-semibold", ACCENT[p.accent].chip)}>
        {initials(name)}
      </span>
      <div className="min-w-0">
        <p className="truncate font-medium text-slate-800 dark:text-slate-100">{name}</p>
        <p className="truncate text-xs text-slate-500 dark:text-slate-400">
          {p.title}{p.org && ` · ${p.org}`}
        </p>
      </div>
    </div>
  );
}

export default function EventInfo({ event }) {
  const speakers = event.speakers || [];
  const tabs = [
    { key: "about", label: "About" },
    // The host is always real, so Speakers has something honest to show even when the
    // event carries no speaker list.
    { key: "speakers", label: "Speakers" },
    agenda.length > 0 && { key: "schedule", label: "Schedule" },
  ].filter(Boolean);
  const [tab, setTab] = useState("about");

  return (
    <Card padding="none" className="overflow-hidden">
      <div className="flex gap-6 overflow-x-auto border-b border-slate-200 px-5 pt-4 dark:border-slate-800">
        {tabs.map((t) => (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            aria-current={tab === t.key ? "true" : undefined}
            className={cx(
              TAB_BTN,
              tab === t.key
                ? "border-emerald-500 text-emerald-600 dark:text-emerald-400"
                : "border-transparent text-slate-500 hover:border-slate-300 hover:text-slate-800 dark:text-slate-400 dark:hover:border-slate-700 dark:hover:text-slate-100"
            )}
          >
            {t.label}
          </button>
        ))}
      </div>

      <div key={tab} className="zk-fade-in p-5 sm:p-6">
        {tab === "about" && (
          <>
            <p className="text-sm leading-relaxed text-slate-600 dark:text-slate-300">
              {event.description || "The host hasn't added a description for this event yet."}
            </p>
            <div className="mt-5 grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {event.date && <Meta icon={FiCalendar} label="Date" value={fmtDate(event.date)} />}
              {event.start && (
                <Meta
                  icon={FiClock}
                  label="Time"
                  value={`${event.start}${event.end ? `–${event.end}` : ""} (${event.timezone})`}
                />
              )}
              {event.category && <Meta icon={FiGrid} label="Category" value={event.category} />}
            </div>
          </>
        )}

        {tab === "speakers" && (
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            {speakers.length > 0
              ? speakers.map((name) => <SpeakerCard key={name} name={name} />)
              : <SpeakerCard name={event.host} role="Host" />}
          </div>
        )}

        {tab === "schedule" && (
          <ol className="relative space-y-5 border-l border-slate-200 pl-6 dark:border-slate-800">
            {agenda.map((a) => (
              <li key={a.time} className="relative">
                <span className="absolute -left-[27px] top-1 grid h-3 w-3 place-items-center rounded-full border-2 border-white bg-emerald-500 dark:border-slate-900" />
                <p className="text-xs font-semibold uppercase tracking-wide text-emerald-600 dark:text-emerald-400">{a.time}</p>
                <p className="text-sm font-medium text-slate-800 dark:text-slate-100">{a.title}</p>
                {a.speaker && (
                  <p className="mt-0.5 inline-flex items-center gap-1.5 text-xs text-slate-500 dark:text-slate-400">
                    <FiMic aria-hidden /> {a.speaker}
                  </p>
                )}
              </li>
            ))}
          </ol>
        )}
      </div>
    </Card>
  );
}
