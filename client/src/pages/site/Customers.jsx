import { FiUsers, FiArrowRight } from "react-icons/fi";
import SitePage from "../../layouts/SitePage";
import { Button, Card, Section, SectionHeading, FeatureCard, Heading, Text, Reveal } from "../../ui";
import { SOLUTIONS } from "../../data/site";

// Customers.
//
// This repository contains no customer records, logos, quotes or case-study figures, and a
// customers page is exactly where a platform is most tempted to invent them. So this page does
// not: it describes the workloads that run on ZoikoStream and states plainly that named
// references come from a conversation, under whatever confidentiality the customer requires.
//
// A wall of invented logos would be a fabricated endorsement of a real, identifiable
// organisation. That is not a design shortcut, it is a lie about someone else.
export default function Customers() {
  return (
    <SitePage
      eyebrow="Customers"
      title="Who runs broadcasts on ZoikoStream"
      lead="Enterprises, broadcasters, universities, places of worship and event teams — the common thread is a broadcast where failure has a real cost."
      crumbs={[["Customers"]]}
      actions={
        <Button href="/contact" variant="primary" size="lg">
          Ask for references
        </Button>
      }
    >
      <Section tone="base">
        <Card padding="xl" variant="subtle">
          <div className="flex flex-col gap-5 sm:flex-row sm:items-center sm:justify-between">
            <div className="flex gap-4">
              <span
                className="grid h-11 w-11 shrink-0 place-items-center rounded-2xl bg-slate-200 text-slate-500 dark:bg-slate-800 dark:text-slate-400"
                aria-hidden="true"
              >
                <FiUsers className="text-xl" />
              </span>
              <div className="max-w-2xl">
                <Heading level={2} size="h4">
                  We don&apos;t publish a logo wall
                </Heading>
                <Text className="mt-2 text-sm leading-relaxed">
                  Most of what runs on this platform is internal, regulated, or belongs to an
                  institution that has not agreed to be named — and several customers ask us
                  specifically not to. Named references are shared in a conversation, with their
                  permission, matched to a workload like yours.
                </Text>
              </div>
            </div>
            <Button href="/contact" variant="dark" className="shrink-0">
              Ask for references <FiArrowRight className="ml-1" />
            </Button>
          </div>
        </Card>
      </Section>

      <Section tone="subtle">
        <SectionHeading
          eyebrow="Workloads"
          title="What people actually broadcast"
          lead="Described by shape rather than by name."
        />
        <div className="mt-14 grid gap-5 sm:grid-cols-2 lg:grid-cols-3">
          {SOLUTIONS.map((s, i) => (
            <Reveal key={s.id} delay={i * 60}>
              <FeatureCard
                icon={s.icon}
                accent={s.accent}
                title={s.title}
                body={s.body}
                cta="See the solution"
                href={`/solutions#${s.id}`}
              />
            </Reveal>
          ))}
        </div>
      </Section>

      <Section tone="base">
        <div className="mx-auto max-w-3xl text-center">
          <Heading level={2} size="h2">
            Want to talk to someone doing what you are doing?
          </Heading>
          <Text tone="lead" className="mt-4">
            Tell us the workload — a weekly service, a results call, a graduation — and we will
            introduce you to a customer running the same shape of broadcast, if they are willing.
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
