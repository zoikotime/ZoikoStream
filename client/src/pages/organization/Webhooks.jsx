import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  FiLink2, FiCopy, FiRefreshCw, FiAlertTriangle, FiCheckCircle, FiPlus,
  FiEdit2, FiTrash2, FiEye, FiActivity,
} from "react-icons/fi";
import api, { errMsg } from "../../api";
import useApi from "../../hooks/useApi";
import { CONSOLE, cx, focusRing, type } from "../../ui/tokens";
import { ConsoleButton } from "../../ui/Button";
import Badge from "../../ui/Badge";
import Modal from "../../ui/Modal";
import ConfirmDialog from "../../ui/ConfirmDialog";
import CodeBlock from "../../ui/CodeBlock";
import { Input, Label, Checkbox, Switch } from "../../ui/forms";
import { notify } from "../../ui/Toast";
import DataTable from "../../components/admin/DataTable";
import MetricCard from "../../components/admin/MetricCard";
import Panel from "../../components/admin/Panel";
import StatRow from "../../components/admin/StatRow";
import { timeAgo } from "../../components/admin/format";
import OrganizationPageHeader from "../../components/organization/OrganizationPageHeader";
import OrganizationEmptyState from "../../components/organization/OrganizationEmptyState";
import OrganizationErrorState from "../../components/organization/OrganizationErrorState";

// Webhooks — the endpoints this organization sends platform events to.
//
// Org-admin scoped create/edit/delete (POST/PATCH/DELETE /organization/developer/webhooks),
// backed by a real dispatcher (services/webhooks.py): every subscribed event is signed,
// sent, and retried with exponential backoff, and every attempt is logged to
// WebhookDelivery — so unlike this page's old read-only build, the delivery log and
// failure history below are real, not a "not recorded" disclosure.

// The events a subscriber can expect, grouped as the lifecycle emits them. Also the exact
// menu the create/edit modal's event checklist is built from — one source of truth for the
// documented contract and the subscription options.
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
const ALL_EVENTS = EVENT_CATALOGUE.flatMap((g) => g.events.map(([name]) => name));

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

const DELIVERY_TONE = { delivered: "success", failed: "danger", pending: "warning" };

// Create or edit one endpoint. The event checklist doubles as subscription documentation —
// same list Webhooks.jsx has always shown, now the thing actually driving what gets sent.
function WebhookModal({ endpoint, open, onClose, onSaved }) {
  const editing = !!endpoint;
  const [url, setUrl] = useState("");
  const [label, setLabel] = useState("");
  const [events, setEvents] = useState([]);
  const [enabled, setEnabled] = useState(true);
  const [saving, setSaving] = useState(false);
  const [wasOpen, setWasOpen] = useState(open);
  if (open !== wasOpen) {
    setWasOpen(open);
    if (open) {
      setUrl(endpoint?.url || "");
      setLabel(endpoint?.label || "");
      setEvents(endpoint?.events || []);
      setEnabled(endpoint?.enabled ?? true);
    }
  }

  const toggleEvent = (name) =>
    setEvents((cur) => (cur.includes(name) ? cur.filter((e) => e !== name) : [...cur, name]));

  const save = async () => {
    setSaving(true);
    try {
      if (editing) {
        await api.patch(`/organization/developer/webhooks/${endpoint.id}`, {
          url: url.trim(), label: label.trim() || null, events, enabled,
        });
        notify.success("Endpoint updated");
        onSaved(null);
      } else {
        const { data } = await api.post("/organization/developer/webhooks", {
          url: url.trim(), label: label.trim() || null, events,
        });
        notify.success("Endpoint created");
        onSaved(data);
      }
      onClose();
    } catch (e) {
      notify.error(errMsg(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={editing ? "Edit endpoint" : "New endpoint"}
      size="lg"
      footer={
        <>
          <ConsoleButton variant="secondary" onClick={onClose} disabled={saving}>Cancel</ConsoleButton>
          <ConsoleButton
            disabled={!url.trim() || events.length === 0 || saving}
            loading={saving}
            onClick={save}
          >
            {editing ? "Save changes" : "Create endpoint"}
          </ConsoleButton>
        </>
      }
    >
      <div className="space-y-4">
        <div>
          <Label variant="console" htmlFor="wh-url">Endpoint URL</Label>
          <Input
            variant="console" id="wh-url" value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder="https://example.com/webhooks/zoiko"
            maxLength={2000}
          />
        </div>
        <div>
          <Label variant="console" htmlFor="wh-label">Label (optional)</Label>
          <Input
            variant="console" id="wh-label" value={label}
            onChange={(e) => setLabel(e.target.value)}
            placeholder="Production ingest"
            maxLength={120}
          />
        </div>
        {editing && (
          <Switch
            checked={enabled}
            onChange={setEnabled}
            accent="violet"
            label={
              <span className={cx("text-[13px] font-medium", CONSOLE.body)}>
                {enabled ? "Enabled — receiving deliveries" : "Disabled — deliveries paused"}
              </span>
            }
          />
        )}
        <div>
          <Label variant="console">Events</Label>
          <div className={cx("mt-1 space-y-3 rounded-lg border p-3", CONSOLE.divider)}>
            {EVENT_CATALOGUE.map(({ group, events: groupEvents }) => (
              <div key={group}>
                <p className={cx("text-[10px] font-semibold uppercase tracking-[0.14em]", CONSOLE.faint)}>
                  {group}
                </p>
                <div className="mt-1.5 space-y-1.5">
                  {groupEvents.map(([name, desc]) => (
                    <label key={name} className="flex cursor-pointer items-start gap-2.5">
                      <Checkbox
                        checked={events.includes(name)}
                        onChange={() => toggleEvent(name)}
                        className="mt-0.5 accent-violet-600"
                      />
                      <span className="min-w-0">
                        <span className={cx("block text-[12px] font-semibold", type.mono, CONSOLE.link)}>
                          {name}
                        </span>
                        <span className={cx("block text-[11px]", CONSOLE.faint)}>{desc}</span>
                      </span>
                    </label>
                  ))}
                </div>
              </div>
            ))}
          </div>
          {events.length === 0 && (
            <p className="mt-1.5 text-xs font-medium text-rose-600 dark:text-rose-400">
              Select at least one event.
            </p>
          )}
        </div>
      </div>
    </Modal>
  );
}

// The signing secret, shown either once at creation (`created`) or re-viewed on demand
// (`revealing`) — never reveal-once like an API key, because the subscriber's own endpoint
// needs it indefinitely to keep verifying HMACs (models/webhook.py explains why).
function SecretDialog({ reveal, onClose }) {
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(reveal.secret);
      notify.success("Signing secret copied");
    } catch {
      notify.error("Couldn't copy — select the secret and copy it manually");
    }
  };
  return (
    <Modal
      open={!!reveal}
      onClose={onClose}
      title="Signing secret"
      size="lg"
      footer={
        <>
          <ConsoleButton variant="secondary" onClick={onClose}>Done</ConsoleButton>
          <ConsoleButton leftIcon={FiCopy} onClick={copy}>Copy secret</ConsoleButton>
        </>
      }
    >
      {reveal && (
        <>
          <p className={cx("text-[13px] leading-[20px]", CONSOLE.muted)}>
            Use this to verify the <code className={cx(type.mono, "text-[12px]")}>X-Zoiko-Signature</code>{" "}
            header on every delivery to{" "}
            <strong className="font-semibold text-slate-800 dark:text-slate-100">{reveal.label}</strong>.
            Unlike an API key, this stays viewable for as long as the endpoint exists — your
            receiver needs it to keep computing HMACs, not just to authenticate once.
          </p>
          <code
            className={cx(
              "mt-4 block break-all rounded-lg px-3 py-2 text-[12px]",
              type.mono, CONSOLE.inset, CONSOLE.body
            )}
          >
            {reveal.secret}
          </code>
        </>
      )}
    </Modal>
  );
}

// One endpoint's recent attempts. Fetched fresh per open (keyed by endpoint id in the
// parent), same on-demand posture as LiveInputs.jsx's key-reveal sheet.
function DeliveriesModal({ endpoint, onClose }) {
  const { data, loading, error, reload } = useApi(() =>
    api.get(`/organization/developer/webhooks/${endpoint.id}/deliveries`).then((r) => r.data)
  );
  const deliveries = data || [];

  return (
    <Modal
      open
      onClose={onClose}
      title="Delivery log"
      size="xl"
      footer={<ConsoleButton variant="secondary" onClick={onClose}>Close</ConsoleButton>}
    >
      <div className="flex items-start justify-between gap-3">
        <p className={cx("text-[13px] leading-[20px]", CONSOLE.muted)}>
          Most recent attempts to{" "}
          <span className={cx(type.mono, "text-[12px]", CONSOLE.body)}>{endpoint.url}</span>.
          Retries use exponential backoff, up to 6 attempts before an event is marked failed.
        </p>
        <ConsoleButton variant="secondary" size="sm" leftIcon={FiRefreshCw} onClick={reload} loading={loading}>
          Refresh
        </ConsoleButton>
      </div>

      {error ? (
        <div className="mt-3">
          <OrganizationErrorState error={error} onRetry={reload} title="Couldn't load deliveries" />
        </div>
      ) : loading ? (
        <ul className="mt-3 space-y-2" aria-hidden="true">
          {[0, 1, 2].map((i) => (
            <li key={i} className="zk-skeleton h-11 rounded-lg bg-slate-200 dark:bg-white/[0.07]" />
          ))}
        </ul>
      ) : deliveries.length === 0 ? (
        <div className="mt-3">
          <OrganizationEmptyState
            icon={FiActivity}
            title="No deliveries yet"
            description="Nothing has been sent to this endpoint. A delivery queues the moment a subscribed event happens."
          />
        </div>
      ) : (
        <ul className={cx("mt-3 divide-y", CONSOLE.divideY)}>
          {deliveries.map((d) => (
            <li key={d.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 py-2.5">
              <Badge tone={DELIVERY_TONE[d.status] || "neutral"} dot>{d.status}</Badge>
              <span className={cx("text-[12px] font-semibold", type.mono, CONSOLE.link)}>{d.event_type}</span>
              <span className={cx("text-[11px]", CONSOLE.faint)}>
                attempt {d.attempt_count}{d.last_response_code != null ? ` · HTTP ${d.last_response_code}` : ""}
              </span>
              {d.last_error && (
                <span
                  className="max-w-[16rem] truncate text-[11px] text-rose-600 dark:text-rose-400"
                  title={d.last_error}
                >
                  {d.last_error}
                </span>
              )}
              <span className={cx("ml-auto shrink-0 text-[11px]", CONSOLE.faint)}>
                {timeAgo(d.delivered_at || d.created_at)}
              </span>
            </li>
          ))}
        </ul>
      )}
    </Modal>
  );
}

export default function Webhooks() {
  const { data, loading, error, reload } = useApi(() =>
    api.get("/organization/developer").then((r) => r.data)
  );
  const endpoints = useMemo(() => data?.webhooks || [], [data]);

  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState(null);
  const [deleting, setDeleting] = useState(null);
  const [reveal, setReveal] = useState(null);
  const [revealingId, setRevealingId] = useState(null);
  const [viewingDeliveries, setViewingDeliveries] = useState(null);

  const summary = useMemo(() => ({
    total: endpoints.length,
    enabled: endpoints.filter((e) => e.enabled).length,
    disabled: endpoints.filter((e) => !e.enabled).length,
  }), [endpoints]);

  const copy = async (value) => {
    try {
      await navigator.clipboard.writeText(value);
      notify.success("Endpoint URL copied");
    } catch {
      notify.error("Clipboard is unavailable in this browser");
    }
  };

  const viewSecret = async (ep) => {
    setRevealingId(ep.id);
    try {
      const { data: d } = await api.get(`/organization/developer/webhooks/${ep.id}/secret`);
      setReveal({ label: ep.label || hostOf(ep.url), secret: d.secret });
    } catch (e) {
      notify.error(errMsg(e));
    } finally {
      setRevealingId(null);
    }
  };

  const deleteEndpoint = async () => {
    try {
      await api.delete(`/organization/developer/webhooks/${deleting.id}`);
      notify.success(`${deleting.label || hostOf(deleting.url)} removed`);
      setDeleting(null);
      reload();
    } catch (e) {
      notify.error(errMsg(e));
    }
  };

  const columns = [
    {
      key: "url",
      header: "Endpoint",
      sortable: true,
      sortValue: (e) => e.label || hostOf(e.url),
      render: (e) => (
        <div className="flex min-w-0 items-center gap-3">
          <span
            className="grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-blue-100 text-blue-600 dark:bg-blue-500/15 dark:text-blue-400"
            aria-hidden="true"
          >
            <FiLink2 className="text-[15px]" />
          </span>
          <div className="min-w-0 flex-1">
            <p className={cx("truncate text-[13px] font-semibold", CONSOLE.heading)}>
              {e.label || hostOf(e.url)}
            </p>
            <p className={cx("truncate text-[11px]", type.mono, CONSOLE.faint)} title={e.url}>
              {e.url}
            </p>
          </div>
        </div>
      ),
    },
    {
      key: "events",
      header: "Subscribed events",
      render: (e) => (
        <span className={cx("text-[13px]", CONSOLE.body)}>
          {(e.events || []).length} of {ALL_EVENTS.length}
        </span>
      ),
    },
    {
      key: "created_at",
      header: "Registered",
      sortable: true,
      sortValue: (e) => (e.created_at ? new Date(e.created_at).getTime() : 0),
      render: (e) => (
        <span className={cx("text-[13px]", CONSOLE.body)}>
          {e.created_at ? timeAgo(e.created_at) : "—"}
        </span>
      ),
    },
    {
      key: "state",
      header: "Status",
      align: "right",
      render: (e) => <Badge tone={e.enabled ? "success" : "neutral"} dot>{e.enabled ? "Enabled" : "Disabled"}</Badge>,
    },
  ];

  return (
    <div className="space-y-6">
      <OrganizationPageHeader
        title="Webhooks"
        subtitle="Endpoints this organization sends platform events to, signed and retried automatically."
        actions={
          <>
            <ConsoleButton variant="secondary" leftIcon={FiRefreshCw} onClick={reload} loading={loading}>
              Refresh
            </ConsoleButton>
            <ConsoleButton leftIcon={FiPlus} onClick={() => setCreating(true)}>
              New endpoint
            </ConsoleButton>
          </>
        }
      />

      {error && <OrganizationErrorState error={error} onRetry={reload} title="Couldn't load webhook endpoints" />}

      <div className="grid gap-4 sm:grid-cols-3">
        <MetricCard icon={FiLink2} accent="blue" label="Endpoints registered" value={summary.total} note="All environments" loading={loading} />
        <MetricCard icon={FiCheckCircle} accent="green" label="Enabled" value={summary.enabled} note="Receiving deliveries" loading={loading} />
        <MetricCard icon={FiAlertTriangle} accent="amber" label="Disabled" value={summary.disabled} note={summary.disabled ? "Paused, not deleted" : "None paused"} loading={loading} />
      </div>

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_320px]">
        <div className="space-y-4">
          <Panel title="Endpoints" flush>
            <DataTable
              columns={columns}
              rows={endpoints}
              rowKey={(e) => e.id}
              loading={loading}
              searchable
              searchShortcut
              searchKeys={["url", "label"]}
              searchPlaceholder="Search by URL or label…"
              initialSort={{ key: "url", dir: "asc" }}
              pageSize={10}
              minWidth={720}
              rowActions={(e) => (
                <>
                  <button
                    type="button"
                    onClick={() => setViewingDeliveries(e)}
                    aria-label={`View deliveries for ${e.label || hostOf(e.url)}`}
                    title="Delivery log"
                    className={cx(
                      "rounded-md p-1.5 transition-colors duration-150 motion-reduce:transition-none",
                      CONSOLE.faint,
                      "hover:bg-slate-100 hover:text-slate-900 dark:hover:bg-white/10 dark:hover:text-white",
                      focusRing
                    )}
                  >
                    <FiActivity className="text-[14px]" />
                  </button>
                  <button
                    type="button"
                    onClick={() => copy(e.url)}
                    aria-label={`Copy the URL of ${e.label || hostOf(e.url)}`}
                    title="Copy URL"
                    className={cx(
                      "rounded-md p-1.5 transition-colors duration-150 motion-reduce:transition-none",
                      CONSOLE.faint,
                      "hover:bg-slate-100 hover:text-slate-900 dark:hover:bg-white/10 dark:hover:text-white",
                      focusRing
                    )}
                  >
                    <FiCopy className="text-[14px]" />
                  </button>
                  <button
                    type="button"
                    onClick={() => viewSecret(e)}
                    disabled={revealingId === e.id}
                    aria-label={`View signing secret for ${e.label || hostOf(e.url)}`}
                    title="View signing secret"
                    className={cx(
                      "rounded-md p-1.5 transition-colors duration-150 motion-reduce:transition-none disabled:opacity-50",
                      CONSOLE.faint,
                      "hover:bg-slate-100 hover:text-slate-900 dark:hover:bg-white/10 dark:hover:text-white",
                      focusRing
                    )}
                  >
                    <FiEye className="text-[14px]" />
                  </button>
                  <button
                    type="button"
                    onClick={() => setEditing(e)}
                    aria-label={`Edit ${e.label || hostOf(e.url)}`}
                    title="Edit"
                    className={cx(
                      "rounded-md p-1.5 transition-colors duration-150 motion-reduce:transition-none",
                      CONSOLE.faint,
                      "hover:bg-slate-100 hover:text-slate-900 dark:hover:bg-white/10 dark:hover:text-white",
                      focusRing
                    )}
                  >
                    <FiEdit2 className="text-[14px]" />
                  </button>
                  <button
                    type="button"
                    onClick={() => setDeleting(e)}
                    aria-label={`Delete ${e.label || hostOf(e.url)}`}
                    title="Delete"
                    className={cx(
                      "rounded-md p-1.5 transition-colors duration-150 motion-reduce:transition-none",
                      CONSOLE.faint,
                      "hover:bg-rose-50 hover:text-rose-600 dark:hover:bg-rose-500/10 dark:hover:text-rose-400",
                      focusRing
                    )}
                  >
                    <FiTrash2 className="text-[14px]" />
                  </button>
                </>
              )}
              empty={{
                icon: FiLink2,
                title: "No endpoints registered",
                description: "Nothing is subscribed to this organization's events yet. Create an endpoint and choose which events it should receive.",
                action: (
                  <ConsoleButton size="sm" leftIcon={FiPlus} onClick={() => setCreating(true)}>
                    New endpoint
                  </ConsoleButton>
                ),
              }}
            />
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
            <StatRow label="Endpoints registered" value={loading ? null : summary.total} reason="Loading" separated />
            <StatRow label="Enabled" value={loading ? null : summary.enabled} reason="Loading" dot="success" separated />
            <StatRow
              label="Disabled"
              value={loading ? null : summary.disabled}
              reason="Loading"
              dot={summary.disabled ? "warning" : "neutral"}
              tone={summary.disabled ? "text-amber-600 dark:text-amber-400" : undefined}
              separated
            />
            <StatRow label="Subscribable event types" value={ALL_EVENTS.length} separated />
          </Panel>

          <Panel title="Delivery observability">
            <div className="flex gap-2.5">
              <FiCheckCircle
                className="mt-0.5 shrink-0 text-[15px] text-emerald-500"
                aria-hidden="true"
              />
              <p className={cx("text-[13px] leading-[20px]", CONSOLE.muted)}>
                Every attempt is logged and retried with exponential backoff (up to 6 tries)
                before an event is marked failed. Open an endpoint's delivery log for its
                per-attempt history.
              </p>
            </div>
            <p className={cx("mt-3 border-t pt-3 text-[12px]", CONSOLE.divider, CONSOLE.faint)}>
              There is no manual redelivery yet — a failed event stays in its log, but
              re-queuing it isn't wired up.
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
              Need help with an integration?{" "}
              <Link to="/organization/support" className={cx("font-semibold", CONSOLE.link)}>
                Open a request
              </Link>
              .
            </p>
          </Panel>
        </div>
      </div>

      <WebhookModal
        endpoint={null}
        open={creating}
        onClose={() => setCreating(false)}
        onSaved={(created) => { if (created) setReveal({ label: created.label || hostOf(created.url), secret: created.secret }); reload(); }}
      />
      <WebhookModal
        endpoint={editing}
        open={!!editing}
        onClose={() => setEditing(null)}
        onSaved={() => reload()}
      />
      <SecretDialog reveal={reveal} onClose={() => setReveal(null)} />
      {viewingDeliveries && (
        <DeliveriesModal
          key={viewingDeliveries.id}
          endpoint={viewingDeliveries}
          onClose={() => setViewingDeliveries(null)}
        />
      )}
      <ConfirmDialog
        open={!!deleting}
        onClose={() => setDeleting(null)}
        onConfirm={deleteEndpoint}
        title="Delete this endpoint?"
        confirmLabel="Delete"
        body={
          <>
            <strong className="font-semibold text-slate-800 dark:text-slate-100">
              {deleting?.label || (deleting ? hostOf(deleting.url) : "")}
            </strong>{" "}
            stops receiving deliveries immediately, and its delivery log is removed with it.
          </>
        }
      />
    </div>
  );
}
