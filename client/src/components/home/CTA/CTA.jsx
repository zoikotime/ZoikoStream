import { FiArrowRight } from "react-icons/fi";
import { Section, SectionHeading, Reveal, Button, Card, ACCENT } from "../../../ui";
import { FINAL_CTA } from "../../../data/home";

export default function CTA() {
  return (
    <Section id="get-started" tone="dark">
      <SectionHeading
        eyebrow="Get started"
        title="Pick your path and start today"
        lead="Three ways in — each with its own next step. No credit card required to start building."
        className="[&_h2]:text-white [&_p]:text-white/70"
      />

      <div className="mt-14 grid grid-cols-1 gap-6 md:grid-cols-3">
        {FINAL_CTA.map((c, i) => (
          <Reveal key={c.key} delay={i * 100}>
            <Card variant="dark" padding="xl" className="flex h-full flex-col rounded-3xl transition hover:border-white/20 hover:bg-white/[0.08]">
              <span className={`grid h-12 w-12 place-items-center rounded-2xl ${ACCENT[c.accent].chip}`}>
                <c.icon className="text-2xl" />
              </span>
              <h3 className="mt-5 text-xl font-bold text-white">{c.title}</h3>
              <p className="mt-2 flex-1 text-sm leading-relaxed text-white/70">{c.body}</p>
              <Button href={c.href} variant="primary" size="lg" className="mt-6 w-full">
                {c.cta} <FiArrowRight />
              </Button>
            </Card>
          </Reveal>
        ))}
      </div>
    </Section>
  );
}
