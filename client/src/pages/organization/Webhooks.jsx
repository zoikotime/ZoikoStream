import { useMemo } from "react";
import { Link } from "react-router-dom";
import {
  FiLink2, FiCopy, FiLifeBuoy, FiRefreshCw, FiAlertTriangle, FiCheckCircle,
} from "react-icons/fi";
import api from "../../api";
import useApi from "../../hooks/useApi";
import { CONSOLE, cx, focusRing, type } from "../../ui/tokens";
import { ConsoleButton } from "../../ui/Button";
import Badge from "../../ui/Badge";
import CodeBlock from "../../ui/CodeBlock";
import { notify } from "../../ui/Toast";
import Panel from "../../components/admin/Panel";
import StatRow from "../../components/admin/StatRow";
import OrganizationPageHeader from "../../components/organization/OrganizationPageHeader";
import OrganizationEmptyState from "../../components/organization/OrganizationEmptyState";
import OrganizationErrorState from "../../components/organization/OrganizationErrorState";

// Webhooks — the endpoints this organization receives platform events on.
//
// READ-ONLY, and deliberately so on two counts:
//   • the endpoint list is real (GET /organization/developer → webhook_urls), but there is no
//     org-scoped write endpoint, so registering routes to Support as on Credentials;
//   • DELIVERY OUTCOMES ARE NOT RECORDED anywhere in this stack (services/org.developer_ops
//     documents webhook_failure_streaks as unmeasured), so there is no attempt log to show and
//     no redelivery to trigger. POST /webhooks/resend is NOT that — it is the INBOUND
//     Svix-signed receiver for Resend's email events (routers/webhooks.py), and calling it
//     from a browser could only ever produce a 401.
// The delivery panel says so, rather than rendering an empty "0 failures" table that would
// read as "everything is being delivered" when nothing is being observed at all.

// The events a subscriber can expect, grouped as the lifecycle emits them. Documentation of
// the contract, so it is static by definition.
const EVENT_CATALOGUE = [
  {
    group: "Sessions",
    events: [
      ["session.started", "A broadcast went live."],
      ["session.paused", "The stage was paused mid-broadcast."],
      ["session.ended", "The broadcast finished; recording follow-ups may still arrive."],
    ],
  },
  {
    group: "Media",
    events: [
      ["recording.ready", "A capture finished processing and is playable."],
      ["recording.failed", "A capture could not be produced."],
      ["transcript.ready", "Captions finished generating for an asset."],
    ],
  },
  {
    group: "Audience",
    events: [
      ["registration.created", "Someone registered for an event."],
      ["access_link.revoked", "A share link was withdrawn."],
    ],
  },
];

const SIGNATURE_SAMPLE = `POST /your-endpoint HTTP/1.1
Content-Type: application/json
X-Zoiko-Event: session.started
X-Zoiko-Delivery: 4f1c2b90-...
X-Zoiko-Signature: t=1750000000,v1=<hex>

{
  "event": "session.started",
  "sent_at": "2026-08-04T10:00:00Z",
  "data": { "session_id": "...", "event_id": "..." }
}`;

const VERIFY_SAMPLE = `// Compare a constant-time HMAC over "<t>.<raw body>" using your endpoint secret.
// Reject anything older than five minutes: a valid signature on a replayed
// body is still a replay.
const [t, v1] = header.split(",").map((p) => p.split("=")[1]);
const expected = crypto
  .createHmac("sha256", secret)
  .update(\`\${t}.\${rawBody}\`)
  .digest("hex");

if (!crypto.timingSafeEqual(Buffer.from(v1), Buffer.from(expected))) return 401;
if (Date.now() / 1000 - Number(t) > 300) return 401;`;

const hostOf = (url) => {
  try {
    return new URL(url).host;
  } catch {
    return url;
  }
};

export default function Webhooks() {
  const { data, loading, error, reload } = useApi(() =>
    api.get("/organization/developer").then((r) => r.data)
  );
  const urls = useMemo(() => data?.webhook_urls || [], [data]);

  const copy = async (value) => {
    try {
      await navigator.clipboard.writeText(value);
      notify.success("Endpoint URL copied");
    } catch {
      notify.error("Clipboard is unavailable in this browser");
    }
  };

  return (
    <div className="space-y-6">
      <OrganizationPageHeader
        title="Webhooks"
        subtitle="Endpoints this organization receives platform events on, and how to verify them."
        actions={
          <>
            <ConsoleButton variant="secondary" leftIcon={FiRefreshCw} onClick={reload} disabled={loading}>
              Refresh
            </ConsoleButton>
            <ConsoleButton href="/organization/support" leftIcon={FiLifeBuoy}>
              Register an endpoint
            </ConsoleButton>
          </>
        }
      />

      {error && <OrganizationErrorState error={error} onRetry={reload} title="Couldn't load webhook endpoints" />}

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_320px]">
        <div className="space-y-4">
          <Panel title="Endpoints" flush>
            {loading ? (
              <ul className={cx("divide-y", CONSOLE.divideY)} aria-hidden="true">
                {[0, 1].map((i) => (
                  <li key={i} className="px-5 py-4">
                    <div className="zk-skeleton h-4 w-64 rounded bg-slate-200 dark:bg-white/[0.07]" />
                    <div className="zk-skeleton mt-2 h-3 w-32 rounded bg-slate-200 dark:bg-white/[0.07]" />
                  </li>
                ))}
              </ul>
            ) : urls.length === 0 ? (
              <OrganizationEmptyState
                icon={FiLink2}
                title="No endpoints registered"
                description="Nothing is subscribed to this organization's events yet. Registering an endpoint is a platform-admin operation — open a request and include the URL and which events you need."
                action={
                  <ConsoleButton href="/organization/support" size="sm" leftIcon={FiLifeBuoy}>
                    Register an endpoint
                  </ConsoleButton>
                }
              />
            ) : (
              <ul className={cx("divide-y", CONSOLE.divideY)}>
                {urls.map((url) => (
                  <li key={url} className="flex flex-wrap items-center gap-3 px-5 py-3.5">
                    <span
                      className="grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-blue-100 text-blue-600 dark:bg-blue-500/15 dark:text-blue-400"
                      aria-hidden="true"
                    >
                      <FiLink2 className="text-[15px]" />
                    </span>
                    <div className="min-w-0 flex-1">
                      <p className={cx("truncate text-[13px] font-semibold", CONSOLE.heading)}>{hostOf(url)}</p>
                      <p className={cx("truncate text-[11px]", type.mono, CONSOLE.faint)} title={url}>
                        {url}
                      </p>
                    </div>
                    {/* Not a health verdict — nothing observes deliveries. It says the
                        endpoint is registered, which is the only fact available. */}
                    <Badge tone="info" dot>
                      Registered
                    </Badge>
                    <button
                      type="button"
                      onClick={() => copy(url)}
                      aria-label={`Copy ${hostOf(url)}`}
                      className={cx("shrink-0 rounded p-1.5", CONSOLE.faint, "hover:text-slate-900 dark:hover:text-white", focusRing)}
                    >
                      <FiCopy className="text-[14px]" />
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </Panel>

          <Panel
            eyebrow="Contract"
            title="What a delivery looks like"
            description="Every request carries the event name, a delivery id for idempotency, and a signed timestamp."
          >
            <CodeBlock filename="delivery.http" code={SIGNATURE_SAMPLE} />
            <h3 className={cx("mt-5 text-[13px] font-semibold", CONSOLE.heading)}>Verifying the signature</h3>
            <p className={cx("mt-1 text-[13px] leading-[20px]", CONSOLE.muted)}>
              Reject anything you cannot verify, and treat a repeated{" "}
              <code className={cx(type.mono, "text-[12px]")}>X-Zoiko-Delivery</code> as the same
              event — retries are expected, duplicates are not failures.
            </p>
            <div className="mt-3">
              <CodeBlock filename="verify.js" code={VERIFY_SAMPLE} />
            </div>
          </Panel>

          <Panel eyebrow="Reference" title="Event catalogue" flush>
            <div className={cx("divide-y", CONSOLE.divideY)}>
              {EVENT_CATALOGUE.map(({ group, events }) => (
                <div key={group} className="px-5 py-4">
                  <p className={cx("text-[10px] font-semibold uppercase tracking-[0.14em]", CONSOLE.faint)}>
                    {group}
                  </p>
                  <dl className="mt-2.5 space-y-2">
                    {events.map(([name, desc]) => (
                      <div key={name} className="flex flex-col gap-0.5 sm:flex-row sm:items-baseline sm:gap-3">
                        <dt className={cx("shrink-0 text-[12px] font-semibold sm:w-52", type.mono, CONSOLE.link)}>
                          {name}
                        </dt>
                        <dd className={cx("text-[13px]", CONSOLE.muted)}>{desc}</dd>
                      </div>
                    ))}
                  </dl>
                </div>
              ))}
            </div>
          </Panel>
        </div>

        <div className="space-y-4">
          <Panel title="Configuration">
            <StatRow label="Endpoints registered" value={loading ? null : urls.length} reason="Loading" />
            <StatRow
              label="Subscribed event types"
              value={EVENT_CATALOGUE.reduce((n, g) => n + g.events.length, 0)}
            />
            <StatRow
              label="Deliveries (24h)"
              value={null}
              reason="Delivery outcomes are not recorded in this stack"
            />
            <StatRow
              label="Failure streaks"
              value={null}
              reason="Delivery outcomes are not recorded in this stack"
            />
            <StatRow
              label="Median delivery latency"
              value={null}
              reason="Delivery outcomes are not recorded in this stack"
            />
          </Panel>

          <Panel title="Delivery observability">
            <div className="flex gap-2.5">
              <FiAlertTriangle
                className="mt-0.5 shrink-0 text-[15px] text-amber-500"
                aria-hidden="true"
              />
              <p className={cx("text-[13px] leading-[20px]", CONSOLE.muted)}>
                Attempts, response codes and retry history are not persisted by this platform, so
                this console cannot show a delivery log. Instrument your own endpoint if you need
                one — and treat a missing event as “unknown”, not “not sent”.
              </p>
            </div>
            <p className={cx("mt-3 border-t pt-3 text-[12px]", CONSOLE.divider, CONSOLE.faint)}>
              There is no redelivery control here for the same reason: with no attempt record,
              the platform has nothing to replay.
            </p>
          </Panel>

          <Panel title="Endpoint requirements">
            <ul className={cx("space-y-2.5 text-[13px]", CONSOLE.body)}>
              {[
                "Answer 2xx within 5 seconds; queue the work, don't do it inline.",
                "Verify the signature before parsing the body.",
                "Be idempotent on delivery id — retries will repeat.",
                "Serve HTTPS with a publicly valid certificate.",
              ].map((r) => (
                <li key={r} className="flex gap-2.5">
                  <FiCheckCircle className={cx("mt-0.5 shrink-0 text-[14px]", CONSOLE.faint)} aria-hidden="true" />
                  <span>{r}</span>
                </li>
              ))}
            </ul>
            <p className={cx("mt-4 border-t pt-3 text-[12px]", CONSOLE.divider, CONSOLE.faint)}>
              Building an integration?{" "}
              <Link to="/organization/developers" className={cx("font-semibold", CONSOLE.link)}>
                Developer Platform
              </Link>
              .
            </p>
          </Panel>
        </div>
      </div>

    </div>
  );
}
