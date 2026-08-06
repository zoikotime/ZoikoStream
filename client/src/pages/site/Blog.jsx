import SitePage from "../../layouts/SitePage";
import { Button, Card, Section, SectionHeading, FeatureCard, Heading, Text, Reveal } from "../../ui";
import { BLOG_TOPICS } from "../../data/site";

// Blog / engineering notes.
//
// There is no CMS or posts table in this repository, so this is not a fake article feed with
// invented dates and author names. Each entry is a real design decision in this platform, and
// links to the page where that decision is actually visible in the product — which is more use to
// a reader evaluating us than a post that doesn't exist.
export default function Blog() {
  return (
    <SitePage
      eyebrow="Engineering notes"
      title="Why the platform behaves the way it does"
      lead="Short notes on decisions that shaped ZoikoStream — each one links to where you can see it working."
      crumbs={[["Resources"], ["Blog"]]}
      actions={
        <Button href="/changelog" variant="secondary" size="lg">
          Changelog
        </Button>
      }
    >
      <Section tone="base">
        <SectionHeading
          eyebrow="Notes"
          title="Four decisions worth explaining"
          lead="These are the ones that surprise people most often in a technical review."
        />
        <div className="mt-14 grid gap-5 sm:grid-cols-2">
          {BLOG_TOPICS.map((t, i) => (
            <Reveal key={t.title} delay={i * 70}>
              <FeatureCard
                icon={t.icon}
                accent={t.accent}
                title={t.title}
                body={t.body}
                cta="See it in the product"
                href={t.read}
              />
            </Reveal>
          ))}
        </div>
      </Section>

      <Section tone="subtle">
        <Card padding="xl" variant="subtle" className="mx-auto max-w-3xl text-center">
          <Heading level={2} size="h4">
            No article feed yet
          </Heading>
          <Text className="mt-2 text-sm leading-relaxed">
            We publish notes when there is something worth writing down, not on a content calendar.
            Until there is a feed, the changelog is the place to follow what actually changed, and
            the docs are where the reasoning lives in full.
          </Text>
          <div className="mt-6 flex flex-col items-center justify-center gap-3 sm:flex-row">
            <Button href="/changelog" variant="primary">
              Read the changelog
            </Button>
            <Button href="/docs" variant="secondary">
              Browse the docs
            </Button>
          </div>
        </Card>
      </Section>
    </SitePage>
  );
}
