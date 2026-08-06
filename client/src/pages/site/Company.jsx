import { FiArrowRight, FiMail } from "react-icons/fi";
import SitePage from "../../layouts/SitePage";
import FactList from "../../components/site/FactList";
import {
  Button, Card, Section, SectionHeading, FeatureCard, Heading, Text, Reveal,
} from "../../ui";
import { COMPANY_INTRO, COMPANY_PRINCIPLES, COMPANY_FACTS } from "../../data/site";

// About Us / Company.
//
// The principles below are drawn from decisions that are actually enforced in this codebase —
// readiness gates that block, access evaluated on the media, unmeasured values rendered as an em
// dash — so the page describes the product rather than asserting a culture. Company facts this
// repository does not contain (headcount, founding date, headquarters) are rendered as em dashes
// with the reason on hover, which is the same rule the consoles follow.
export default function Company() {
  return (
    <SitePage
      eyebrow="Company"
      title="Video infrastructure for moments that only happen once"
      lead={COMPANY_INTRO}
      crumbs={[["Company"]]}
      actions={
        <>
          <Button href="/contact" variant="primary" size="lg">
            Talk to an expert
          </Button>
          <Button href="/careers" variant="secondary" size="lg">
            Open roles
          </Button>
        </>
      }
    >
      <Section tone="base">
        <SectionHeading
          eyebrow="What we build for"
          title="Four commitments the product is built around"
          lead="Each of these is visible in how the platform behaves, not only in how it is described."
        />
        <div className="mt-14 grid gap-5 sm:grid-cols-2">
          {COMPANY_PRINCIPLES.map((p, i) => (
            <Reveal key={p.title} delay={i * 70}>
              <FeatureCard icon={p.icon} accent={p.accent} title={p.title} body={p.body} />
            </Reveal>
          ))}
        </div>
      </Section>

      <Section tone="subtle">
        <div className="grid gap-10 lg:grid-cols-[1.2fr_1fr] lg:gap-16">
          <div>
            <Heading level={2} size="h2">
              Part of Zoiko Group
            </Heading>
            <Text tone="lead" className="mt-4">
              ZoikoStream is the video platform inside Zoiko Group&apos;s product portfolio. It exists
              because the organisations we work with — enterprises, broadcasters, universities,
              places of worship, event teams — kept needing the same three things at once: a real
              API, an operator interface for people running a broadcast live, and an access model
              they could defend to a compliance team.
            </Text>
            <Text className="mt-4">
              Most platforms give you one of the three. Building all of them into one system is the
              whole point: the readiness verdict a producer sees in the console is the same verdict
              the API enforces, because only one of them computes it.
            </Text>
            <div className="mt-8 flex flex-wrap gap-3">
              <Button href="/solutions" variant="dark">
                Who it is for
              </Button>
              <Button href="/docs/architecture" variant="ghost">
                How it is built <FiArrowRight className="ml-1" />
              </Button>
            </div>
          </div>

          <Card padding="xl" className="self-start">
            <Heading level={3} size="h4">
              Company facts
            </Heading>
            <Text className="mt-2 text-sm">
              Anything this site cannot source is left blank rather than filled in.
            </Text>
            <FactList items={COMPANY_FACTS} className="mt-5" />
            <Button href="/contact" variant="secondary" className="mt-6 w-full">
              <FiMail /> Ask us directly
            </Button>
          </Card>
        </div>
      </Section>

      <Section tone="base">
        <div className="mx-auto max-w-3xl text-center">
          <Heading level={2} size="h2">
            Work with us
          </Heading>
          <Text tone="lead" className="mt-4">
            Whether you are integrating the API, moving a weekly service onto the platform, or
            handing us a broadcast that cannot fail — start with a conversation, not a trial form.
          </Text>
          <div className="mt-8 flex flex-col items-center justify-center gap-3 sm:flex-row">
            <Button href="/contact" variant="primary" size="lg">
              Talk to an expert
            </Button>
            <Button href="/pricing" variant="secondary" size="lg">
              See plans
            </Button>
          </div>
        </div>
      </Section>
    </SitePage>
  );
}
