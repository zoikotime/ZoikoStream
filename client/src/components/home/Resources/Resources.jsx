import { Section, SectionHeading, Reveal, FeatureCard } from "../../../ui";
import { RESOURCES } from "../../../data/home";

export default function Resources() {
  return (
    <Section id="resources" tone="subtle">
      <SectionHeading
        eyebrow="Resources"
        title="Everything you need to go deeper"
        lead="Documentation, reference architectures, hands-on guides, and engineering write-ups."
      />

      <div className="mt-14 grid grid-cols-1 gap-5 sm:grid-cols-2 lg:grid-cols-4">
        {RESOURCES.map((r, i) => (
          <Reveal key={r.title} delay={i * 80}>
            <FeatureCard icon={r.icon} title={r.title} body={r.body} cta="Explore" href={r.href} />
          </Reveal>
        ))}
      </div>
    </Section>
  );
}
