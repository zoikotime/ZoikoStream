import SitePage from "../../layouts/SitePage";
import { Button, Section, SectionHeading, FeatureCard, Heading, Text, Reveal } from "../../ui";
import { SOLUTIONS, PLATFORM_PILLAR_DETAIL } from "../../data/site";

// Solutions, plus the platform pillars.
//
// One page rather than nine thin ones. The footer's Solutions column (Enterprise, Media &
// Broadcast, Education, Worship, Events) and Platform column (Infrastructure, Streaming,
// Security, Analytics) all resolve to anchors here, so every one of those nine links reaches
// real content instead of a page that says the same thing five ways.
export default function Solutions() {
  return (
    <SitePage
      eyebrow="Solutions"
      title="Built for the people who have to make the broadcast happen"
      lead="The same platform underneath, with the surfaces and safeguards each kind of workload actually needs."
      crumbs={[["Solutions"]]}
      actions={
        <>
          <Button href="/contact" variant="primary" size="lg">
            Talk to an expert
          </Button>
          <Button href="/pricing" variant="secondary" size="lg">
            See plans
          </Button>
        </>
      }
    >
      <Section tone="base">
        <SectionHeading
          eyebrow="By workload"
          title="Five shapes of broadcast"
          lead="Pick the one closest to yours — the differences are in the operator surfaces and the access model, not the API."
        />
        <div className="mt-14 grid gap-5 sm:grid-cols-2 lg:grid-cols-3">
          {SOLUTIONS.map((s, i) => (
            <Reveal key={s.id} delay={i * 60}>
              {/* id on a wrapper so the footer's deep links land on the card, and
                  scroll-mt keeps the fixed header from covering it. */}
              <div id={s.id} className="h-full scroll-mt-28">
                <FeatureCard
                  icon={s.icon}
                  accent={s.accent}
                  title={s.title}
                  body={s.body}
                  points={s.points}
                />
              </div>
            </Reveal>
          ))}
        </div>
      </Section>

      <Section tone="subtle">
        <SectionHeading
          eyebrow="Platform"
          title="What sits underneath all five"
          lead="Four pillars, each measured and reported per stage and per region."
        />
        <div className="mt-14 grid gap-5 sm:grid-cols-2">
          {PLATFORM_PILLAR_DETAIL.map((p, i) => (
            <Reveal key={p.id} delay={i * 60}>
              <div id={p.id} className="h-full scroll-mt-28">
                <FeatureCard
                  icon={p.icon}
                  accent={p.accent}
                  title={p.title}
                  body={p.body}
                  points={p.points}
                />
              </div>
            </Reveal>
          ))}
        </div>
      </Section>

      <Section tone="base">
        <div className="mx-auto max-w-3xl text-center">
          <Heading level={2} size="h2">
            Not sure which one you are?
          </Heading>
          <Text tone="lead" className="mt-4">
            Most teams are a mix — a weekly service and an annual conference have very different
            failure costs. Tell us both and we will tell you which parts of the platform matter.
          </Text>
          <div className="mt-8 flex flex-col items-center justify-center gap-3 sm:flex-row">
            <Button href="/contact" variant="primary" size="lg">
              Talk to an expert
            </Button>
            <Button href="/live-events" variant="secondary" size="lg">
              How managed events work
            </Button>
          </div>
        </div>
      </Section>
    </SitePage>
  );
}
