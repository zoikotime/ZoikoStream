import { useMemo, useState } from "react";
import { FiDownload, FiFilm, FiLock, FiRefreshCw, FiSearch, FiX } from "react-icons/fi";
import {
  Badge, Button, DataTable, DetailField, KpiCard, Panel, CONSOLE, cx, focusRing, type,
} from "../../components/admin";
import ConsoleScreen from "../../components/admin/ConsoleScreen";
import useInterval from "../../hooks/useInterval";
import Asset360Drawer from "./Asset360Drawer";
import ContentAccessDialog from "./ContentAccessDialog";
import {
  ASSETS, ASSET_KINDS, AVAILABILITY_STATES, AVAILABILITY_TONE, JOBS, JOB_TYPES,
  LIFECYCLE_STATES, LIFECYCLE_TONE, ORGANIZATIONS, POLICY_STATES, POLICY_TONE,
  PRESERVATION_STATES, PRESERVATION_TONE, PROCESSING_STATES, PROCESSING_TONE, PROVENANCE_TONE,
  REGIONS, REPLAY_TONE, TRACK_TONE, label,
} from "./mediaData";

// Media (ZST-WF-SA-MEDIA-001 · screen S06) — the cross-Organization surface for discovering,
// diagnosing, recovering, preserving and governing media assets and their derivatives.
//
// It is NOT a tenant content-management system, and the interface is built so it cannot drift
// into one: no asset creation, no editorial metadata editing, no Trust & Safety decisions, no
// legal-hold removal, and no route to customer media that skips the protected access session.
//
// WHAT THIS BUILD IS: the canonical interface over a STATIC demonstration payload. §36 lists
// the Media Asset Service, Processing Orchestrator, Preserve Service, Identity & Access and
// Audit as build blockers, and today's media API is org-scoped rather than cross-tenant. So
// nothing here fetches, nothing mutates, no media plays — and the page says so, because a
// console read as ground truth during an incident must not let demo figures pass for state.
//
// The organising rule is STD-04 / §5: there is NO generic status column. Lifecycle, processing,
// availability, policy, preservation, legal hold, provenance and mode are eight separate axes,
// so an operator can tell whether a problem is a transcode, a Trust & Safety decision, or a
// retention conflict — three completely different next actions.

// Field skins come from the console tokens so a hover or focus change lands on every filter
// row at once, instead of being re-typed per page.
const inputCls = CONSOLE.search;
const selectCls = CONSOLE.select;

const DATE_RANGES = [
  ["all", "Any created date"],
  ["7", "Created in last 7 days"],
  ["30", "Created in last 30 days"],
  ["365", "Created in last 12 months"],
];

const TRACK_STATE_LABEL = {
  validated: "Validated",
  provisioned: "Provisioned",
  failed: "Failed",
  pending: "In progress",
  absent: "Not provided",
};

// §21 safe intervention actions for the job queue. Each names the authorization it needs, so a
// control never looks cheaper than it is.
const JOB_ACTIONS = [
  ["Retry", "Platform Operations elevation · retryability checked · audited"],
  ["Reprocess", "Platform Operations elevation · new job lineage · audited"],
  ["Rebuild rendition", "Platform Operations elevation · targets the missing derivative only"],
  ["Revalidate", "Platform Operations elevation · no content change"],
  ["Cancel", "Cancelable state only · impact preview · existing outputs kept"],
  ["Escalate", "Platform Operations · links the asset and job to the Incident Center"],
];

const regionLabel = (id) => REGIONS.find((r) => r.id === id)?.label || id;

export default function Media() {
  // §8 page header filters
  const [q, setQ] = useState("");
  const [org, setOrg] = useState("all");
  const [kind, setKind] = useState("all");
  const [lifecycle, setLifecycle] = useState("all");
  const [processing, setProcessing] = useState("all");
  const [availability, setAvailability] = useState("all");
  const [policy, setPolicy] = useState("all");
  const [preservation, setPreservation] = useState("all");
  const [region, setRegion] = useState("all");
  const [dateRange, setDateRange] = useState("all");
  const [includeTest, setIncludeTest] = useState(false);

  // Job queue filters
  const [jobState, setJobState] = useState("all");
  const [jobType, setJobType] = useState("all");

  // The asset the operational panels below the portfolio are scoped to. A row click sets it
  // and opens the drawer; closing the drawer keeps the focus, so the tracks and replay panels
  // stay on the asset the operator was last looking at.
  const [focusId, setFocusId] = useState(ASSETS[0].asset_id);
  const [selected, setSelected] = useState(null);
  const [accessOpen, setAccessOpen] = useState(false);

  // A protected content-access session, held here rather than in the drawer so its banner
  // survives the drawer closing — §12 requires the banner to be visible for the whole session.
  //
  // The live session is DERIVED from the remaining time rather than cleared by the timer: a
  // session whose window has run out is not a session, and deriving it means there is no
  // window in which the banner and the content gate disagree about whether access is open.
  const [grant, setGrant] = useState(null);
  const [remaining, setRemaining] = useState(0);
  useInterval(() => setRemaining((s) => Math.max(0, s - 1)), 1000, remaining > 0);
  const session = remaining > 0 ? grant : null;

  const [ageSeconds, setAgeSeconds] = useState(0);
  useInterval(() => setAgeSeconds((s) => s + 1), 1000);

  const focused = ASSETS.find((a) => a.asset_id === focusId) || ASSETS[0];

  const openRow = (a) => {
    setFocusId(a.asset_id);
    setSelected(a);
  };

  const clearFilters = () => {
    setQ("");
    setOrg("all");
    setKind("all");
    setLifecycle("all");
    setProcessing("all");
    setAvailability("all");
    setPolicy("all");
    setPreservation("all");
    setRegion("all");
    setDateRange("all");
  };

  // Live mode is the default (STD-02). Test rows appear only while Include test is on, and
  // they stay visually distinct in the table below.
  const scoped = useMemo(() => ASSETS.filter((a) => includeTest || a.mode === "LIVE"), [includeTest]);

  const rows = useMemo(() => {
    const query = q.trim().toLowerCase();
    const maxDays = dateRange === "all" ? Infinity : Number(dateRange);
    return scoped.filter((a) => {
      if (query && !`${a.name} ${a.asset_id} ${a.org} ${a.source_event}`.toLowerCase().includes(query)) return false;
      if (org !== "all" && a.org !== org) return false;
      if (kind !== "all" && a.kind !== kind) return false;
      if (lifecycle !== "all" && a.lifecycle !== lifecycle) return false;
      if (processing !== "all" && a.processing !== processing) return false;
      if (availability !== "all" && a.availability !== availability) return false;
      if (policy !== "all" && a.policy !== policy) return false;
      if (preservation !== "all" && a.preservation !== preservation) return false;
      if (region !== "all" && a.region !== region) return false;
      if (a.created_days_ago > maxDays) return false;
      return true;
    });
  }, [scoped, q, org, kind, lifecycle, processing, availability, policy, preservation, region, dateRange]);

  const kpis = useMemo(() => {
    const liveJobs = JOBS.filter((j) => scoped.some((a) => a.asset_id === j.asset_id));
    // Provenance coverage: the denominator is the assets where provenance is actually
    // measurable. UNSUPPORTED means the capability is off here, so counting it as a failure
    // would turn a coverage gap into a fake integrity problem.
    const measurable = scoped.filter((a) => a.provenance !== "UNSUPPORTED");
    const verified = measurable.filter((a) => a.provenance === "VERIFIED");
    return {
      ready: scoped.filter((a) => a.lifecycle === "READY" && a.availability === "AVAILABLE").length,
      processing: liveJobs.filter((j) => ["QUEUED", "RUNNING"].includes(j.state)).length,
      failed: liveJobs.filter((j) => j.state === "FAILED").length,
      partial: liveJobs.filter((j) => j.state === "PARTIAL").length,
      replayPending: scoped.filter((a) => ["PROCESSING", "SCHEDULED"].includes(a.replay.state)).length,
      deletionBlocked: scoped.filter((a) => a.preservation_detail.deletion_eligibility.startsWith("Not eligible")).length,
      holdBlocked: scoped.filter((a) => a.legal_hold.active).length,
      provenancePct: measurable.length ? Math.round((verified.length / measurable.length) * 1000) / 10 : null,
      provenanceVerified: verified.length,
      provenanceMeasurable: measurable.length,
    };
  }, [scoped]);

  const jobRows = useMemo(
    () =>
      JOBS.filter(
        (j) =>
          scoped.some((a) => a.asset_id === j.asset_id) &&
          (jobState === "all" || j.state === jobState) &&
          (jobType === "all" || j.type === jobType)
      ),
    [scoped, jobState, jobType]
  );

  const assetColumns = [
    {
      key: "name",
      header: "Asset",
      sortable: true,
      render: (a) => (
        <div className="min-w-0">
          <p className={cx("text-[13px] font-semibold", CONSOLE.heading)}>{a.name}</p>
          <p className={cx("text-[11px]", type.mono, CONSOLE.faint)}>{a.asset_id}</p>
        </div>
      ),
    },
    {
      key: "org",
      header: "Organization",
      sortable: true,
      render: (a) => (
        <span className={cx("text-[13px]", CONSOLE.body)} title={`tenant ${a.tenant_id}`}>
          {a.org}
        </span>
      ),
    },
    { key: "kind", header: "Kind", sortable: true, render: (a) => <Badge tone="neutral">{a.kind}</Badge> },
    {
      key: "mode",
      header: "Mode",
      sortable: true,
      render: (a) => <Badge tone={a.mode === "TEST" ? "warning" : "neutral"}>{a.mode}</Badge>,
    },
    {
      key: "lifecycle",
      header: "Lifecycle",
      sortable: true,
      render: (a) => <Badge tone={LIFECYCLE_TONE[a.lifecycle]}>{label(a.lifecycle)}</Badge>,
    },
    {
      key: "processing",
      header: "Processing",
      sortable: true,
      render: (a) => <Badge tone={PROCESSING_TONE[a.processing]}>{label(a.processing)}</Badge>,
    },
    {
      key: "availability",
      header: "Availability",
      sortable: true,
      render: (a) => <Badge tone={AVAILABILITY_TONE[a.availability]}>{label(a.availability)}</Badge>,
    },
    {
      key: "policy",
      header: "Policy",
      sortable: true,
      render: (a) => <Badge tone={POLICY_TONE[a.policy]}>{label(a.policy)}</Badge>,
    },
    {
      key: "preservation",
      header: "Preservation",
      sortable: true,
      render: (a) => <Badge tone={PRESERVATION_TONE[a.preservation]}>{label(a.preservation)}</Badge>,
    },
    {
      key: "legal_hold",
      header: "Legal hold",
      sortable: true,
      sortValue: (a) => a.legal_hold.count,
      render: (a) => (
        <Badge tone={a.legal_hold.active ? "danger" : "neutral"}>
          {a.legal_hold.active ? `Yes · ${a.legal_hold.count}` : "No"}
        </Badge>
      ),
    },
    {
      key: "provenance",
      header: "Provenance",
      sortable: true,
      render: (a) => <Badge tone={PROVENANCE_TONE[a.provenance]}>{label(a.provenance)}</Badge>,
    },
    {
      key: "created",
      header: "Created / recorded",
      sortable: true,
      sortValue: (a) => -a.created_days_ago,
      render: (a) => (
        <div className="min-w-0">
          <p className={cx("whitespace-nowrap text-[12px]", CONSOLE.body)}>{a.created_local}</p>
          <p className={cx("whitespace-nowrap text-[11px]", type.mono, CONSOLE.faint)}>{a.created_utc}</p>
        </div>
      ),
    },
    { key: "region", header: "Region", sortable: true, render: (a) => regionLabel(a.region) },
    {
      key: "size",
      header: "Size / duration",
      align: "right",
      render: (a) => (
        <div className="min-w-0">
          <p className={cx("whitespace-nowrap text-[12px]", type.mono, CONSOLE.body)}>{a.size}</p>
          <p className={cx("whitespace-nowrap text-[11px]", type.mono, CONSOLE.faint)}>{a.duration}</p>
        </div>
      ),
    },
    {
      key: "last_activity",
      header: "Last activity",
      render: (a) => (
        <div className="min-w-0">
          <p className={cx("whitespace-nowrap text-[12px]", CONSOLE.body)}>{a.last_activity_local}</p>
          <p className={cx("whitespace-nowrap text-[11px]", type.mono, CONSOLE.faint)}>{a.last_activity_utc}</p>
        </div>
      ),
    },
    {
      key: "action",
      header: "Action",
      align: "right",
      render: (a) => (
        <Button variant="secondary" size="sm" onClick={() => openRow(a)} aria-label={`Open Asset 360 for ${a.name}`}>
          Open
        </Button>
      ),
    },
  ];

  const jobColumns = [
    { key: "job_id", header: "Job ID", sortable: true, mono: true },
    {
      key: "asset_name",
      header: "Asset",
      sortable: true,
      render: (j) => (
        <div className="min-w-0">
          <p className={cx("text-[13px]", CONSOLE.body)}>{j.asset_name}</p>
          <p className={cx("text-[11px]", type.mono, CONSOLE.faint)}>{j.asset_id}</p>
        </div>
      ),
    },
    { key: "type", header: "Job type", sortable: true },
    { key: "region", header: "Region", sortable: true, render: (j) => regionLabel(j.region) },
    {
      key: "state",
      header: "State",
      sortable: true,
      render: (j) => <Badge tone={PROCESSING_TONE[j.state]}>{label(j.state)}</Badge>,
    },
    { key: "priority", header: "Priority", sortable: true },
    {
      key: "attempt",
      header: "Attempts",
      align: "right",
      sortable: true,
      render: (j) => `${j.attempt} / ${j.attempts_total}`,
    },
    {
      key: "failure_code",
      header: "Failure code",
      render: (j) =>
        j.failure_code === "—" ? (
          <span className={CONSOLE.faint}>—</span>
        ) : (
          <div className="min-w-0">
            <code className="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-[11px] text-slate-700 dark:bg-white/[0.07] dark:text-neutral-200">
              {j.failure_code}
            </code>
            <p className={cx("mt-0.5 text-[11px] leading-snug", CONSOLE.faint)}>{j.failure_summary}</p>
          </div>
        ),
    },
    { key: "outputs", header: "Outputs", render: (j) => <span className={cx("text-[12px]", CONSOLE.body)}>{j.outputs}</span> },
  ];

  return (
    <ConsoleScreen
      title="Media"
      subtitle="Cross-Organization media operations, processing, preservation, integrity and governance visibility. Metadata inspection never implies permission to view customer content."
      ageSeconds={ageSeconds}
      hasData
      actions={
        <>
          {/* Stated, not implied — no authoritative media service is wired to this console yet. */}
          <Badge tone="brand">Wireframe · static data</Badge>
          <Button variant="secondary" leftIcon={FiRefreshCw} onClick={() => setAgeSeconds(0)}>
            Refresh
          </Button>
          <Button
            variant="secondary"
            leftIcon={FiDownload}
            title="Display only in this build — export is classification-aware, expiring and audited"
          >
            Export
          </Button>
        </>
      }
    >
      {/* §12 persistent banner. It sits at the top of the page, not inside the drawer, because
          it has to stay visible for the whole session. */}
      {session && (
        <div className="flex flex-wrap items-center gap-3 rounded-lg border border-green-300 bg-green-50 px-4 py-3 dark:border-green-500/30 dark:bg-green-500/10">
          <span className="flex min-w-0 items-center gap-2">
            <span className="relative flex h-2 w-2 shrink-0" aria-hidden="true">
              <span className="zk-pulse-ring absolute inline-flex h-full w-full rounded-full bg-green-500 opacity-60" />
              <span className="relative inline-flex h-2 w-2 rounded-full bg-green-500" />
            </span>
            <span className={cx("text-[13px] font-semibold", CONSOLE.heading)}>Customer content access active</span>
          </span>
          <span className={cx("min-w-0 text-[12px]", CONSOLE.body)}>
            {session.asset_name} · {session.asset_id} · {session.org} · {session.purpose} · case {session.caseRef}
          </span>
          <span className={cx("ml-auto text-[12px] font-semibold tabular-nums", type.mono, CONSOLE.heading)}>
            {Math.floor(remaining / 60)}:{String(remaining % 60).padStart(2, "0")} remaining
          </span>
          <Button variant="secondary" size="sm" leftIcon={FiX} onClick={() => setRemaining(0)}>
            End session
          </Button>
        </div>
      )}

      {/* §8 Page header and filters */}
      <Panel title="Portfolio scope" flush>
        <div className="flex flex-wrap items-center gap-3 px-4 py-3">
          <div className="relative min-w-[240px] flex-1">
            <FiSearch className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" aria-hidden="true" />
            <input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="Search Organizations, assets, recordings, replays, IDs or jobs…"
              aria-label="Search the media portfolio"
              className={inputCls}
            />
          </div>
          <select value={org} onChange={(e) => setOrg(e.target.value)} className={selectCls} aria-label="Filter by Organization">
            <option value="all">All Organizations</option>
            {ORGANIZATIONS.map((o) => (
              <option key={o} value={o}>
                {o}
              </option>
            ))}
          </select>
          <select value={kind} onChange={(e) => setKind(e.target.value)} className={selectCls} aria-label="Filter by asset kind">
            <option value="all">All asset kinds</option>
            {ASSET_KINDS.map((k) => (
              <option key={k} value={k}>
                {k}
              </option>
            ))}
          </select>
          <select value={lifecycle} onChange={(e) => setLifecycle(e.target.value)} className={selectCls} aria-label="Filter by lifecycle state">
            <option value="all">All lifecycle states</option>
            {LIFECYCLE_STATES.map((s) => (
              <option key={s} value={s}>
                {label(s)}
              </option>
            ))}
          </select>
          <select value={processing} onChange={(e) => setProcessing(e.target.value)} className={selectCls} aria-label="Filter by processing state">
            <option value="all">All processing states</option>
            {PROCESSING_STATES.map((s) => (
              <option key={s} value={s}>
                {label(s)}
              </option>
            ))}
          </select>
          <select value={availability} onChange={(e) => setAvailability(e.target.value)} className={selectCls} aria-label="Filter by playback availability">
            <option value="all">All availability</option>
            {AVAILABILITY_STATES.map((s) => (
              <option key={s} value={s}>
                {label(s)}
              </option>
            ))}
          </select>
          <select value={policy} onChange={(e) => setPolicy(e.target.value)} className={selectCls} aria-label="Filter by policy enforcement state">
            <option value="all">All policy states</option>
            {POLICY_STATES.map((s) => (
              <option key={s} value={s}>
                {label(s)}
              </option>
            ))}
          </select>
          <select value={preservation} onChange={(e) => setPreservation(e.target.value)} className={selectCls} aria-label="Filter by preservation state">
            <option value="all">All preservation states</option>
            {PRESERVATION_STATES.map((s) => (
              <option key={s} value={s}>
                {label(s)}
              </option>
            ))}
          </select>
          <select value={region} onChange={(e) => setRegion(e.target.value)} className={selectCls} aria-label="Filter by region">
            <option value="all">All regions</option>
            {REGIONS.map((r) => (
              <option key={r.id} value={r.id}>
                {r.label}
              </option>
            ))}
          </select>
          <select value={dateRange} onChange={(e) => setDateRange(e.target.value)} className={selectCls} aria-label="Filter by created date range">
            {DATE_RANGES.map(([v, l]) => (
              <option key={v} value={v}>
                {l}
              </option>
            ))}
          </select>
          <label className={cx(CONSOLE.checkboxRow, "text-[13px]", CONSOLE.body)}>
            <input
              type="checkbox"
              checked={includeTest}
              onChange={(e) => setIncludeTest(e.target.checked)}
              className={cx(CONSOLE.checkbox, focusRing)}
            />
            Include test mode
          </label>
          {includeTest && <Badge tone="warning">Test mode included</Badge>}
        </div>
      </Panel>

      {/* §9 KPI strip. Every card filters the portfolio or the job queue below. */}
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-3 xl:grid-cols-6">
        <KpiCard
          label="Assets ready"
          value={kpis.ready}
          note="Technically ready — not policy clearance and not replay publication"
          pressed={lifecycle === "READY" && availability === "AVAILABLE"}
          onClick={() => {
            const on = lifecycle === "READY" && availability === "AVAILABLE";
            setLifecycle(on ? "all" : "READY");
            setAvailability(on ? "all" : "AVAILABLE");
          }}
        />
        <KpiCard
          label="Processing"
          value={kpis.processing}
          note="Queued or running jobs · live mode only by default"
          pressed={jobState === "RUNNING"}
          onClick={() => setJobState(jobState === "RUNNING" ? "all" : "RUNNING")}
        />
        <KpiCard
          label="Failed jobs"
          value={kpis.failed}
          note={`${kpis.partial} partial with actionable derivative failures`}
          tone={kpis.failed ? "text-rose-600 dark:text-rose-400" : undefined}
          pressed={jobState === "FAILED"}
          onClick={() => setJobState(jobState === "FAILED" ? "all" : "FAILED")}
        />
        <KpiCard
          label="Replay pending"
          value={kpis.replayPending}
          note="Not yet replay-ready. Does not imply a public replay entitlement"
          pressed={false}
        />
        <KpiCard
          label="Deletion blocked"
          value={kpis.deletionBlocked}
          note={`${kpis.holdBlocked} by an active legal hold · a hold cannot be cleared from Media`}
          tone={kpis.deletionBlocked ? "text-amber-600 dark:text-amber-400" : undefined}
          pressed={preservation === "DELETION_SCHEDULED"}
          onClick={() => setPreservation(preservation === "DELETION_SCHEDULED" ? "all" : "DELETION_SCHEDULED")}
        />
        <KpiCard
          label="Provenance verified"
          value={kpis.provenancePct == null ? "—" : `${kpis.provenancePct}%`}
          note={`${kpis.provenanceVerified} of ${kpis.provenanceMeasurable} where collection is enabled — coverage, not an authenticity score`}
          pressed={false}
        />
      </div>

      {/* §10 S06-V01 Media Portfolio */}
      <Panel
        title="Media portfolio"
        description="Eight named state axes. There is no combined status column, because lifecycle, processing, availability, policy, preservation, hold, provenance and mode fail in different ways."
        flush
      >
        <DataTable
          columns={assetColumns}
          rows={rows}
          rowKey={(a) => a.asset_id}
          onRowClick={openRow}
          pageSize={6}
          minWidth={2200}
          empty={{
            icon: FiFilm,
            title: "No assets match these filters",
            description:
              "No asset in scope satisfies every active filter. Live mode is the default — enable Include test mode if you expected a test-mode asset.",
            action: (
              <Button variant="secondary" size="sm" onClick={clearFilters}>
                Clear filters
              </Button>
            ),
          }}
        />
      </Panel>

      {/* §13 S06-V03 Processing & Renditions — the cross-asset job queue. */}
      <Panel
        title="Processing jobs"
        description="Expected versus produced derivatives, attempt lineage and machine-readable failure codes."
        count={kpis.failed}
        flush
      >
        <div className="flex flex-wrap items-center gap-3 px-4 py-3">
          <select value={jobState} onChange={(e) => setJobState(e.target.value)} className={selectCls} aria-label="Filter jobs by state">
            <option value="all">All job states</option>
            {PROCESSING_STATES.map((s) => (
              <option key={s} value={s}>
                {label(s)}
              </option>
            ))}
          </select>
          <select value={jobType} onChange={(e) => setJobType(e.target.value)} className={selectCls} aria-label="Filter jobs by type">
            <option value="all">All job types</option>
            {JOB_TYPES.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
          <span className={cx("ml-auto text-[11px] leading-snug", CONSOLE.faint)}>
            An accepted retry is not a repair — success appears only when the processing service verifies the output.
          </span>
        </div>
        <div className={cx("border-t", CONSOLE.divider)} />
        <DataTable
          columns={jobColumns}
          rows={jobRows}
          rowKey={(j) => j.job_id}
          pageSize={6}
          minWidth={1500}
          rowActions={(j) => (
            <>
              <Button variant="ghost" size="sm" disabled={!j.retryable} title={j.retryable ? "Retry this job" : "This failure is not retryable"}>
                Retry
              </Button>
              <Button variant="ghost" size="sm" onClick={() => openRow(ASSETS.find((a) => a.asset_id === j.asset_id))}>
                Open asset
              </Button>
            </>
          )}
          empty={{
            icon: FiFilm,
            title: "No jobs match these filters",
            description: "No processing job in scope is in that state.",
          }}
        />
        <div className={cx("border-t px-5 py-4", CONSOLE.divider)}>
          <p className={cx("mb-2 text-[10px] font-semibold uppercase tracking-[0.14em]", CONSOLE.faint)}>
            Safe recovery actions and the authorization each requires
          </p>
          <div className="flex flex-wrap gap-x-6 gap-y-2">
            {JOB_ACTIONS.map(([action, auth]) => (
              <div key={action} className="min-w-[240px]">
                <Button variant="secondary" size="sm" className="w-full justify-start">
                  {action}
                </Button>
                <p className={cx("mt-0.5 px-1 text-[11px] leading-snug", CONSOLE.faint)}>{auth}</p>
              </div>
            ))}
          </div>
        </div>
      </Panel>

      {/* §15 S06-V04 Tracks & Accessibility, scoped to the focused asset. */}
      <Panel
        title="Tracks and accessibility"
        description={`${focused.name} · ${focused.asset_id} — open a portfolio row above to inspect another asset.`}
      >
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {focused.tracks.map((t) => (
            <div key={t.cls} className={cx(CONSOLE.inset, CONSOLE.panelHover, "transition-colors duration-150 motion-reduce:transition-none", "p-4")}>
              <div className="flex flex-wrap items-start justify-between gap-2">
                <p className={cx("text-[13px] font-semibold", CONSOLE.heading)}>{t.cls}</p>
                <Badge tone={TRACK_TONE[t.state]}>{TRACK_STATE_LABEL[t.state]}</Badge>
              </div>
              <dl className="mt-1">
                <DetailField label="Language" value={t.language} />
                <DetailField label="Detail" value={t.detail} />
                <DetailField label="Processing" value={label(t.processing)} />
                <DetailField label="Generation" value={t.automated ? "Automated" : "Not automated"} />
              </dl>
            </div>
          ))}
        </div>
        <p className={cx("mt-3 border-t pt-3 text-[12px] leading-snug", CONSOLE.divider, CONSOLE.faint)}>
          A caption file being present does not make an asset accessible. Provisioned, validated and
          quality-reviewed are distinct states, and automated generation is never presented as human
          review without an evidence artifact.
        </p>
      </Panel>

      {/* §16 S06-V05 Recording & Replay — recording and replay are independent states. */}
      <Panel
        title="Recording and replay"
        description="A finalized recording does not imply an audience replay. Completion, preparation, availability, access policy and retention are separate."
      >
        <div className="grid gap-3 lg:grid-cols-2 xl:grid-cols-3">
          {rows
            .filter((a) => a.recording.state !== "Not applicable")
            .map((a) => (
              <div key={a.asset_id} className={cx(CONSOLE.inset, CONSOLE.panelHover, "transition-colors duration-150 motion-reduce:transition-none", "p-4")}>
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <div className="min-w-0">
                    <p className={cx("text-[13px] font-semibold", CONSOLE.heading)}>{a.name}</p>
                    <p className={cx("text-[11px]", type.mono, CONSOLE.faint)}>{a.asset_id}</p>
                  </div>
                  <Badge tone={REPLAY_TONE[a.replay.state]}>Replay · {label(a.replay.state)}</Badge>
                </div>
                <dl className="mt-1">
                  <DetailField label="Recording state" value={a.recording.state} />
                  <DetailField label="Start" value={`${a.recording.start_local} · ${a.recording.start_utc}`} />
                  <DetailField label="End" value={`${a.recording.end_local} · ${a.recording.end_utc}`} />
                  <DetailField label="Continuity" value={a.recording.continuity} />
                  <DetailField label="Finalization" value={a.recording.finalization} />
                  <DetailField label="Retention" value={a.recording.retention} />
                </dl>
              </div>
            ))}
          {rows.filter((a) => a.recording.state !== "Not applicable").length === 0 && (
            <p className={cx("py-6 text-center text-[13px]", CONSOLE.faint)}>
              No asset in the current filter came from a recorded session.
            </p>
          )}
        </div>
      </Panel>

      <div className="grid gap-4 xl:grid-cols-2">
        {/* §12 the content boundary, stated on the page rather than only inside the dialog. */}
        <Panel title="Protected media access" description="S06-V08 — the only route to customer media essence.">
          <p className={cx("text-[13px] leading-[20px]", CONSOLE.body)}>
            The default is that no content essence is visible. Playback of a customer&apos;s media is a
            separate privileged session with a case reference, a substantive justification, a bounded
            duration, a persistent banner and an immutable access log — and the read is audited even
            though nothing is mutated.
          </p>
          <dl className="mt-3">
            <DetailField label="Current session" value={session ? `Active · ${session.asset_id}` : "None"} />
            <DetailField label="Scope rule" value="One asset, or an explicitly approved bounded set. Never broad cross-tenant browsing." />
            <DetailField label="Download" value="Disabled by default. It needs separate explicit permission." />
            <DetailField label="Expiry" value="Automatic. An extension requires reauthorization; in-progress notes survive it." />
          </dl>
          <Button
            variant="secondary"
            size="sm"
            leftIcon={FiLock}
            className="mt-3"
            onClick={() => setAccessOpen(true)}
          >
            Request content access · {focused.asset_id}
          </Button>
        </Panel>

        <Panel title="Media control principles">
          <ul className={cx("space-y-2 text-[13px]", CONSOLE.body)}>
            <li>
              <strong className={CONSOLE.heading}>Metadata is not content.</strong> Inspecting an
              asset&apos;s technical state never implies permission to view the customer&apos;s media.
            </li>
            <li>
              <strong className={CONSOLE.heading}>Trust &amp; Safety decides enforcement.</strong> Media
              executes and displays the authoritative decision. It cannot create, weaken or lift one.
            </li>
            <li>
              <strong className={CONSOLE.heading}>Governance owns holds and retention.</strong> Media
              shows why deletion is blocked. It never bypasses a hold or edits retention to avoid one.
            </li>
            <li>
              <strong className={CONSOLE.heading}>Repairs go through the service.</strong> Every retry
              and reprocess executes in the authoritative pipeline, is audited, and shows success only
              once the output is verified.
            </li>
            <li>
              <strong className={CONSOLE.heading}>Verified is not true.</strong> A verified Content
              Credential means the credential and its binding validated — not that every assertion
              about the content is factually true, safe or lawful.
            </li>
          </ul>
        </Panel>
      </div>

      <Asset360Drawer
        asset={selected}
        open={Boolean(selected)}
        onClose={() => setSelected(null)}
        session={session ? { ...session, remaining: `${Math.floor(remaining / 60)}m ${remaining % 60}s` } : null}
        onRequestAccess={() => setAccessOpen(true)}
      />

      <ContentAccessDialog
        asset={selected || focused}
        open={accessOpen}
        onClose={() => setAccessOpen(false)}
        onGrant={(s) => {
          setGrant(s);
          setRemaining(s.minutes * 60);
        }}
      />
    </ConsoleScreen>
  );
}
