import { Section, SectionHeading, Reveal, FAQ as FaqAccordion } from "../../../ui";
import { FAQS } from "../../../data/home";

export default function FAQ() {
  return (
    <Section id="faq" tone="base">
      <SectionHeading eyebrow="FAQ" title="Questions, answered" />
      <Reveal className="mx-auto mt-10 max-w-3xl">
        <FaqAccordion items={FAQS} searchable />
      </Reveal>
    </Section>
  );
}
