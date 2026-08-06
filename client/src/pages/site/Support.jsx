import { FiAlertTriangle } from "react-icons/fi";
import SitePage from "../../layouts/SitePage";
import { Button, Card, Section, SectionHeading, FeatureCard, Heading, Text, Reveal } from "../../ui";
import { SUPPORT_CHANNELS, SUPPORT_EXPECTATIONS } from "../../data/site";

// Support.
//
// The severity table below states the operating INTENT, and says so — the platform does not
// aggregate achieved ticket response times, so quoting a measured figure here would be inventing
// one. The Sev 1 row deliberately tells you not to use a ticket, because a queue is the wrong
// channel for a broadcast that is already failing.
export default function Support() {
  return (
    <SitePage
      eyebrow="Support"
      title="Getting help, in the right order"
      lead="Most questions are answered in the docs or on the status page. When they are not, here is how to reach a person — and when not to use a form at all."
      crumbs={[["Support"]]}
      actions={
        <>
          <Button href="/contact" variant="primary" size="lg">
            Contact us
          </Button>
          <Button href="/status" variant="secondary" size="lg">
            Platform status
          </Button>
        </>
      }
    >
      <Section tone="base">
        {/* First, because it is the case where getting the channel wrong costs the most. */}
        <Card padding="xl" className="border-amber-300 bg-amber-50 dark:border-amber-500/30 dark:bg-amber-500/10">
          <div className="flex gap-4">
            <FiAlertTriangle
              className="mt-0.5 shrink-0 text-2xl text-amber-600 dark:text-amber-400"
              aria-hidden="true"
            />
            <div>
              <Heading level={2} size="h4" className="text-amber-900 dark:text-amber-200">
                A live broadcast is failing
              </Heading>
              <Text className="mt-2 text-sm leading-relaxed text-amber-800 dark:text-amber-300">
                Use the escalation path in your agreement — the phone number, not a ticket and not
                the contact form. A Sev 1 during an event needs a person now, and anything queued
                will be read after the event has ended, which is too late to help.
              </Text>
            </div>
          </div>
        </Card>

        <SectionHeading
          className="mt-20"
          eyebrow="Channels"
          title="Where to start"
          lead="In roughly the order that gets you an answer fastest."
        />
        <div className="mt-14 grid gap-5 sm:grid-cols-2">
          {SUPPORT_CHANNELS.map((c, i) => (
            <Reveal key={c.title} delay={i * 60}>
              <FeatureCard
                icon={c.icon}
                accent={c.accent}
                title={c.title}
                body={c.body}
                cta={c.cta}
                href={c.href}
              />
            </Reveal>
          ))}
        </div>
      </Section>

      <Section tone="subtle">
        <div className="mx-auto max-w-3xl">
          <SectionHeading
            eyebrow="Severity"
            title="What we aim for"
            lead="These are targets, not measurements — see the note below."
          />

          <dl className="mt-12 divide-y divide-slate-200 dark:divide-slate-800">
            {SUPPORT_EXPECTATIONS.map(([severity, target]) => (
              <div key={severity} className="grid gap-1 py-4 sm:grid-cols-[1.3fr_1fr] sm:gap-6">
                <dt className="text-sm font-semibold text-slate-900 dark:text-white">{severity}</dt>
                <dd className="text-sm text-slate-600 dark:text-slate-400">{target}</dd>
              </div>
            ))}
          </dl>

          <Card padding="lg" variant="subtle" className="mt-8">
            <Text className="text-sm leading-relaxed">
              <span className="font-semibold text-slate-800 dark:text-slate-200">
                Why there is no “average response time” here.
              </span>{" "}
              The platform does not aggregate achieved ticket response latency, so we have no
              measured figure to publish. Rather than print a number nobody computed, we state the
              target and leave the actual figure blank — the same rule our own operational consoles
              follow. Contractual response commitments live in your agreement.
            </Text>
          </Card>

          <div className="mt-8 flex flex-col gap-3 sm:flex-row">
            <Button href="/organization/support" variant="primary" size="lg">
              Open a request in the console
            </Button>
            <Button href="/docs" variant="secondary" size="lg">
              Search the docs
            </Button>
          </div>
        </div>
      </Section>
    </SitePage>
  );
}
