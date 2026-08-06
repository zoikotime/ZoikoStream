import { FiAlertCircle } from "react-icons/fi";
import SitePage from "../../layouts/SitePage";
import NotFound from "../NotFound";
import FactList from "../../components/site/FactList";
import { Button, Card, Section, SectionHeading, FeatureCard, Heading, Text, Reveal } from "../../ui";
import {
  LEGAL_PAGES, LEGAL_DISCLAIMER, SECURITY_PRACTICES, COMPLIANCE_POSTURE,
} from "../../data/site";

// Privacy / Terms / Security — one renderer, three routes.
//
// The disclaimer at the top is not boilerplate softening: this page describes how the platform
// actually behaves (drawn from the code), and it is NOT the executed contract. Presenting
// generated prose as binding legal terms would be the most harmful thing on this site, so the
// distinction is stated first and cannot be edited out of one page without the others.
//
// Security additionally carries the practices list (each item is a real control in this codebase)
// and a compliance posture where every certification is an explicit em dash — this repository
// holds no certification records, and a claimed SOC 2 badge would be a fabricated attestation.
export default function Legal({ doc }) {
  const page = LEGAL_PAGES[doc];

  // An unknown /legal/* path is a 404, not a blank shell.
  if (!page) return <NotFound />;

  const isSecurity = doc === "security";

  return (
    <SitePage
      eyebrow="Trust"
      title={page.title}
      lead={page.lead}
      crumbs={[["Trust"], [page.title]]}
      actions={
        <Button href="/contact" variant="secondary" size="lg">
          Request the signed documents
        </Button>
      }
    >
      <Section tone="base">
        <div className="mx-auto max-w-3xl">
          <Card padding="lg" variant="subtle">
            <div className="flex gap-3">
              <FiAlertCircle className="mt-0.5 shrink-0 text-lg text-slate-400" aria-hidden="true" />
              <Text className="text-sm leading-relaxed">{LEGAL_DISCLAIMER}</Text>
            </div>
          </Card>

          {page.sections.length > 0 && (
            <div className="mt-12 space-y-10">
              {page.sections.map((s) => (
                <section key={s.heading}>
                  <Heading level={2} size="h3">
                    {s.heading}
                  </Heading>
                  <Text className="mt-3 leading-relaxed">{s.body}</Text>
                </section>
              ))}
            </div>
          )}

          <div className="mt-12 flex flex-wrap gap-3 border-t border-slate-200 pt-8 dark:border-slate-800">
            {[
              ["Privacy", "/privacy"],
              ["Terms", "/terms"],
              ["Security", "/security"],
            ]
              .filter(([label]) => label !== page.title)
              .map(([label, href]) => (
                <Button key={href} href={href} variant="secondary" size="sm">
                  {label}
                </Button>
              ))}
            <Button href="/status" variant="ghost" size="sm">
              Platform status
            </Button>
          </div>
        </div>
      </Section>

      {isSecurity && (
        <>
          <Section tone="subtle">
            <SectionHeading
              eyebrow="Controls"
              title="What is actually enforced"
              lead="Each of these is a control in the platform, not an aspiration."
            />
            <div className="mt-14 grid gap-5 sm:grid-cols-2 lg:grid-cols-3">
              {SECURITY_PRACTICES.map((p, i) => (
                <Reveal key={p.title} delay={i * 60}>
                  <FeatureCard icon={p.icon} accent={p.accent} title={p.title} body={p.body} />
                </Reveal>
              ))}
            </div>
          </Section>

          <Section tone="base">
            <div className="mx-auto max-w-3xl">
              <SectionHeading
                eyebrow="Compliance"
                title="What we are not claiming"
                lead="Every line here is blank on purpose."
              />
              <Card padding="xl" className="mt-12">
                <FactList items={COMPLIANCE_POSTURE} />
                <Text className="mt-6 text-sm leading-relaxed">
                  This site holds no certification records, so it asserts no certifications. A badge
                  we could not evidence would be worth less than an honest blank — and an auditor
                  will ask for the report, not the badge. If your review needs a specific
                  attestation, ask us directly and we will tell you exactly where we stand.
                </Text>
                <Button href="/contact" variant="primary" className="mt-6">
                  Start a security review
                </Button>
              </Card>
            </div>
          </Section>
        </>
      )}
    </SitePage>
  );
}
