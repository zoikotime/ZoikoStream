import { Section, SectionHeading, Reveal, Card } from "../../../ui";
import { TRUST } from "../../../data/home";

const BADGES = ["SOC 2 Type II", "GDPR", "ISO 27001", "HIPAA-ready", "WCAG 2.1 AA"];

export default function Trust() {
  return (
    <Section id="trust" tone="subtle">
      <SectionHeading
        eyebrow="Security + trust"
        title="Secure and compliant by default"
        lead="Security isn't a plan tier. Encryption, signed playback, resilience, and accessibility are built into every stream on every account."
      />

      <div className="mt-14 grid grid-cols-1 gap-5 sm:grid-cols-2 lg:grid-cols-3">
        {TRUST.map((t, i) => (
          <Reveal key={t.title} delay={i * 70}>
            <Card hover padding="lg" className="group flex h-full gap-4">
              <span className="grid h-11 w-11 shrink-0 place-items-center rounded-xl bg-slate-900 text-emerald-400 transition-transform duration-300 group-hover:scale-110 dark:bg-emerald-500/15">
                <t.icon className="text-xl" />
              </span>
              <div>
                <h3 className="font-semibold text-slate-900 dark:text-white">{t.title}</h3>
                <p className="mt-1 text-sm leading-relaxed text-slate-600 dark:text-slate-400">{t.body}</p>
              </div>
            </Card>
          </Reveal>
        ))}
      </div>

      <Reveal className="mt-10 flex flex-wrap items-center justify-center gap-3">
        {BADGES.map((b) => (
          <span key={b} className="rounded-full border border-slate-200 bg-white px-4 py-2 text-sm font-semibold text-slate-500 transition-colors hover:text-slate-700 dark:border-slate-800 dark:bg-slate-900 dark:text-slate-400 dark:hover:text-slate-200">
            {b}
          </span>
        ))}
      </Reveal>
    </Section>
  );
}
