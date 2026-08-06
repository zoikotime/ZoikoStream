import { FiCheck, FiShield } from "react-icons/fi";
import SitePage from "../../layouts/SitePage";
import { Button, Card, Section, SectionHeading, Heading, Text, Badge, Reveal } from "../../ui";
import { cx } from "../../ui/tokens";
import { LIVE_EVENT_PHASES, LIVE_EVENT_ASSURANCES } from "../../data/site";

// Live Events — the public page behind the homepage's Live Events call to action, which until now
// pointed at a route that did not exist.
//
// The five phases are the real lifecycle of a managed event on this platform, and the readiness
// language matches what the console actually enforces: gates are evaluated per impact class, and
// for an unrepeatable event a failing mandatory gate BLOCKS rather than warns.
export default function LiveEventsPage() {
  return (
    <SitePage
      eyebrow="Live Events"
      title="For the broadcast that only happens once"
      lead="Managed production for graduations, shareholder meetings, launches and services — where discovering a problem on air is not an acceptable outcome."
      crumbs={[["Live Events"]]}
      actions={
        <>
          <Button href="/contact" variant="primary" size="lg">
            Request an event
          </Button>
          <Button href="/pricing" variant="secondary" size="lg">
            See pricing
          </Button>
        </>
      }
    >
      <Section tone="base">
        <SectionHeading
          eyebrow="How it runs"
          title="Five phases, each with something that has to be true before the next"
          lead="This is the same sequence the platform models internally — the readiness verdict you see is the one the API enforces."
        />

        <ol className="mt-14 space-y-5">
          {LIVE_EVENT_PHASES.map((phase, i) => (
            <Reveal key={phase.id} delay={i * 60}>
              <Card padding="xl" id={phase.id} className="scroll-mt-28">
                <div className="grid gap-6 lg:grid-cols-[auto_1fr_auto] lg:items-start lg:gap-10">
                  {/* Step number doubles as the visual rhythm of the list. */}
                  <span
                    className={cx(
                      "grid h-11 w-11 shrink-0 place-items-center rounded-2xl text-base font-bold",
                      "bg-emerald-100 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-400"
                    )}
                    aria-hidden="true"
                  >
                    {i + 1}
                  </span>

                  <div className="min-w-0">
                    <Heading level={3} size="h3">
                      {phase.title}
                    </Heading>
                    <Text className="mt-2 max-w-2xl text-sm leading-relaxed">{phase.body}</Text>
                  </div>

                  <ul className="grid gap-2 lg:w-64">
                    {phase.points.map((p) => (
                      <li key={p} className="flex items-start gap-2 text-sm text-slate-700 dark:text-slate-300">
                        <FiCheck className="mt-0.5 shrink-0 text-emerald-500" aria-hidden="true" />
                        {p}
                      </li>
                    ))}
                  </ul>
                </div>
              </Card>
            </Reveal>
          ))}
        </ol>
      </Section>

      <Section tone="subtle">
        <SectionHeading
          eyebrow="What you are actually buying"
          title="Three promises that are enforced, not asserted"
        />
        <div className="mt-14 grid gap-5 lg:grid-cols-3">
          {LIVE_EVENT_ASSURANCES.map(([title, body], i) => (
            <Reveal key={title} delay={i * 70}>
              <Card padding="xl" className="h-full">
                <span
                  className="grid h-11 w-11 place-items-center rounded-2xl bg-violet-100 text-violet-600 dark:bg-violet-500/15 dark:text-violet-400"
                  aria-hidden="true"
                >
                  <FiShield className="text-xl" />
                </span>
                <Heading level={3} size="h4" className="mt-5">
                  {title}
                </Heading>
                <Text className="mt-2 text-sm leading-relaxed">{body}</Text>
              </Card>
            </Reveal>
          ))}
        </div>
      </Section>

      <Section tone="dark">
        <div className="mx-auto max-w-3xl text-center">
          <Badge status="live" live>
            Managed events
          </Badge>
          <Heading level={2} size="h2" className="mt-5 text-white">
            Tell us the date first
          </Heading>
          <Text tone="lead" className="mt-4 text-white/70">
            Readiness work is the part that prevents failure, and it needs lead time. If you have a
            date, start with that — everything else can be scoped afterwards.
          </Text>
          <div className="mt-8 flex flex-col items-center justify-center gap-3 sm:flex-row">
            <Button href="/contact" variant="primary" size="lg">
              Request an event
            </Button>
            <Button href="/docs/guides" variant="outlineLight" size="lg">
              Read the operator guides
            </Button>
          </div>
        </div>
      </Section>
    </SitePage>
  );
}
