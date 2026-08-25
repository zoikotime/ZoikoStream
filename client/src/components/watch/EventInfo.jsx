// client/src/components/watch/EventInfo.jsx
// Below-the-video content for the Viewer Portal: two stat cards (countdown + total
// viewers) above one tabbed card with About / Speakers / Resources.
//
// What's real vs. filler: the watch payload (schemas/event.py WatchOut) carries title,
// description, host_name, category, end_time and start_time — About, the countdown, and
// the host card are live data. Speaker job titles are still the data/watch.js placeholders
// they always were, and event.resources has no backing API yet (see the redesign plan's
// known-limitation note). Tabs that would render empty are simply not offered.
import { useState } from "react";
import { FiCalendar, FiClock, FiGrid, FiFileText, FiExternalLink, FiUsers } from "react-icons/fi";
import { cx, ACCENT } from "../../ui/tokens";
import Card from "../../ui/Card";
import StatsCard from "../../ui/StatsCard";
import EventCountdown from "./EventCountdown";
import { fmtDate } from "../../data/events";
import { speakerProfiles, initials } from "../../data/watch";

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

export default function EventInfo({ event, endISO, viewers }) {
  const speakers = event.speakers || [];
  const resources = Array.isArray(event.resources) ? event.resources : [];
  const tabs = [
    { key: "about", label: "About" },
    // The host is always real, so Speakers has something honest to show even when the
    // event carries no speaker list.
    { key: "speakers", label: "Speakers", count: speakers.length || 1 },
    // No backing data model exists for this yet (see the redesign plan's known-limitation
    // note) — the tab simply never renders until a future API populates event.resources.
    resources.length > 0 && { key: "resources", label: "Resources", count: resources.length },
  ].filter(Boolean);
  const [tab, setTab] = useState("about");

  return (
    <div className="space-y-4">
      {(endISO || viewers != null) && (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <EventCountdown endISO={endISO} />
          {viewers != null && (
            <StatsCard title="Total viewers" value={viewers} icon={FiUsers} accent="violet" live liveLabel="Watching now" />
          )}
        </div>
      )}

      <Card padding="none" className="overflow-hidden">
        <div className="flex gap-6 overflow-x-auto border-b border-slate-200 px-5 pt-4 dark:border-slate-800">
          {tabs.map((t) => (
            <button
              key={t.key}
              onClick={() => setTab(t.key)}
              aria-current={tab === t.key ? "true" : undefined}
              className={cx(
                TAB_BTN,
                "inline-flex items-center gap-1.5",
                tab === t.key
                  ? "border-violet-500 text-violet-600 dark:text-violet-400"
                  : "border-transparent text-slate-500 hover:border-slate-300 hover:text-slate-800 dark:text-slate-400 dark:hover:border-slate-700 dark:hover:text-slate-100"
              )}
            >
              {t.label}
              {t.count != null && (
                <span className="rounded-full bg-violet-100 px-1.5 text-[10px] font-bold text-violet-600 dark:bg-violet-500/15 dark:text-violet-400">
                  {t.count}
                </span>
              )}
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

          {tab === "resources" && (
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              {resources.map((r) => (
                <a
                  key={r.url || r.name}
                  href={r.url}
                  target="_blank"
                  rel="noreferrer"
                  className="flex items-center gap-3 rounded-xl border border-slate-200 p-3 transition duration-150 hover:border-slate-300 hover:bg-slate-50 motion-reduce:transition-none dark:border-slate-800 dark:hover:border-slate-700 dark:hover:bg-slate-800/50"
                >
                  <span className="grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-violet-100 text-violet-600 dark:bg-violet-500/15 dark:text-violet-400">
                    <FiFileText aria-hidden />
                  </span>
                  <span className="min-w-0 flex-1 truncate text-sm font-medium text-slate-800 dark:text-slate-100">{r.name}</span>
                  <FiExternalLink className="shrink-0 text-slate-400" aria-hidden />
                </a>
              ))}
            </div>
          )}
        </div>
      </Card>
    </div>
  );
}
