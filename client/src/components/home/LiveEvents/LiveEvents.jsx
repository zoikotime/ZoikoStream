import { useState } from "react";
import { FiPlayCircle, FiUsers, FiVideo, FiArrowRight } from "react-icons/fi";
import { Section, SectionHeading, Reveal, Button } from "../../../ui";
import { EVENT_CATEGORIES } from "../../../data/home";

// Interactive category selector: pick an event type, the management UI updates.
export default function LiveEvents() {
  const [active, setActive] = useState(EVENT_CATEGORIES[0].key);
  const cat = EVENT_CATEGORIES.find((c) => c.key === active);

  return (
    <Section id="live-events" tone="base">
      <SectionHeading
        eyebrow="Live events"
        title="Broadcasts for the moments that matter"
        lead="From a private memorial to a stadium graduation, run a professional live event with a production UI your whole team can operate."
      />

      <div className="mt-14 grid gap-6 lg:grid-cols-5">
        {/* Category selector */}
        <div className="lg:col-span-2">
          <div role="tablist" aria-label="Event categories" className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-2">
            {EVENT_CATEGORIES.map((c) => {
              const isActive = c.key === active;
              return (
                <button
                  key={c.key}
                  role="tab"
                  aria-selected={isActive}
                  onClick={() => setActive(c.key)}
                  className={`flex items-center gap-2.5 rounded-2xl border p-3.5 text-left transition-all duration-200 hover:-translate-y-0.5 ${
                    isActive
                      ? "border-emerald-300 bg-emerald-50 shadow-sm dark:border-emerald-500/40 dark:bg-emerald-500/10"
                      : "border-slate-200 bg-white hover:border-slate-300 hover:bg-slate-50 dark:border-slate-800 dark:bg-slate-900 dark:hover:border-slate-700 dark:hover:bg-slate-800"
                  }`}
                >
                  <span className={`grid h-9 w-9 shrink-0 place-items-center rounded-xl transition ${isActive ? "bg-emerald-600 text-white" : "bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300"}`}>
                    <c.icon className="text-lg" />
                  </span>
                  <span className={`text-sm font-semibold ${isActive ? "text-emerald-800 dark:text-emerald-300" : "text-slate-700 dark:text-slate-200"}`}>{c.title}</span>
                </button>
              );
            })}
          </div>
        </div>

        {/* Event management UI mockup */}
        <div className="lg:col-span-3">
          <div className="overflow-hidden rounded-3xl border border-slate-200 bg-slate-950 shadow-xl dark:border-slate-800">
            {/* Preview */}
            <div className="relative aspect-video bg-gradient-to-br from-indigo-900 via-slate-900 to-emerald-900">
              <span className="absolute left-3 top-3 flex items-center gap-1.5 rounded-md bg-black/50 px-2 py-1 text-[11px] font-semibold text-white">
                <span className="h-2 w-2 animate-pulse rounded-full bg-rose-500" /> LIVE
              </span>
              <span className="absolute right-3 top-3 flex items-center gap-1.5 rounded-md bg-black/50 px-2 py-1 text-[11px] font-medium text-white/90">
                <FiUsers className="text-xs" /> 3,412 watching
              </span>
              <span className="absolute left-1/2 top-1/2 grid h-16 w-16 -translate-x-1/2 -translate-y-1/2 place-items-center rounded-full bg-white/90 text-slate-900 shadow-lg">
                <FiPlayCircle className="text-3xl" />
              </span>
              <span className="absolute bottom-3 left-3 text-sm font-semibold text-white">{cat.title} · Main stage</span>
            </div>

            {/* Controls */}
            <div className="border-t border-white/10 p-5">
              <p className="text-sm leading-relaxed text-white/70">{cat.blurb}</p>
              <div className="mt-4 grid grid-cols-3 gap-2">
                {[
                  { icon: FiVideo, label: "Multi-camera" },
                  { icon: FiPlayCircle, label: "Auto-record" },
                  { icon: FiUsers, label: "Access control" },
                ].map((f) => (
                  <div key={f.label} className="flex flex-col items-center gap-1 rounded-xl bg-white/5 py-3 text-center">
                    <f.icon className="text-emerald-300" />
                    <span className="text-[11px] font-medium text-white/70">{f.label}</span>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
      </div>

      <Reveal className="mt-8 flex justify-center">
        <Button href="/live-events" variant="primary" size="lg">
          Explore live events <FiArrowRight />
        </Button>
      </Reveal>
    </Section>
  );
}
