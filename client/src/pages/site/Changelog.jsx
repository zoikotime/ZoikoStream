import { FiAlertTriangle, FiTag } from "react-icons/fi";
import SitePage from "../../layouts/SitePage";
import { Button, Card, Section, Heading, Text, Badge } from "../../ui";
import { cx, type } from "../../ui/tokens";
import { CHANGELOG, API_VERSION } from "../../data/site";

// Changelog.
//
// Only two things in this platform are genuinely versioned and dated: the published API version
// and the announced SDK deprecation — both of which the org console's Developer Platform page
// already displays. Those are what this page lists. It does not manufacture a release history,
// because a dated entry that never shipped is the one kind of changelog error that erodes trust
// in every other entry.
const CHANNEL = {
  api: { tone: "info", label: "API version" },
  deprecation: { tone: "warning", label: "Deprecation" },
};

export default function Changelog() {
  return (
    <SitePage
      eyebrow="Changelog"
      title="What changed, and what is going away"
      lead={`The published API version is ${API_VERSION}. Breaking changes arrive as a new dated version; deprecations are announced with a date, never removed quietly.`}
      crumbs={[["Resources"], ["Changelog"]]}
      actions={
        <Button href="/docs/api" variant="secondary" size="lg">
          API reference
        </Button>
      }
    >
      <Section tone="base">
        <div className="mx-auto max-w-3xl">
          <ol className="space-y-5">
            {CHANGELOG.map((entry) => {
              const channel = CHANNEL[entry.channel] || CHANNEL.api;
              const upcoming = new Date(entry.date) > new Date();
              return (
                <li key={entry.version}>
                  <Card padding="xl">
                    <div className="flex flex-wrap items-center gap-2.5">
                      <Badge status={channel.tone}>{channel.label}</Badge>
                      <span className={cx("text-xs font-semibold", type.mono, "text-slate-500 dark:text-slate-400")}>
                        {entry.version}
                      </span>
                      <span className="text-xs text-slate-400 dark:text-slate-500">
                        {upcoming ? "effective " : ""}
                        {new Date(entry.date).toLocaleDateString(undefined, {
                          year: "numeric",
                          month: "long",
                          day: "numeric",
                        })}
                      </span>
                    </div>

                    <Heading level={2} size="h4" className="mt-4">
                      {entry.title}
                    </Heading>

                    <ul className="mt-4 space-y-2">
                      {entry.notes.map((note) => (
                        <li
                          key={note}
                          className="flex items-start gap-2.5 text-sm leading-relaxed text-slate-600 dark:text-slate-400"
                        >
                          {entry.channel === "deprecation" ? (
                            <FiAlertTriangle className="mt-0.5 shrink-0 text-amber-500" aria-hidden="true" />
                          ) : (
                            <FiTag className="mt-0.5 shrink-0 text-slate-400" aria-hidden="true" />
                          )}
                          {note}
                        </li>
                      ))}
                    </ul>
                  </Card>
                </li>
              );
            })}
          </ol>

          <Card padding="lg" variant="subtle" className="mt-6">
            <Text className="text-sm leading-relaxed">
              <span className="font-semibold text-slate-800 dark:text-slate-200">Versioning policy.</span>{" "}
              Additive changes ship continuously and do not move the version. Anything that could
              break a working integration gets a new dated version, and the previous one keeps
              working. Deprecations are announced here with an effective date before support ends.
            </Text>
            <div className="mt-4 flex flex-wrap gap-3">
              <Button href="/docs/sdks" variant="secondary" size="sm">
                SDK versions
              </Button>
              <Button href="/status" variant="ghost" size="sm">
                Platform status
              </Button>
            </div>
          </Card>
        </div>
      </Section>
    </SitePage>
  );
}
