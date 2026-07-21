import { Section, SectionHeading, Reveal, FeatureCard } from "../../../ui";
import { PATHWAYS } from "../../../data/home";

export default function AudiencePathways() {
  return (
    <Section id="pathways" tone="subtle">
      <SectionHeading
        eyebrow="Solutions"
        title="Built for how your team ships video"
        lead="Whether you're embedding video in a product, running media operations at scale, or broadcasting a once-in-a-lifetime event — start from the path that fits."
      />

      <div className="mt-14 grid grid-cols-1 gap-6 md:grid-cols-3">
        {PATHWAYS.map((p, i) => (
          <Reveal key={p.key} delay={i * 100}>
            <FeatureCard
              icon={p.icon}
              title={p.title}
              body={p.body}
              accent={p.accent}
              points={p.points}
              cta={p.cta}
              href={p.href}
            />
          </Reveal>
        ))}
      </div>
    </Section>
  );
}
