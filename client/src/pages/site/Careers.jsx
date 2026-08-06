import { FiBriefcase, FiMail } from "react-icons/fi";
import SitePage from "../../layouts/SitePage";
import { Button, Card, Section, SectionHeading, Heading, Text, Reveal } from "../../ui";
import { CAREERS_PRINCIPLES } from "../../data/site";

// Careers.
//
// There is no vacancies data source in this repository, so this page does not fabricate a list of
// roles with invented locations and salary bands — an open role is a commitment to a real person
// who might apply. It states how the team works (each item is observable in the codebase) and
// gives a real route in for a speculative application.
export default function Careers() {
  return (
    <SitePage
      eyebrow="Careers"
      title="Build the platform that cannot drop the broadcast"
      lead="Small team, high consequence. If you like owning a surface end to end and writing down why, this is the sort of work we do."
      crumbs={[["Company", "/company"], ["Careers"]]}
      actions={
        <Button href="/contact" variant="primary" size="lg">
          <FiMail /> Send a speculative application
        </Button>
      }
    >
      <Section tone="base">
        <SectionHeading
          eyebrow="How we work"
          title="Four things you would notice in the first week"
          lead="Each of these is visible in the codebase, not just in an onboarding deck."
        />
        <div className="mt-14 grid gap-5 sm:grid-cols-2">
          {CAREERS_PRINCIPLES.map(([title, body], i) => (
            <Reveal key={title} delay={i * 70}>
              <Card padding="xl" className="h-full">
                <Heading level={3} size="h4">
                  {title}
                </Heading>
                <Text className="mt-2 text-sm leading-relaxed">{body}</Text>
              </Card>
            </Reveal>
          ))}
        </div>
      </Section>

      <Section tone="subtle">
        <div className="mx-auto max-w-2xl text-center">
          <span
            className="mx-auto grid h-12 w-12 place-items-center rounded-2xl bg-slate-200 text-slate-500 dark:bg-slate-800 dark:text-slate-400"
            aria-hidden="true"
          >
            <FiBriefcase className="text-xl" />
          </span>
          <Heading level={2} size="h2" className="mt-6">
            No open roles listed right now
          </Heading>
          <Text tone="lead" className="mt-4">
            We would rather show you nothing than a list of roles that are not really open. When
            there is a vacancy it will be posted here with the team, the scope and the
            compensation range.
          </Text>
          <Text className="mt-4 text-sm">
            If you think you should be working here anyway, write to us. Tell us what you have
            built and what you would want to own — a CV alone tells us less than one paragraph
            about a hard problem you finished.
          </Text>
          <div className="mt-8 flex flex-col items-center justify-center gap-3 sm:flex-row">
            <Button href="/contact" variant="primary" size="lg">
              Get in touch
            </Button>
            <Button href="/company" variant="secondary" size="lg">
              About the company
            </Button>
          </div>
        </div>
      </Section>
    </SitePage>
  );
}
