import { FiRefreshCw, FiInfo } from "react-icons/fi";
import SitePage from "../../layouts/SitePage";
import api from "../../api";
import useApi from "../../hooks/useApi";
import useInterval from "../../hooks/useInterval";
import { Button, Card, Section, Heading, Text, Badge, Skeleton } from "../../ui";
import { cx, type } from "../../ui/tokens";
import HealthDot from "../../components/admin/HealthDot";
import FactList from "../../components/site/FactList";
import { STATUS_UNMEASURED } from "../../data/site";

// Public platform status.
//
// /admin/platform-health is super-admin only, so this page CANNOT read it — and it does not
// pretend to. What it does instead is the honest public equivalent: it probes the API's own
// reachability from the visitor's browser, reports that as a fact, and describes the service
// inventory statically.
//
// A public status page that showed green lights it had no way to verify would be worse than no
// status page. Signed-in operators get the real probed verdict in their console; this page says
// so and links there.
const REFRESH_MS = 30_000;

// Declared service inventory, split the way the platform itself splits it: probed services
// versus integrations that do not exist in this deployment. `not_configured` is NOT an outage.
const SERVICES = [
  ["API", "probed", "Serves every request on this page."],
  ["Authentication", "probed", "JWT verification for signed-in sessions."],
  ["Database", "probed", "Reachability is checked per request inside the API."],
  ["Streaming (LiveKit)", "declared", "Configured per deployment."],
  ["Email", "declared", "Configured per deployment."],
  ["Storage", "pending", "Google Cloud Storage is not yet integrated."],
  ["CDN", "pending", "Not yet integrated."],
  ["Background workers", "pending", "Not yet integrated."],
];

const GROUP = {
  probed: { label: "Probed", tone: "success" },
  declared: { label: "Deployment-configured", tone: "info" },
  pending: { label: "Not integrated", tone: "neutral" },
};

export default function Status() {
  // A cheap, unauthenticated read. Any 2xx/4xx answer proves the API is serving; only a network
  // failure means unreachable. /auth/me returning 401 for a visitor is still a healthy API,
  // which is why the error branch distinguishes a status code from no response at all.
  const { data, loading, error, reload } = useApi(() =>
    api
      .get("/auth/me")
      .then(() => ({ reachable: true, at: Date.now() }))
      .catch((e) => {
        if (e?.response?.status) return { reachable: true, at: Date.now() };
        throw e;
      })
  );
  useInterval(reload, REFRESH_MS);

  const reachable = Boolean(data?.reachable);
  const overall = loading ? "neutral" : reachable ? "ok" : "down";

  return (
    <SitePage
      eyebrow="Status"
      title="Platform status"
      lead="Live reachability, checked from your browser, plus the service inventory this deployment declares."
      crumbs={[["Status"]]}
      actions={
        <Button href="/support" variant="secondary" size="lg">
          Get support
        </Button>
      }
    >
      <Section tone="base">
        <Card padding="xl">
          <div className="flex flex-col gap-5 sm:flex-row sm:items-center sm:justify-between">
            <div className="flex items-center gap-4">
              {loading ? (
                <Skeleton variant="circle" className="h-11 w-11" />
              ) : (
                <span
                  className={cx(
                    "grid h-11 w-11 shrink-0 place-items-center rounded-2xl",
                    reachable
                      ? "bg-green-100 text-green-600 dark:bg-green-500/15 dark:text-green-400"
                      : "bg-rose-100 text-rose-600 dark:bg-rose-500/15 dark:text-rose-400"
                  )}
                  aria-hidden="true"
                >
                  <span className={cx("h-3 w-3 rounded-full", reachable ? "bg-green-500" : "bg-rose-500")} />
                </span>
              )}
              <div>
                <Heading level={2} size="h4">
                  {loading ? "Checking…" : reachable ? "The API is serving requests" : "The API is unreachable"}
                </Heading>
                <Text className="mt-1 text-sm">
                  {loading
                    ? "Probing from your browser."
                    : reachable
                      ? "Verified from this browser just now, and re-checked every 30 seconds."
                      : "This browser could not reach the API. It may be a local network issue rather than a platform outage."}
                </Text>
              </div>
            </div>
            <div className="flex shrink-0 items-center gap-3">
              <HealthDot status={overall} badge pulse={reachable} />
              <Button variant="secondary" onClick={reload} disabled={loading}>
                <FiRefreshCw /> Re-check
              </Button>
            </div>
          </div>

          {error && (
            <p className="mt-4 rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700 dark:border-rose-500/25 dark:bg-rose-500/10 dark:text-rose-300">
              No response from the API at all. If other sites are working for you, this is likely a
              platform problem — use the escalation path in your agreement if a broadcast is
              affected.
            </p>
          )}
        </Card>

        {/* The honest limitation, stated where it cannot be missed. */}
        <Card padding="lg" variant="subtle" className="mt-6">
          <div className="flex gap-3">
            <FiInfo className="mt-0.5 shrink-0 text-lg text-slate-400" aria-hidden="true" />
            <Text className="text-sm leading-relaxed">
              <span className="font-semibold text-slate-800 dark:text-slate-200">
                What this page can and cannot tell you.
              </span>{" "}
              The per-service probe — including the live database round trip — runs inside the
              authenticated platform console and is not exposed publicly. This page reports the one
              thing it can actually verify from here: whether the API answered. Signed-in operators
              see the full probed verdict on{" "}
              <a href="/organization/support" className="font-semibold text-emerald-700 underline decoration-emerald-500/40 dark:text-emerald-400">
                Support &amp; Status
              </a>
              .
            </Text>
          </div>
        </Card>
      </Section>

      <Section tone="subtle">
        <div className="grid gap-10 lg:grid-cols-[1.2fr_1fr] lg:gap-16">
          <div>
            <Heading level={2} size="h2">
              Service inventory
            </Heading>
            <Text className="mt-3 text-sm">
              Grouped the way the platform itself groups them. An integration that does not exist in
              this deployment is listed as not integrated — it is not failing, and it is never
              counted as an outage.
            </Text>

            <ul className="mt-8 divide-y divide-slate-200 dark:divide-slate-800">
              {SERVICES.map(([name, group, note]) => (
                <li key={name} className="flex items-start justify-between gap-4 py-3.5">
                  <div className="min-w-0">
                    <p className="text-sm font-semibold text-slate-900 dark:text-white">{name}</p>
                    <p className="mt-0.5 text-xs text-slate-500 dark:text-slate-400">{note}</p>
                  </div>
                  <Badge status={GROUP[group].tone} className="shrink-0">
                    {GROUP[group].label}
                  </Badge>
                </li>
              ))}
            </ul>
          </div>

          <Card padding="xl" className="self-start">
            <Heading level={3} size="h4">
              Not published here
            </Heading>
            <Text className="mt-2 text-sm">
              Each of these is blank because nothing produces it — not because the value is zero.
            </Text>
            <FactList
              items={STATUS_UNMEASURED.map((u) => ({ label: u.label, value: null, reason: u.reason }))}
              className="mt-5"
            />
            <p className={cx("mt-5 text-xs", type.mono, "text-slate-400 dark:text-slate-500")}>
              Re-checked every {REFRESH_MS / 1000}s
            </p>
          </Card>
        </div>
      </Section>
    </SitePage>
  );
}
