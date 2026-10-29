import { useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import {
  FiSearch, FiPlay, FiMonitor, FiShield, FiAlignLeft,
  FiTrendingUp, FiCheckSquare, FiChevronRight, FiKey, FiLink, FiLayers, FiActivity, FiCode,
  FiCalendar, FiLock, FiCheck, FiArrowRight,
} from "react-icons/fi";
import api, { errMsg } from "../../api";
import useApi from "../../hooks/useApi";
import useInterval from "../../hooks/useInterval";
import { keyInventory } from "../../data/keyInventory";
import { ACCENT, CONSOLE, STAGE_COLOR, cx, focusRing, type } from "../../ui/tokens";
import { ConsoleButton } from "../../ui/Button";
import { Input, Label, Select, Textarea } from "../../ui/forms";
import Modal from "../../ui/Modal";
import { notify } from "../../ui/Toast";
import HealthDot from "../../components/admin/HealthDot";
import StatRow from "../../components/admin/StatRow";
import OrganizationPageHeader from "../../components/organization/OrganizationPageHeader";
import {
  Banner, IconChip, KpiRow, KpiTile, Panel, PanelLink, Tag,
} from "../../components/organization/console/ConsoleUI";

// Developer Platform — the org console's build surface: what ZoikoStream is, the docs
// entry points, and whether the platform is currently in a state worth building against.
//
// The catalogue below is DOCUMENTATION, so it is static by definition. The two panels at
// the foot are posture, so they read from /organization/overview — the same payload the
// Overview page already polls. Figures with no producer in this stack render "—" with the
// reason (the convention DeveloperOps/SecuritySupport established), never a zero that
// would read as a clean bill of health.
const REFRESH_MS = 60_000;

// Published API version and deprecation notice — release facts, not telemetry.
const API_VERSION = "2026-06-01";
const LATEST_DEPRECATION = "Node SDK v2 · Oct 2026";

// The lifecycle, left to right, as a broadcast travels it. Doubles as the docs filter:
// picking a stage narrows the catalogue to the guides that touch it.
const STAGES = [
  ["contribute", "Contribute"],
  ["ingest", "Ingest"],
  ["produce", "Produce"],
  ["secure", "Secure"],
  ["deliver", "Deliver"],
  ["understand", "Understand"],
  ["preserve", "Preserve"],
  ["platform", "Platform"],
];

// Tinted icon tiles. Blue/violet/amber come from the shared ACCENT map; green has no ACCENT
// entry (and `emerald` is remapped to violet in index.css), so it is spelled out here.
const GREEN_TILE = "bg-green-100 text-green-600 dark:bg-green-500/15 dark:text-green-400";

// Each quickstart links to the console surface it is about, so the card is a real
// destination rather than a placeholder for docs that don't exist yet.
const QUICKSTARTS = [
  {
    icon: FiMonitor, tile: ACCENT.blue.chip, to: "/organization/live-inputs",
    title: "Send your first stream",
    desc: "Create a live input, connect an encoder over RTMP or SRT, and confirm ingest health.",
    crumb: "Contribute → Ingest", stages: ["contribute", "ingest"],
  },
  {
    icon: FiShield, tile: ACCENT.violet.chip, to: "/organization/audience/access",
    title: "Secure playback with signed tokens",
    desc: "Issue short-lived access grants and verify sessions before serving protected media.",
    crumb: "Secure", stages: ["secure"],
  },
  {
    icon: FiAlignLeft, tile: GREEN_TILE, to: "/organization/recordings",
    title: "Publish a replay",
    desc: "Turn a finished session into a governed on-demand asset with captions intact.",
    crumb: "Preserve", stages: ["preserve"],
  },
  {
    icon: FiTrendingUp, tile: ACCENT.blue.chip, to: "/organization/analytics",
    title: "Read analytics via the API",
    desc: "Pull QoE, concurrency, and usage metering for a session, event, or workspace.",
    crumb: "Understand", stages: ["understand"],
  },
  {
    icon: FiCheckSquare, tile: ACCENT.violet.chip, to: "/organization/events",
    title: "Integrate a managed Live Event",
    desc: "Understand the request, readiness, and status contract for operator-run events.",
    crumb: "Deliver", stages: ["deliver"],
  },
];

// What the platform actually enforces today — each line is a behaviour of this codebase
// (crud.admin key minting, services/webhooks signing, the audit ledger), not a roadmap item.
const SAFEGUARDS = [
  ["One-time secret reveal", "Only a hash of each API key is stored; the key is shown once"],
  ["Expiry and revocation", "Keys can expire and be revoked; expiring keys are flagged"],
  ["Auditable changes", "Creating or revoking a key is recorded in Security & audit"],
  ["Signed webhooks", "Deliveries carry a timestamped HMAC-SHA256 X-Zoiko-Signature"],
];

const STEP_TONE = { done: "green", todo: "amber", unavailable: "slate", error: "rose" };

function ReadinessStep({ n, icon, title, detail, state, label, to }) {
  return (
    <li className="flex items-center gap-3 py-3">
      <span className={cx("w-6 shrink-0 text-[12px] font-semibold tabular-nums", CONSOLE.faint)}>{String(n).padStart(2, "0")}</span>
      <IconChip icon={icon} tone={state === "done" ? "green" : state === "todo" ? "violet" : "slate"} size="sm" />
      <div className="min-w-0 flex-1">
        <p className={cx("text-[13px] font-semibold", CONSOLE.heading)}>{title}</p>
        <p className={cx("text-[12px]", CONSOLE.faint)}>{detail}</p>
      </div>
      {to && state === "todo" ? (
        <Link to={to} className={cx("shrink-0 rounded", focusRing)} aria-label={`${title}: ${label}`}>
          <Tag plain tone="amber">{label}</Tag>
        </Link>
      ) : (
        <Tag plain tone={STEP_TONE[state]}>{label}</Tag>
      )}
    </li>
  );
}

function FlowBox({ icon: Icon, title, detail }) {
  return (
    <div className={cx("flex min-w-0 flex-1 flex-col items-center rounded-xl border px-3 py-3 text-center", CONSOLE.divider)}>
      <Icon aria-hidden="true" className="text-[18px] text-violet-600 dark:text-violet-300" />
      <p className={cx("mt-1.5 text-[12.5px] font-semibold", CONSOLE.heading)}>{title}</p>
      <p className={cx("text-[11px]", CONSOLE.faint)}>{detail}</p>
    </div>
  );
}

function FlowArrow({ label }) {
  return (
    <div className={cx("flex shrink-0 flex-col items-center px-1 text-[10px] uppercase tracking-wider", CONSOLE.faint)}>
      <span>{label}</span>
      <FiArrowRight aria-hidden="true" className="text-[14px]" />
    </div>
  );
}

function FootPanel({ eyebrow, children }) {
  return (
    <section className={cx(CONSOLE.panel, "px-5 py-4")}>
      <p className={cx("mb-2 text-[10px] font-semibold uppercase tracking-[0.14em]", CONSOLE.faint)}>
        {eyebrow}
      </p>
      {children}
    </section>
  );
}

// Request and delivery visibility (ORG-08 "API requests & webhook deliveries"). Webhook
// deliveries are recorded per endpoint, so that tab is real; per-key request telemetry is not
// collected anywhere, and that tab says so rather than drawing a chart of nothing.
const ACTIVITY_TABS = [
  ["requests", "Request activity"],
  ["deliveries", "Webhook deliveries"],
  ["errors", "Errors"],
];
const failedDelivery = (d) => d.status === "failed" || Boolean(d.last_error) || (d.last_response_code ?? 0) >= 400;

function useDeliveries(hooks) {
  // Up to five endpoints' recent deliveries, newest first; only metadata, never payloads.
  const deliveries = useApi(() =>
    Promise.all(
      hooks.slice(0, 5).map((h) =>
        api.get(`/organization/developer/webhooks/${h.id}/deliveries`).then((r) => (r.data || []).map((d) => ({ ...d, endpoint: h.label || h.url })))
      )
    ).then((lists) => lists.flat().sort((a, b) => (b.created_at || "").localeCompare(a.created_at || "")).slice(0, 20))
  );
  return deliveries;
}

function IntegrationActivity({ hooks, failed }) {
  const [tab, setTab] = useState("requests");
  return (
    <Panel eyebrow="Operational visibility" title="API requests & webhook deliveries" subtitle="Organization-scoped technical activity, where it is recorded">
      <div role="tablist" aria-label="Activity" className={cx("-mt-1 mb-3 flex gap-4 border-b", CONSOLE.divider)}>
        {ACTIVITY_TABS.map(([key, label]) => (
          <button
            key={key}
            type="button"
            role="tab"
            aria-selected={tab === key}
            onClick={() => setTab(key)}
            className={cx(
              "-mb-px border-b-2 pb-2 text-[13px] font-semibold",
              tab === key ? "border-violet-600 text-violet-700 dark:border-violet-400 dark:text-violet-300" : cx("border-transparent", CONSOLE.muted),
              focusRing
            )}
          >
            {label}
          </button>
        ))}
      </div>
      {tab === "requests" ? (
        <div className={cx("flex items-start gap-3 rounded-xl border border-dashed p-4", CONSOLE.divider)}>
          <IconChip icon={FiActivity} tone="violet" size="sm" />
          <div>
            <p className={cx("text-[13px] font-semibold", CONSOLE.heading)}>Request telemetry is not connected</p>
            <p className={cx("text-[12px]", CONSOLE.faint)}>
              Per-key request volume, failures and latency are not collected, so nothing is shown here.
            </p>
          </div>
        </div>
      ) : failed ? (
        <p className={cx("py-4 text-center text-[12.5px]", CONSOLE.faint)}>Developer settings couldn&apos;t be read.</p>
      ) : !hooks ? (
        <p className={cx("py-4 text-center text-[12.5px]", CONSOLE.faint)}>Loading…</p>
      ) : hooks.length === 0 ? (
        <p className={cx("rounded-xl border border-dashed px-4 py-6 text-center text-[12.5px]", CONSOLE.divider, CONSOLE.faint)}>
          No webhook endpoints are registered, so there are no deliveries.
        </p>
      ) : (
        <Deliveries hooks={hooks} errorsOnly={tab === "errors"} />
      )}
      <p className={cx("mt-3 text-[11.5px]", CONSOLE.faint)}>Delivery records are scoped to this organization and never include payloads.</p>
    </Panel>
  );
}

function Deliveries({ hooks, errorsOnly }) {
  const { data, error, loading } = useDeliveries(hooks);
  const rows = (data || []).filter((d) => !errorsOnly || failedDelivery(d));
  if (error) return <p className={cx("py-4 text-center text-[12.5px]", CONSOLE.faint)}>Deliveries couldn&apos;t be read.</p>;
  if (loading && !data) return <p className={cx("py-4 text-center text-[12.5px]", CONSOLE.faint)}>Loading…</p>;
  if (rows.length === 0) {
    return (
      <p className={cx("rounded-xl border border-dashed px-4 py-6 text-center text-[12.5px]", CONSOLE.divider, CONSOLE.faint)}>
        {errorsOnly ? "No failed deliveries." : "No deliveries yet."}
      </p>
    );
  }
  return (
    <ul className={cx("divide-y", CONSOLE.divideY)}>
      {rows.map((d) => (
        <li key={d.id} className="flex items-center gap-3 py-2.5">
          <div className="min-w-0 flex-1">
            <p className={cx("truncate font-mono text-[12.5px]", CONSOLE.heading)}>{d.event_type}</p>
            <p className={cx("truncate text-[11.5px]", CONSOLE.faint)}>
              {d.endpoint} · {new Date(d.created_at).toLocaleString()} · {d.attempt_count} attempt{d.attempt_count === 1 ? "" : "s"}
            </p>
          </div>
          <Tag tone={failedDelivery(d) ? "rose" : d.status === "delivered" ? "green" : "slate"}>
            {d.last_response_code ? `${d.status} · ${d.last_response_code}` : d.status}
          </Tag>
        </li>
      ))}
    </ul>
  );
}


// What the Feedback dialog may claim is decided by the SERVER's answer, not assumed. Today
// POST /organization/feedback validates the message and answers 503
// FEEDBACK_DELIVERY_UNAVAILABLE: nothing stores or forwards console feedback yet. So the dialog
// says the message was NOT sent, keeps the text, and points at Contact support. Only an answer
// of `status: "delivered"` — once delivery exists — shows a "sent" confirmation.
const FEEDBACK_MAX = 4000;

export function FeedbackModal({ open, onClose }) {
  const [category, setCategory] = useState("developer_platform");
  const [message, setMessage] = useState("");
  const [error, setError] = useState(null);
  const [undelivered, setUndelivered] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [sent, setSent] = useState(false);
  // Each submission takes a ticket; closing the dialog voids it (and aborts the request), so
  // an answer that arrives later cannot write into a dialog that was closed or reopened.
  const attempt = useRef(0);
  const inFlight = useRef(null);

  const resetForm = () => {
    setCategory("developer_platform");
    setMessage("");
    setError(null);
    setUndelivered(false);
    setSubmitting(false);
    setSent(false);
  };

  const handleClose = () => {
    attempt.current += 1;
    inFlight.current?.abort();
    inFlight.current = null;
    resetForm();
    onClose();
  };

  const handleSubmit = async (e) => {
    e?.preventDefault();
    if (submitting) return;
    const trimmed = message.trim();
    if (!trimmed) {
      setError("Please enter your feedback before submitting.");
      return;
    }
    if (trimmed.length < 5) {
      setError("Please enter at least 5 characters of feedback.");
      return;
    }
    const ticket = ++attempt.current;
    const controller = new AbortController();
    inFlight.current = controller;
    setError(null);
    setUndelivered(false);
    setSubmitting(true);
    try {
      const { data } = await api.post(
        "/organization/feedback",
        { category, message: trimmed },
        { signal: controller.signal }
      );
      if (ticket !== attempt.current) return;
      if (data?.status === "delivered") {
        setSent(true);
        notify.success("Feedback sent");
      } else {
        // A success status that does not say "delivered" is not a receipt.
        setUndelivered(true);
      }
    } catch (err) {
      if (ticket !== attempt.current) return;
      const detail = err?.response?.data?.detail;
      if (err?.response?.status === 503 && detail?.code === "FEEDBACK_DELIVERY_UNAVAILABLE") setUndelivered(true);
      else setError(errMsg(err));
    } finally {
      if (ticket === attempt.current) {
        setSubmitting(false);
        inFlight.current = null;
      }
    }
  };

  return (
    <Modal
      open={open}
      onClose={handleClose}
      title={sent ? "Feedback sent" : "Developer Platform Feedback"}
      size="md"
      // A form: a stray click outside must not discard it. Escape, Cancel and the close
      // button always close it, and cancel a submission still in flight.
      closeOnBackdrop={false}
      footer={
        sent ? (
          <ConsoleButton variant="secondary" type="button" onClick={handleClose}>
            Done
          </ConsoleButton>
        ) : (
          <div className="flex w-full items-center justify-end gap-2">
            <ConsoleButton variant="secondary" type="button" onClick={handleClose}>
              Cancel
            </ConsoleButton>
            <ConsoleButton
              variant="primary"
              type="button"
              onClick={handleSubmit}
              disabled={submitting}
            >
              {submitting ? "Submitting..." : "Submit Feedback"}
            </ConsoleButton>
          </div>
        )
      }
    >
      {sent ? (
        <div className="space-y-3 py-2 text-center sm:text-left">
          <div className="flex items-center gap-2 text-emerald-600 dark:text-emerald-400">
            <FiCheck className="h-5 w-5 shrink-0" aria-hidden="true" />
            <p className="font-semibold text-slate-900 dark:text-white">
              Your feedback was sent to the ZoikoStream team.
            </p>
          </div>
          <p className="text-sm text-slate-600 dark:text-slate-300">
            Thank you — it helps us improve ZoikoStream APIs, documentation, and developer tools.
          </p>
        </div>
      ) : (
        <form onSubmit={handleSubmit} className="space-y-4">
          <p className="text-xs text-slate-500 dark:text-slate-400">
            Help us improve ZoikoStream developer tools, APIs, and documentation.
          </p>

          {undelivered && (
            <div
              role="alert"
              className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs text-amber-900 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-100"
            >
              <p className="font-semibold">Your feedback was not sent.</p>
              <p className="mt-1">
                Feedback from the console can&rsquo;t reach our team yet. Your text is still here
                — please send it through{" "}
                <Link to="/contact" className={cx("font-semibold underline underline-offset-2", focusRing)}>
                  Contact support
                </Link>
                .
              </p>
            </div>
          )}

          {error && (
            <div
              role="alert"
              className="rounded-lg border border-rose-200 bg-rose-50 p-3 text-xs text-rose-700 dark:border-rose-900/50 dark:bg-rose-950/40 dark:text-rose-300"
            >
              {error}
            </div>
          )}

          <div>
            <Label htmlFor="feedback-category" variant="console">
              Topic / Category
            </Label>
            <Select
              id="feedback-category"
              variant="console"
              value={category}
              onChange={(e) => setCategory(e.target.value)}
              disabled={submitting}
            >
              <option value="developer_platform">Developer Platform & APIs</option>
              <option value="documentation">Documentation & Quickstarts</option>
              <option value="webhooks_ingest">Webhooks & Stream Ingest</option>
              <option value="general">General feedback</option>
            </Select>
          </div>

          <div>
            <Label htmlFor="feedback-message" variant="console">
              Your feedback <span className="text-rose-500" aria-hidden="true">*</span>
            </Label>
            <Textarea
              id="feedback-message"
              variant="console"
              rows={4}
              maxLength={FEEDBACK_MAX}
              value={message}
              onChange={(e) => {
                setMessage(e.target.value);
                if (error) setError(null);
              }}
              placeholder="Tell us what worked, what was confusing, or what capability is missing..."
              aria-required="true"
              aria-describedby="feedback-message-hint"
              error={Boolean(error)}
              disabled={submitting}
            />
            <p id="feedback-message-hint" className="mt-1.5 text-xs text-slate-500 dark:text-slate-400">
              5–{FEEDBACK_MAX.toLocaleString()} characters. Do not include API keys, passwords, or personal credentials.
            </p>
          </div>
        </form>
      )}
    </Modal>
  );
}

export function AccessibilityModal({ open, onClose }) {
  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Accessibility & Keyboard Navigation"
      size="lg"
      closeOnBackdrop
      footer={
        <ConsoleButton variant="secondary" type="button" onClick={onClose}>
          Close
        </ConsoleButton>
      }
    >
      <div className="space-y-5 text-sm text-slate-600 dark:text-slate-300">
        <p>
          ZoikoStream is committed to providing an accessible console for developers and operators of all abilities.
        </p>

        <div>
          <h3 className="mb-2 text-xs font-semibold uppercase tracking-wider text-slate-900 dark:text-white">
            Keyboard Shortcuts
          </h3>
          <dl className="grid grid-cols-1 gap-2.5 sm:grid-cols-2">
            <div className="rounded-lg border border-slate-200 bg-slate-50 p-2.5 dark:border-slate-800 dark:bg-slate-800/50">
              <dt className="font-mono text-xs font-bold text-violet-600 dark:text-violet-400">
                Tab / Shift + Tab
              </dt>
              <dd className="mt-1 text-xs text-slate-600 dark:text-slate-300">
                Navigate forward and backward through interactive elements, buttons, and form inputs.
              </dd>
            </div>
            <div className="rounded-lg border border-slate-200 bg-slate-50 p-2.5 dark:border-slate-800 dark:bg-slate-800/50">
              <dt className="font-mono text-xs font-bold text-violet-600 dark:text-violet-400">
                Enter / Space
              </dt>
              <dd className="mt-1 text-xs text-slate-600 dark:text-slate-300">
                Activate links, toggle switches, expand collapsible sections, and submit dialog forms.
              </dd>
            </div>
            <div className="rounded-lg border border-slate-200 bg-slate-50 p-2.5 dark:border-slate-800 dark:bg-slate-800/50">
              <dt className="font-mono text-xs font-bold text-violet-600 dark:text-violet-400">
                Escape (Esc)
              </dt>
              <dd className="mt-1 text-xs text-slate-600 dark:text-slate-300">
                Dismiss open dialogs, drawers, dropdown menus, and overlays.
              </dd>
            </div>
            <div className="rounded-lg border border-slate-200 bg-slate-50 p-2.5 dark:border-slate-800 dark:bg-slate-800/50">
              <dt className="font-mono text-xs font-bold text-violet-600 dark:text-violet-400">
                Arrow Keys
              </dt>
              <dd className="mt-1 text-xs text-slate-600 dark:text-slate-300">
                Cycle through menu items, tab lists, and select options where supported.
              </dd>
            </div>
          </dl>
        </div>

        <div className="space-y-3 border-t border-slate-100 pt-3 dark:border-slate-800">
          <div>
            <h4 className="text-xs font-semibold text-slate-900 dark:text-white">
              Visual & Contrast Standards
            </h4>
            <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
              Both light and dark console themes are calibrated against WCAG 2.1 Level AA color contrast ratios for text and essential interface components.
            </p>
          </div>

          <div>
            <h4 className="text-xs font-semibold text-slate-900 dark:text-white">
              Motion Sensitivity
            </h4>
            <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
              The interface respects the system-level <code className="rounded bg-slate-100 px-1 py-0.5 font-mono text-[11px] dark:bg-slate-800">prefers-reduced-motion</code> setting, disabling animated transitions and pulsing indicators.
            </p>
          </div>

          <div>
            <h4 className="text-xs font-semibold text-slate-900 dark:text-white">
              Assistive Technology & Feedback
            </h4>
            <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
              We continuously test and improve screen reader compatibility (NVDA, JAWS, VoiceOver). If you encounter accessibility hurdles, please use the Feedback link or contact support.
            </p>
          </div>
        </div>
      </div>
    </Modal>
  );
}

export default function DeveloperPlatform() {
  const [query, setQuery] = useState("");
  const [stage, setStage] = useState(null);
  const [feedbackOpen, setFeedbackOpen] = useState(false);
  const [a11yOpen, setA11yOpen] = useState(false);
  const feedbackTriggerRef = useRef(null);
  const a11yTriggerRef = useRef(null);

  const handleCloseFeedback = () => {
    setFeedbackOpen(false);
    feedbackTriggerRef.current?.focus();
  };

  const handleCloseA11y = () => {
    setA11yOpen(false);
    a11yTriggerRef.current?.focus();
  };

  // Posture only — the catalogue above renders immediately, so there is no page skeleton
  // to wait behind. Same endpoint the Overview polls; no new API surface.
  const { data, reload } = useApi(() =>
    api.get("/organization/overview", { params: { range: "24h" } }).then((r) => r.data)
  );
  useInterval(reload, REFRESH_MS);

  // The organization's own integration state: keys (with the server's lifecycle counts) and
  // webhook endpoints. Admin-only on the server; a refusal reads as "Unavailable", never as zero.
  const dev = useApi(() => api.get("/organization/developer").then((r) => r.data));
  const keys = Array.isArray(dev.data?.api_keys) ? dev.data.api_keys : null;
  const hooks = Array.isArray(dev.data?.webhooks) ? dev.data.webhooks : null;
  const inventory = keyInventory(dev.data);
  const activeKeys = inventory?.active ?? null;
  const enabledHooks = hooks ? hooks.filter((h) => h.enabled !== false).length : null;

  const health = data?.service_health || {};
  const openIncidents = useMemo(
    () => (data?.lifecycle || []).reduce((n, s) => n + (s.open_incidents || 0), 0),
    [data]
  );

  const guides = useMemo(() => {
    const q = query.trim().toLowerCase();
    return QUICKSTARTS.filter(
      (g) =>
        (!stage || g.stages.includes(stage)) &&
        (!q || `${g.title} ${g.desc} ${g.crumb}`.toLowerCase().includes(q))
    );
  }, [query, stage]);

  return (
    <div className="space-y-6">
      <OrganizationPageHeader
        breadcrumbs={[["Organization", "/organization/manage"], ["Developers", "/organization/developers"], ["Overview"]]}
        eyebrow="Integration workspace / Conditional experience"
        title="Developers"
        subtitle="Build secure integrations with governed API access and event-driven workflows."
        actions={
          <>
            <ConsoleButton href="/organization/credentials" variant="secondary" size="sm" leftIcon={FiKey}>
              API credentials
            </ConsoleButton>
            <ConsoleButton href="/organization/live-inputs" size="sm" leftIcon={FiPlay}>
              Start the quickstart
            </ConsoleButton>
          </>
        }
      />

      <Banner
        icon={FiLock}
        title="Conditional module — shown once developer access is in use"
        aside={<Tag tone="slate"><FiShield aria-hidden="true" /> Organization admins only</Tag>}
      >
        This workspace appears in the rail when your organization has an API key or a webhook endpoint. Every request is
        still checked by the server for organization and role.
      </Banner>

      <KpiRow>
        <KpiTile
          icon={FiKey}
          label="API credentials"
          value={dev.error ? null : activeKeys}
          emphasis={activeKeys == null ? "text" : "number"}
          caption={
            dev.error
              ? "Unavailable right now"
              : dev.loading && !dev.data
              ? "Loading…"
              : !inventory
              ? "Key inventory not reported"
              : inventory.total === 0
              ? "No credentials issued yet"
              : `Active of ${inventory.total} issued${inventory.expiring_30d ? ` · ${inventory.expiring_30d} expiring in 30 days` : ""}`
          }
          to="/organization/credentials"
        />
        <KpiTile
          icon={FiLayers}
          tone="sky"
          label="Sandbox access"
          value="Not available"
          emphasis="text"
          caption="No sandbox environment is provisioned"
        />
        <KpiTile
          icon={FiLink}
          tone="green"
          label="Webhooks"
          value={dev.error ? null : enabledHooks}
          caption={
            dev.error
              ? "Unavailable right now"
              : hooks == null
              ? "Loading…"
              : hooks.length === 0
              ? "No endpoints registered"
              : "Enabled endpoints · signed deliveries"
          }
        />
        <KpiTile
          icon={FiActivity}
          tone="pink"
          label="Request telemetry"
          value="Not connected"
          emphasis="text"
          caption="Per-key request metrics are not collected"
        />
      </KpiRow>

      <div className="grid items-start gap-6 xl:grid-cols-[minmax(0,1.35fr)_minmax(0,1fr)]">
        <div className="min-w-0 space-y-6">
        <Panel
          eyebrow="Getting started"
          title="Integration readiness"
          subtitle="The prerequisites before an integration goes to production"
          action={<PanelLink to="/organization/credentials">Manage credentials</PanelLink>}
        >
          <ol className={cx("divide-y", CONSOLE.divideY)}>
            <ReadinessStep
              n={1}
              icon={FiShield}
              title="Confirm organization access"
              detail="Developer settings are readable only by an organization admin"
              state={dev.error ? "error" : dev.data ? "done" : "unavailable"}
              label={dev.error ? "Unavailable" : dev.data ? "Confirmed" : "Checking"}
            />
            <ReadinessStep
              n={2}
              icon={FiKey}
              title="Create a credential"
              detail="Shown once on creation; set an expiry and revoke when unused"
              state={activeKeys > 0 ? "done" : keys ? "todo" : "unavailable"}
              label={activeKeys > 0 ? "Done" : keys ? "To do" : "—"}
              to="/organization/credentials"
            />
            <ReadinessStep
              n={3}
              icon={FiLink}
              title="Register a signed webhook"
              detail="Through the API (POST /organization/developer/webhooks) — then verify X-Zoiko-Signature at your receiver"
              state={enabledHooks > 0 ? "done" : hooks ? "todo" : "unavailable"}
              label={enabledHooks > 0 ? "Done" : hooks ? "To do" : "—"}
            />
            <ReadinessStep
              n={4}
              icon={FiCheck}
              title="Validate in a sandbox"
              detail="No sandbox environment exists yet — test against a draft event"
              state="unavailable"
              label="Not available"
            />
          </ol>
        </Panel>

        <IntegrationActivity hooks={hooks} failed={Boolean(dev.error)} />
        </div>

        <div className="min-w-0 space-y-6">
          <Panel eyebrow="Reference architecture" title="How requests are authorized" subtitle="Every call is checked at the API boundary">
            <div className="flex items-stretch gap-1">
              <FlowBox icon={FiCode} title="Your application" detail="API key" />
              <FlowArrow label="HTTPS" />
              <FlowBox icon={FiShield} title="API boundary" detail="Key check · org scope" />
              <FlowArrow label="Scoped" />
              <FlowBox icon={FiCalendar} title="Your organization" detail="Events · media · analytics" />
            </div>
          </Panel>

          <Panel eyebrow="Built-in safeguards" title="Credential & webhook governance" subtitle="What the platform enforces today">
            <ul className={cx("divide-y", CONSOLE.divideY)}>
              {SAFEGUARDS.map(([title, detail]) => (
                <li key={title} className="flex items-start gap-3 py-2.5">
                  <span aria-hidden="true" className="mt-0.5 grid h-6 w-6 shrink-0 place-items-center rounded-md bg-emerald-50 text-emerald-600 dark:bg-emerald-500/15 dark:text-emerald-300">
                    <FiCheck />
                  </span>
                  <div className="min-w-0">
                    <p className={cx("text-[13px] font-semibold", CONSOLE.heading)}>{title}</p>
                    <p className={cx("text-[12px]", CONSOLE.faint)}>{detail}</p>
                  </div>
                </li>
              ))}
            </ul>
          </Panel>
        </div>
      </div>

      {/* ── Docs by lifecycle stage ────────────────────────────────────────── */}
      <section className={cx(CONSOLE.panel, "overflow-hidden")}>
        <p
          className={cx(
            "px-4 pt-4 text-center text-[10px] uppercase tracking-[0.14em] sm:px-6",
            type.mono,
            CONSOLE.faint
          )}
        >
          Find docs by lifecycle stage — Contribute → Preserve
        </p>
        <div className="overflow-x-auto">
          <ol
            className="grid min-w-[46rem] px-4 pb-6 pt-6 sm:px-8"
            style={{ gridTemplateColumns: `repeat(${STAGES.length}, minmax(0, 1fr))` }}
          >
            {STAGES.map(([key, label], i) => {
              const color = STAGE_COLOR[key];
              const next = STAGES[i + 1];
              const on = stage === key;
              return (
                <li key={key} className="relative flex flex-col items-center">
                  {/* Connector drawn from this cell so it never overflows the last one. */}
                  {next && (
                    <span
                      aria-hidden="true"
                      className="absolute left-1/2 top-[5px] h-[2px] w-full"
                      style={{
                        background: `linear-gradient(90deg, ${color}, ${STAGE_COLOR[next[0]]})`,
                        opacity: 0.55,
                      }}
                    />
                  )}
                  <button
                    type="button"
                    onClick={() => setStage(on ? null : key)}
                    aria-pressed={on}
                    className={cx("group relative z-10 flex flex-col items-center rounded-md px-2 py-1", focusRing)}
                  >
                    <span
                      className={cx(
                        "h-2.5 w-2.5 rounded-full transition-transform duration-150 motion-reduce:transition-none",
                        on ? "scale-150 ring-2 ring-offset-2 ring-offset-white dark:ring-offset-black" : "group-hover:scale-125"
                      )}
                      style={{ backgroundColor: color, "--tw-ring-color": color }}
                    />
                    <span
                      className={cx(
                        "mt-3 text-[12px] font-medium transition-colors duration-150 motion-reduce:transition-none",
                        on ? CONSOLE.heading : cx(CONSOLE.body, "group-hover:text-slate-900 dark:group-hover:text-white")
                      )}
                    >
                      {label}
                    </span>
                  </button>
                </li>
              );
            })}
          </ol>
        </div>
      </section>

      {/* ── Quickstarts ────────────────────────────────────────────────────── */}
      <section>
        <div className="flex flex-wrap items-center justify-between gap-4">
          <h2 className={cx("text-[20px] font-semibold tracking-tight", CONSOLE.heading)}>Quickstarts</h2>
          {/* Filters the catalogue below — a live control, not decoration. */}
          <div className="relative order-last w-full sm:order-none sm:ml-auto sm:w-[360px]">
            <FiSearch
              className={cx("pointer-events-none absolute left-3.5 top-1/2 -translate-y-1/2 text-[15px]", CONSOLE.faint)}
              aria-hidden="true"
            />
            <Input
              variant="console"
              type="search"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              aria-label="Search the developer guides"
              placeholder='Search "live input", "webhook signature", "playback token"…'
              /* Side-only padding override, the same way PasswordField clears room for its
                 eye icon — safe against Tailwind's shorthand ordering (no tailwind-merge here). */
              className="pl-10 text-[13px]"
            />
          </div>
          <button
            type="button"
            onClick={() => {
              setStage(null);
              setQuery("");
            }}
            className={cx("shrink-0 rounded text-[12px] font-semibold", CONSOLE.link, focusRing)}
          >
            All guides →
          </button>
        </div>

        {guides.length === 0 ? (
          <p className={cx("mt-5 rounded-xl border border-dashed px-5 py-10 text-center text-[13px]", CONSOLE.divider, CONSOLE.muted)}>
            No guide matches that search.
          </p>
        ) : (
          <div className="mt-5 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {guides.map(({ icon: Icon, tile, title, desc, crumb, to }) => (
              <Link
                key={title}
                to={to}
                className={cx(
                  "group flex flex-col rounded-xl border p-5 transition-colors duration-150 motion-reduce:transition-none",
                  CONSOLE.panel,
                  CONSOLE.panelHover,
                  focusRing
                )}
              >
                <span className={cx("grid h-9 w-9 shrink-0 place-items-center rounded-lg", tile)} aria-hidden="true">
                  <Icon className="text-[17px]" />
                </span>
                <h3 className={cx("mt-4 text-[15px] font-semibold tracking-tight", CONSOLE.heading)}>{title}</h3>
                <p className={cx("mt-1.5 flex-1 text-[13px] leading-[20px]", CONSOLE.muted)}>{desc}</p>
                <span className={cx("mt-4 inline-flex items-center gap-0.5 text-[12px] font-semibold", CONSOLE.link)}>
                  {crumb}
                  <FiChevronRight
                    className="text-[13px] transition-transform duration-150 group-hover:translate-x-0.5 motion-reduce:transition-none"
                    aria-hidden="true"
                  />
                </span>
              </Link>
            ))}
          </div>
        )}
      </section>

      {/* ── Platform posture ───────────────────────────────────────────────── */}
      <div className="grid gap-4 lg:grid-cols-2">
        <FootPanel eyebrow="Platform status">
          <StatRow
            label="All monitored services"
            value={<HealthDot status={health.status || "neutral"} label={health.status === "ok" ? "Operational" : health.label} />}
          />
          <StatRow
            label="Active incidents"
            value={data ? openIncidents : null}
            reason="Waiting for the first overview poll"
            tone={openIncidents ? "text-rose-600 dark:text-rose-400" : CONSOLE.heading}
          />
          <StatRow
            label="Last 90 days uptime"
            value={null}
            reason="The availability probe retains 30 days at most — no 90-day window to report"
          />
        </FootPanel>

        <FootPanel eyebrow="Version & updates">
          <StatRow label="Current API version" value={API_VERSION} mono />
          <StatRow
            label="SDK updates this week"
            value={null}
            reason="Needs an SDK release feed (not integrated)"
          />
          <StatRow
            label="Latest deprecation"
            value={LATEST_DEPRECATION}
            mono
            tone="text-amber-600 dark:text-amber-400"
          />
        </FootPanel>
      </div>

      {/* ── Footer ─────────────────────────────────────────────────────────── */}
      <footer className={cx("flex flex-col gap-3 border-t pt-5 sm:flex-row sm:items-center sm:justify-between", CONSOLE.divider)}>
        <p className={cx("text-[12px]", CONSOLE.faint)}>
          © {new Date().getFullYear()} Zoiko Group. All rights reserved.
        </p>
        {/* Feedback and Accessibility open modals; Security and Status navigate; Terms is informational.
            inline-flex items-center: in the Android app the 44px touch-target floor (index.css) makes
            a link or button 44px tall with its text pinned to the top, 13px above plain-text items.
            Centering keeps the touch target and levels the row. */}
        <nav className="flex flex-wrap items-center gap-x-6 gap-y-2">
          <button
            ref={feedbackTriggerRef}
            type="button"
            onClick={() => setFeedbackOpen(true)}
            className={cx("inline-flex items-center rounded text-[12px]", CONSOLE.faint, "hover:text-slate-900 dark:hover:text-white", focusRing)}
          >
            Feedback
          </button>
          <button
            ref={a11yTriggerRef}
            type="button"
            onClick={() => setA11yOpen(true)}
            className={cx("inline-flex items-center rounded text-[12px]", CONSOLE.faint, "hover:text-slate-900 dark:hover:text-white", focusRing)}
          >
            Accessibility
          </button>
          <Link
            to="/organization/settings?tab=security"
            className={cx("inline-flex items-center rounded text-[12px]", CONSOLE.faint, "hover:text-slate-900 dark:hover:text-white", focusRing)}
          >
            Security
          </Link>
          <Link
            to="/organization/support"
            className={cx("inline-flex items-center rounded text-[12px]", CONSOLE.faint, "hover:text-slate-900 dark:hover:text-white", focusRing)}
          >
            Status
          </Link>
          <span className={cx("text-[12px]", CONSOLE.faint)}>
            Terms
          </span>
        </nav>
      </footer>

      <FeedbackModal open={feedbackOpen} onClose={handleCloseFeedback} />
      <AccessibilityModal open={a11yOpen} onClose={handleCloseA11y} />
    </div>
  );
}
