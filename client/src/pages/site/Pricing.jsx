import { useState } from "react";
import { FiCheck, FiInfo } from "react-icons/fi";
import SitePage from "../../layouts/SitePage";
import { Button, Card, Section, SectionHeading, Heading, Text, Badge, FAQ, Reveal } from "../../ui";
import { cx } from "../../ui/tokens";
import { plans, fmtGB } from "../../data/billing";
import { PRICING_NOTES, PRICING_FAQ } from "../../data/site";

// Pricing.
//
// The tiers, prices, limits and features here are imported from data/billing.js — the SAME module
// the signed-in Usage & Entitlements page reads. That is deliberate: a public price list and an
// in-app plan comparison that disagree is a support ticket waiting to happen, and inventing a
// second set of numbers for the marketing site is how that happens.
//
// The annual toggle applies a stated discount to the stated monthly price rather than quoting a
// separate annual figure, so the arithmetic is checkable on the page.
const ANNUAL_MONTHS_FREE = 2;

const RECOMMENDED = "business";

export default function Pricing() {
  const [annual, setAnnual] = useState(false);

  const priceFor = (plan) =>
    annual ? Math.round((plan.price * (12 - ANNUAL_MONTHS_FREE)) / 12) : plan.price;

  return (
    <SitePage
      eyebrow="Pricing"
      title="Capacity-based plans, one complete API"
      lead="Plans differ by capacity, retention and support response — never by which endpoints you can call. An integration built on Starter does not need rewriting on Enterprise."
      crumbs={[["Pricing"]]}
      actions={
        <>
          <Button href="/signup" variant="primary" size="lg">
            Start building
          </Button>
          <Button href="/contact" variant="secondary" size="lg">
            Talk to an expert
          </Button>
        </>
      }
    >
      <Section tone="base">
        {/* Billing period. A real control, and the discount is stated as arithmetic. */}
        <div className="flex flex-col items-center gap-3">
          <div
            className="inline-flex rounded-xl bg-slate-100 p-1 dark:bg-slate-900"
            role="group"
            aria-label="Billing period"
          >
            {[
              [false, "Monthly"],
              [true, `Annual — ${ANNUAL_MONTHS_FREE} months free`],
            ].map(([value, label]) => (
              <button
                key={label}
                type="button"
                onClick={() => setAnnual(value)}
                aria-pressed={annual === value}
                className={cx(
                  "rounded-lg px-4 py-2 text-sm font-semibold transition-colors duration-200 motion-reduce:transition-none",
                  annual === value
                    ? "bg-white text-slate-900 shadow-sm dark:bg-slate-800 dark:text-white"
                    : "text-slate-600 hover:text-slate-900 dark:text-slate-400 dark:hover:text-white"
                )}
              >
                {label}
              </button>
            ))}
          </div>
          <p className="text-xs text-slate-500 dark:text-slate-400">
            {annual
              ? `Billed annually — ${ANNUAL_MONTHS_FREE} of 12 months free, shown below as an effective monthly rate.`
              : "Billed monthly, per organization, in USD."}
          </p>
        </div>

        <div className="mt-12 grid gap-6 lg:grid-cols-3">
          {plans.map((plan, i) => {
            const featured = plan.id === RECOMMENDED;
            return (
              <Reveal key={plan.id} delay={i * 80}>
                <Card
                  padding="xl"
                  hover
                  className={cx(
                    "flex h-full flex-col",
                    featured && "border-emerald-300 ring-1 ring-emerald-500/30 dark:border-emerald-500/40"
                  )}
                >
                  <div className="flex items-center justify-between gap-3">
                    <Heading level={3} size="h4">
                      {plan.name}
                    </Heading>
                    {featured && <Badge status="active">Most chosen</Badge>}
                  </div>
                  <Text className="mt-2 text-sm">{plan.blurb}</Text>

                  <p className="mt-6 flex items-baseline gap-1.5">
                    <span className="text-4xl font-bold tracking-tight text-slate-900 tabular-nums dark:text-white">
                      ${priceFor(plan).toLocaleString()}
                    </span>
                    <span className="text-sm text-slate-500 dark:text-slate-400">/month</span>
                  </p>
                  {annual && (
                    <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
                      ${(priceFor(plan) * 12).toLocaleString()} billed annually · was $
                      {(plan.price * 12).toLocaleString()}
                    </p>
                  )}

                  <Button
                    href={plan.id === "enterprise" ? "/contact" : "/signup"}
                    variant={featured ? "primary" : "secondary"}
                    size="lg"
                    className="mt-6 w-full"
                  >
                    {plan.id === "enterprise" ? "Talk to sales" : "Start building"}
                  </Button>

                  {/* Limits first — they are what actually separates the tiers. */}
                  <dl className="mt-7 grid grid-cols-2 gap-3 border-t border-slate-200 pt-6 dark:border-slate-800">
                    {[
                      ["Streaming hours", plan.limits.streamingHours.toLocaleString()],
                      ["Storage", fmtGB(plan.limits.storageGB)],
                      ["Team members", plan.limits.users.toLocaleString()],
                      ["Events / month", plan.limits.events.toLocaleString()],
                    ].map(([label, value]) => (
                      <div key={label}>
                        <dt className="text-xs text-slate-500 dark:text-slate-400">{label}</dt>
                        <dd className="text-sm font-semibold tabular-nums text-slate-900 dark:text-white">
                          {value}
                        </dd>
                      </div>
                    ))}
                  </dl>

                  <ul className="mt-6 flex-1 space-y-2.5">
                    {plan.features.map((f) => (
                      <li key={f} className="flex items-start gap-2 text-sm text-slate-700 dark:text-slate-300">
                        <FiCheck className="mt-0.5 shrink-0 text-emerald-500" aria-hidden="true" />
                        {f}
                      </li>
                    ))}
                  </ul>
                </Card>
              </Reveal>
            );
          })}
        </div>

        {/* Managed events sit outside the plan grid because they are quoted per event. */}
        <Card padding="xl" variant="subtle" className="mt-6">
          <div className="flex flex-col gap-6 lg:flex-row lg:items-center lg:justify-between">
            <div className="max-w-2xl">
              <Heading level={3} size="h4">
                Managed Live Events
              </Heading>
              <Text className="mt-2 text-sm">
                For a broadcast that cannot be repeated: our operators run readiness, production and
                escalation with you. Quoted per event, on top of any plan — including no plan.
              </Text>
            </div>
            <div className="flex shrink-0 flex-wrap gap-3">
              <Button href="/live-events" variant="dark">
                How it works
              </Button>
              <Button href="/contact" variant="primary">
                Request a quote
              </Button>
            </div>
          </div>
        </Card>

        <ul className="mt-8 grid gap-2 sm:grid-cols-2">
          {PRICING_NOTES.map((note) => (
            <li key={note} className="flex items-start gap-2 text-xs text-slate-500 dark:text-slate-400">
              <FiInfo className="mt-0.5 shrink-0" aria-hidden="true" />
              {note}
            </li>
          ))}
        </ul>
      </Section>

      <Section tone="subtle">
        <div className="mx-auto max-w-3xl">
          <SectionHeading eyebrow="Pricing questions" title="What people ask before signing" />
          <div className="mt-10">
            <FAQ items={PRICING_FAQ} />
          </div>
        </div>
      </Section>
    </SitePage>
  );
}
