import { FiArrowDown } from "react-icons/fi";
import { Section, SectionHeading, Reveal, FeatureCard } from "../../../ui";
import { PLATFORM_PILLARS } from "../../../data/home";

// Simple architecture illustration: three layered planes (edge / core / apps).
function ArchitectureDiagram() {
  const layers = [
    { label: "Applications", sub: "Web · Mobile · TV · Embedded", tone: "border-emerald-300 bg-emerald-50 text-emerald-800 dark:border-emerald-500/30 dark:bg-emerald-500/10 dark:text-emerald-300" },
    { label: "ZoikoStream API", sub: "Ingest · Transcode · Secure · Deliver · Analyze", tone: "border-indigo-300 bg-indigo-50 text-indigo-800 dark:border-indigo-500/30 dark:bg-indigo-500/10 dark:text-indigo-300" },
    { label: "Global edge infrastructure", sub: "Multi-CDN · 60+ regions · Durable storage", tone: "border-slate-300 bg-slate-100 text-slate-700 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-300" },
  ];
  return (
    <div className="mx-auto flex max-w-3xl flex-col items-center gap-2">
      {layers.map((l, i) => (
        <div key={l.label} className="w-full" style={{ maxWidth: `${100 - i * 6}%` }}>
          <Reveal delay={i * 100}>
            <div className={`rounded-2xl border ${l.tone} px-6 py-4 text-center shadow-sm`}>
              <p className="font-semibold">{l.label}</p>
              <p className="mt-0.5 text-sm opacity-80">{l.sub}</p>
            </div>
          </Reveal>
          {i < layers.length - 1 && (
            <div className="flex justify-center py-1 text-slate-300 dark:text-slate-700">
              <FiArrowDown />
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

export default function PlatformDefinition() {
  return (
    <Section id="platform" tone="base">
      <SectionHeading
        eyebrow="What is ZoikoStream?"
        title="One platform for the entire video stack"
        lead="ZoikoStream is the infrastructure layer for video — everything between the camera and the viewer, exposed through a single secure API so your team ships features instead of operating servers."
      />

      <div className="mt-14">
        <ArchitectureDiagram />
      </div>

      <div className="mt-14 grid grid-cols-1 gap-5 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-5">
        {PLATFORM_PILLARS.map((p, i) => (
          <Reveal key={p.title} delay={i * 80}>
            <FeatureCard icon={p.icon} title={p.title} body={p.body} />
          </Reveal>
        ))}
      </div>
    </Section>
  );
}
