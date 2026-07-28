// client/src/components/watch/EventInfo.jsx
// Below-the-video content for the Viewer Portal: description, speakers, agenda.
import { cx, ACCENT } from "../../ui/tokens";
import Card from "../../ui/Card";
import { speakerProfiles, agenda, initials } from "../../data/watch";

export default function EventInfo({ event }) {
  return (
    <div className="space-y-6">
      {/* Description */}
      <Card>
        <h2 className="mb-2 font-semibold text-slate-900 dark:text-white">About this event</h2>
        <p className="text-sm leading-relaxed text-slate-600 dark:text-slate-300">{event.description}</p>
        <div className="mt-4 flex flex-wrap gap-2 text-xs">
          <span className="rounded-full bg-slate-100 px-2.5 py-1 font-medium text-slate-600 dark:bg-slate-800 dark:text-slate-300">{event.category}</span>
          <span className="rounded-full bg-slate-100 px-2.5 py-1 font-medium text-slate-600 dark:bg-slate-800 dark:text-slate-300">
            {event.timezone.split("/").pop().replace("_", " ")}
          </span>
        </div>
      </Card>

      {/* Speakers */}
      {event.speakers.length > 0 && (
        <Card>
          <h2 className="mb-4 font-semibold text-slate-900 dark:text-white">Speakers</h2>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            {event.speakers.map((name) => {
              const p = speakerProfiles[name] || { title: "Speaker", org: "", accent: "emerald" };
              return (
                <div key={name} className="flex items-center gap-3">
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
            })}
          </div>
        </Card>
      )}

      {/* Agenda */}
      <Card>
        <h2 className="mb-4 font-semibold text-slate-900 dark:text-white">Agenda</h2>
        <ol className="relative space-y-5 border-l border-slate-200 pl-6 dark:border-slate-800">
          {agenda.map((a) => (
            <li key={a.time} className="relative">
              <span className="absolute -left-[27px] top-1 h-3 w-3 rounded-full border-2 border-white bg-emerald-500 dark:border-slate-900" />
              <p className="text-xs font-semibold uppercase tracking-wide text-emerald-600 dark:text-emerald-400">{a.time}</p>
              <p className="text-sm font-medium text-slate-800 dark:text-slate-100">{a.title}</p>
              {a.speaker && <p className="text-xs text-slate-500 dark:text-slate-400">{a.speaker}</p>}
            </li>
          ))}
        </ol>
      </Card>
    </div>
  );
}
