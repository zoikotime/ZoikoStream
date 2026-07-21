import { Section, SectionHeading, Timeline } from "../../../ui";
import { LIFECYCLE } from "../../../data/home";

export default function MediaLifecycle() {
  return (
    <Section id="lifecycle" tone="base">
      <SectionHeading
        eyebrow="Media lifecycle"
        title="From contribution to preservation"
        lead="Every stage of the video lifecycle is handled on the same platform — no stitching together vendors, no gaps in security or telemetry."
      />
      <Timeline items={LIFECYCLE} className="mt-16" />
    </Section>
  );
}
