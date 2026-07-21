import { Section, SectionHeading, Reveal, FeatureCard } from "../../../ui";
import { PROOF } from "../../../data/home";

export default function Proof() {
  return (
    <Section id="proof" tone="base">
      <SectionHeading
        eyebrow="Proof, not promises"
        title="See exactly how it works before you commit"
        lead="Read the architecture, watch the status page, and build against a sandbox that mirrors production. No sales gate required."
      />

      <div className="mt-14 grid grid-cols-1 gap-5 sm:grid-cols-2 lg:grid-cols-3">
        {PROOF.map((item, i) => (
          <Reveal key={item.title} delay={i * 70}>
            <FeatureCard icon={item.icon} title={item.title} body={item.body} cta={item.cta} href={item.href} />
          </Reveal>
        ))}
      </div>
    </Section>
  );
}
