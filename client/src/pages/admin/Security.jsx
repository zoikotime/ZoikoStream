import { useMemo, useState } from "react";
import { FiDownload, FiLock, FiRefreshCw, FiSearch, FiShield, FiX } from "react-icons/fi";
import {
  Badge, Button, DataTable, DetailField, KpiCard, Panel, CONSOLE, cx, focusRing, type,
} from "../../components/admin";
import ConsoleScreen from "../../components/admin/ConsoleScreen";
import useInterval from "../../hooks/useInterval";
import Case360Drawer, { AxisRow } from "./Case360Drawer";
import ProtectedPreviewDialog from "./ProtectedPreviewDialog";
import RestrictionProposalDialog from "./RestrictionProposalDialog";
import {
  APPEAL_STATES, APPEAL_TONE, CASES, CASE_STATES, CASE_STATE_TONE, CONFIDENCE_TONE,
  CONTENT_TYPES, ENFORCEMENT_TONE, FINDING_TONE, LIVE_CONTEXTS, LIVE_TONE, ORGANIZATIONS,
  OUTCOMES, OWNERS, POLICY_FAMILIES, PRIORITIES, PRIORITY_TONE, REGIONS, SIGNAL_LABEL,
  SIGNAL_TREATMENT, SLA_STATES, SLA_TONE, label,
} from "./trustSafetyData";

// Trust & Safety (ZST-WF-SA-TS-001 · screen S08) — the policy-governed case-management and
// enforcement surface for tenant-originated broadcast content.
//
// It is NOT an audience moderation dashboard, and the LOCKED LAUNCH RULE is enforced by absence:
// there is no chat, no Q&A, no reactions, no comments, no audience uploads, no audience report
// queue and no Moderator Dashboard anywhere in this file or its data.
//
// WHAT THIS BUILD IS: the canonical interface over a STATIC demonstration payload. §36 lists the
// Trust & Safety Policy Registry, Identity & Access, Audit and the Evidence store as build
// blockers — and for this page in particular the audit sink is what makes every gate here real
// rather than decorative. So nothing fetches, nothing mutates, no media plays, and the page says
// so on its face.
//
// Two rules do the most work below:
//   · STD-04 / §4 — nine separate axes. A case that is VERIFYING with a confirmed finding, a
//     reversed enforcement and an overturned appeal is a real and common shape, and each axis
//     tells the reviewer to do something different.
//   · NO AUTOMATED GUILT — confidence belongs to a signal. The queue renders it in a neutral
//     tone beside its source treatment, so "automated · HIGH" can never read as a verdict.

// Field skins come from the console tokens so a hover or focus change lands on every filter
// row at once, instead of being re-typed per page.
const inputCls = CONSOLE.search;
const selectCls = CONSOLE.select;

const TIME_RANGES = [
  ["open", "Open workload"],
  ["24h", "Last 24 hours"],
  ["7d", "Last 7 days"],
  ["all", "All cases"],
];

const CONTENT_TYPE_LABEL = {
  live_event: "Live event",
  recorded_event: "Recorded event",
  replay: "Replay",
  uploaded_asset: "Uploaded asset",
  tenant_media: "Tenant media",
};

// §8.1 default sort. Critical live before critical non-live before high live, then the safety
// deadline. Service tier is the LAST tiebreaker and never lifts a case above a safety priority —
// commercial value cannot lower a verified safety priority.
const PRIORITY_RANK = { CRITICAL: 0, HIGH: 1, MEDIUM: 2, LOW: 3, INFORMATIONAL: 4 };
const TIER_RANK = { Enterprise: 0, Business: 1, Standard: 2 };
const SLA_RANK = { BREACHED: 0, WARNING: 1, ON_TRACK: 2 };
const OPEN_STATES = ["NEW", "TRIAGED", "EVIDENCE_REQUIRED", "UNDER_REVIEW", "DECISION_PENDING", "ACTION_PENDING", "VERIFYING"];

export default function Security() {
  // §7 header filters
  const [q, setQ] = useState("");
  const [range, setRange] = useState("open");
  const [priority, setPriority] = useState("all");
  const [caseState, setCaseState] = useState("all");
  const [family, setFamily] = useState("all");
  const [org, setOrg] = useState("all");
  const [contentType, setContentType] = useState("all");
  const [liveContext, setLiveContext] = useState("all");
  const [region, setRegion] = useState("all");
  const [owner, setOwner] = useState("all");
  const [sla, setSla] = useState("all");
  const [appealState, setAppealState] = useState("all");

  const [selected, setSelected] = useState(null);
  const [focusId, setFocusId] = useState(CASES[0].case_id);
  const [previewOpen, setPreviewOpen] = useState(false);
  const [proposalOpen, setProposalOpen] = useState(false);
  const [chosenOutcome, setChosenOutcome] = useState(null);

  // A protected review session, derived from its remaining time rather than cleared by the timer:
  // a session whose window has closed is not a session, so the banner and the gate can never
  // disagree about whether content access is open.
  const [grant, setGrant] = useState(null);
  const [remaining, setRemaining] = useState(0);
  useInterval(() => setRemaining((s) => Math.max(0, s - 1)), 1000, remaining > 0);
  const session = remaining > 0 ? grant : null;

  const [ageSeconds, setAgeSeconds] = useState(0);
  useInterval(() => setAgeSeconds((s) => s + 1), 1000);

  const focused = CASES.find((c) => c.case_id === focusId) || CASES[0];
  // §10 Urgent Live Review is scoped to live content, so it never presents an ended event as
  // time-critical. When nothing is live it says so rather than falling back to an ordinary case.
  const urgent = CASES.find((c) => c.urgent_live && c.live_context === "LIVE") || null;

  const openRow = (c) => {
    setFocusId(c.case_id);
    setSelected(c);
  };

  const clearFilters = () => {
    setQ("");
    setPriority("all");
    setCaseState("all");
    setFamily("all");
    setOrg("all");
    setContentType("all");
    setLiveContext("all");
    setRegion("all");
    setOwner("all");
    setSla("all");
    setAppealState("all");
  };

  // Live mode is the operational default (STD-02): test-mode activity never consumes live
  // attention counts, so it is excluded from the KPI strip and from the open workload.
  const liveMode = useMemo(() => CASES.filter((c) => c.mode === "LIVE"), []);

  const kpis = useMemo(
    () => ({
      urgentLive: liveMode.filter((c) => c.urgent_live && c.live_context === "LIVE").length,
      open: liveMode.filter((c) => OPEN_STATES.includes(c.case_state)).length,
      awaitingEvidence: liveMode.filter((c) => c.case_state === "EVIDENCE_REQUIRED").length,
      appeals: liveMode.filter((c) => ["FILED", "UNDER_REVIEW"].includes(c.appeal_state)).length,
      slaAtRisk: liveMode.filter((c) => ["WARNING", "BREACHED"].includes(c.sla_state) && OPEN_STATES.includes(c.case_state)).length,
      breached: liveMode.filter((c) => c.sla_state === "BREACHED").length,
      verifying: liveMode.filter((c) => c.verification_pending).length,
    }),
    [liveMode]
  );

  const rows = useMemo(() => {
    const query = q.trim().toLowerCase();
    return CASES.filter((c) => {
      if (range === "open" && !OPEN_STATES.includes(c.case_state)) return false;
      if (range === "24h" && !["6m", "36m", "42m", "1h 18m", "1h 46m", "3h 11m", "14h 46m"].includes(c.age)) return false;
      if (range === "7d" && c.age.includes("d") && parseInt(c.age, 10) > 7) return false;
      if (query && !`${c.case_id} ${c.title} ${c.org} ${c.content_ref} ${c.owner} ${c.reason}`.toLowerCase().includes(query))
        return false;
      if (priority !== "all" && c.priority !== priority) return false;
      if (caseState !== "all" && c.case_state !== caseState) return false;
      if (family !== "all" && c.reason_family !== family) return false;
      if (org !== "all" && c.org !== org) return false;
      if (contentType !== "all" && c.content_type !== contentType) return false;
      if (liveContext !== "all" && c.live_context !== liveContext) return false;
      if (region !== "all" && c.region !== region) return false;
      if (owner !== "all" && c.owner !== owner) return false;
      if (sla !== "all" && c.sla_state !== sla) return false;
      if (appealState !== "all" && c.appeal_state !== appealState) return false;
      return true;
    }).sort(
      (a, b) =>
        // Critical live first, then critical, then high live — the spec's ladder, in order.
        Number(b.priority === "CRITICAL" && b.live_context === "LIVE") -
          Number(a.priority === "CRITICAL" && a.live_context === "LIVE") ||
        PRIORITY_RANK[a.priority] - PRIORITY_RANK[b.priority] ||
        Number(b.live_context === "LIVE") - Number(a.live_context === "LIVE") ||
        SLA_RANK[a.sla_state] - SLA_RANK[b.sla_state] ||
        Number(a.owner === "Unassigned") - Number(b.owner === "Unassigned") ||
        TIER_RANK[a.tier] - TIER_RANK[b.tier]
    );
  }, [q, range, priority, caseState, family, org, contentType, liveContext, region, owner, sla, appealState]);

  const appealRows = useMemo(() => CASES.filter((c) => c.appeal_state !== "NOT_APPLICABLE"), []);

  const columns = [
    {
      key: "priority",
      header: "Priority",
      sortable: true,
      sortValue: (c) => PRIORITY_RANK[c.priority],
      render: (c) => (
        <Badge tone={PRIORITY_TONE[c.priority]} dot={c.priority === "CRITICAL"}>
          {label(c.priority)}
        </Badge>
      ),
    },
    {
      key: "case_id",
      header: "Case",
      sortable: true,
      render: (c) => (
        <div className="min-w-0 max-w-[320px]">
          <p className={cx("text-[12px] font-semibold", type.mono, CONSOLE.heading)}>{c.case_id}</p>
          <p className={cx("truncate text-[12px]", CONSOLE.body)} title={c.title}>
            {c.title}
          </p>
        </div>
      ),
    },
    {
      key: "org",
      header: "Organization",
      sortable: true,
      render: (c) => (
        <div className="min-w-0">
          <p className={cx("text-[13px]", CONSOLE.body)}>{c.org}</p>
          <p className={cx("text-[11px]", type.mono, CONSOLE.faint)} title={`tenant ${c.tenant_id}`}>
            {c.tier}
          </p>
        </div>
      ),
    },
    {
      key: "content_ref",
      header: "Content",
      render: (c) => (
        <div className="min-w-0 max-w-[220px]">
          <p className={cx("truncate text-[12px]", CONSOLE.body)} title={c.content_ref}>
            {c.content_ref}
          </p>
          <p className={cx("text-[11px]", CONSOLE.faint)}>{CONTENT_TYPE_LABEL[c.content_type]}</p>
        </div>
      ),
    },
    {
      key: "live_context",
      header: "Live",
      sortable: true,
      render: (c) => (
        <Badge tone={LIVE_TONE[c.live_context]} dot={c.live_context === "LIVE"}>
          {label(c.live_context)}
        </Badge>
      ),
    },
    {
      key: "reason",
      header: "Reason",
      sortable: true,
      sortValue: (c) => c.reason_family,
      render: (c) => (
        <div className="min-w-0 max-w-[200px]">
          <p className={cx("text-[12px] font-medium", CONSOLE.heading)}>{c.reason_family}</p>
          <p className={cx("truncate text-[11px]", CONSOLE.faint)} title={c.reason}>
            {c.reason}
          </p>
        </div>
      ),
    },
    {
      key: "signal",
      header: "Signal",
      render: (c) => (
        <div className="min-w-0 max-w-[200px]">
          {c.signal_sources.map((s) => (
            <p key={s} className={cx("text-[12px]", CONSOLE.body)}>
              {SIGNAL_LABEL[s]}
            </p>
          ))}
          <p className={cx("text-[11px] leading-snug", CONSOLE.faint)}>{SIGNAL_TREATMENT[c.signal_sources[0]]}</p>
        </div>
      ),
    },
    {
      key: "confidence",
      header: "Confidence",
      sortable: true,
      render: (c) => (
        <span title="Signal confidence only — it is never a finding">
          <Badge tone={CONFIDENCE_TONE[c.confidence]}>{label(c.confidence)}</Badge>
        </span>
      ),
    },
    {
      key: "case_state",
      header: "Case state",
      sortable: true,
      render: (c) => <Badge tone={CASE_STATE_TONE[c.case_state]}>{label(c.case_state)}</Badge>,
    },
    {
      key: "age",
      header: "Age",
      render: (c) => (
        <div className="min-w-0">
          <p className={cx("whitespace-nowrap text-[12px]", type.mono, CONSOLE.body)}>{c.age}</p>
          <p className={cx("whitespace-nowrap text-[11px]", CONSOLE.faint)}>opened {c.opened_utc}</p>
        </div>
      ),
    },
    {
      key: "sla_state",
      header: "SLA",
      sortable: true,
      sortValue: (c) => SLA_RANK[c.sla_state],
      render: (c) => (
        <div className="min-w-0">
          <Badge tone={SLA_TONE[c.sla_state]}>{label(c.sla_state)}</Badge>
          <p className={cx("mt-0.5 whitespace-nowrap text-[11px]", type.mono, CONSOLE.faint)}>due {c.due_utc}</p>
        </div>
      ),
    },
    {
      key: "owner",
      header: "Owner",
      sortable: true,
      render: (c) =>
        c.owner === "Unassigned" ? (
          <span className="text-[12px] font-semibold text-amber-600 dark:text-amber-400">Unassigned</span>
        ) : (
          <span className={cx("whitespace-nowrap text-[13px]", CONSOLE.body)}>{c.owner}</span>
        ),
    },
    {
      key: "action",
      header: "Action",
      align: "right",
      render: (c) => (
        <Button variant="secondary" size="sm" onClick={() => openRow(c)} aria-label={`Open Case 360 for ${c.case_id}`}>
          Open
        </Button>
      ),
    },
  ];

  const appealColumns = [
    { key: "case_id", header: "Case", sortable: true, mono: true },
    { key: "org", header: "Organization", sortable: true },
    {
      key: "decision",
      header: "Original decision",
      render: (c) => (
        <div className="min-w-0">
          <p className={cx("text-[12px]", CONSOLE.body)}>
            {OUTCOMES.find((o) => o.id === c.decision.outcome)?.label || c.decision.outcome}
          </p>
          <p className={cx("text-[11px]", CONSOLE.faint)}>{c.decision.recorded_at}</p>
        </div>
      ),
    },
    {
      key: "appeal_state",
      header: "Appeal state",
      sortable: true,
      sortValue: (c) => c.appeal_state,
      render: (c) => <Badge tone={APPEAL_TONE[c.appeal_state]}>{label(c.appeal_state)}</Badge>,
    },
    { key: "deadline", header: "Deadline", render: (c) => <span className={cx("text-[12px]", CONSOLE.body)}>{c.appeal.deadline}</span> },
    {
      key: "reviewer",
      header: "Independent reviewer",
      render: (c) => <span className={cx("text-[12px]", CONSOLE.body)}>{c.appeal.reviewer}</span>,
    },
    {
      key: "effect",
      header: "Enforcement effect",
      render: (c) => (
        <div className="min-w-0 max-w-[260px]">
          <Badge tone={ENFORCEMENT_TONE[c.enforcement_state]}>{label(c.enforcement_state)}</Badge>
          <p className={cx("mt-0.5 text-[11px] leading-snug", CONSOLE.faint)}>{c.appeal.effect}</p>
        </div>
      ),
    },
    {
      key: "action",
      header: "Action",
      align: "right",
      render: (c) => (
        <Button variant="secondary" size="sm" onClick={() => openRow(c)}>
          Open
        </Button>
      ),
    },
  ];

  return (
    <ConsoleScreen
      title="Trust & Safety"
      subtitle="Policy-governed review and enforcement for tenant-originated broadcast content. Signals prioritize review; they never become findings on their own."
      ageSeconds={ageSeconds}
      hasData
      actions={
        <>
          <Badge tone="brand">Wireframe · static data</Badge>
          <Button variant="secondary" leftIcon={FiRefreshCw} onClick={() => setAgeSeconds(0)}>
            Refresh
          </Button>
          <Button
            variant="secondary"
            leftIcon={FiDownload}
            title="Display only in this build — case metadata export is permission-controlled; protected evidence is governed separately"
          >
            Export
          </Button>
        </>
      }
    >
      {/* §10.1 persistent Protected Review banner. It lives on the page, not in the drawer, so it
          stays visible for the whole session. */}
      {session && (
        <div className="flex flex-wrap items-center gap-3 rounded-lg border border-amber-300 bg-amber-50 px-4 py-3 dark:border-amber-500/30 dark:bg-amber-500/10">
          <span className="flex min-w-0 items-center gap-2">
            <span className="relative flex h-2 w-2 shrink-0" aria-hidden="true">
              <span className="zk-pulse-ring absolute inline-flex h-full w-full rounded-full bg-amber-500 opacity-60" />
              <span className="relative inline-flex h-2 w-2 rounded-full bg-amber-500" />
            </span>
            <span className={cx("text-[13px] font-semibold", CONSOLE.heading)}>Protected review active</span>
          </span>
          <span className={cx("min-w-0 text-[12px]", CONSOLE.body)}>
            {session.case_id} · {session.org} · {session.content_ref} · {session.purpose} · audio starts muted
          </span>
          <span className={cx("ml-auto text-[12px] font-semibold tabular-nums", type.mono, CONSOLE.heading)}>
            {Math.floor(remaining / 60)}:{String(remaining % 60).padStart(2, "0")} remaining
          </span>
          <Button variant="secondary" size="sm" leftIcon={FiX} onClick={() => setRemaining(0)}>
            End review
          </Button>
        </div>
      )}

      {/* §7 header, filters and global case search */}
      <Panel title="Case scope" flush>
        <div className="flex flex-wrap items-center gap-3 px-4 py-3">
          <div className="relative min-w-[240px] flex-1">
            <FiSearch className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" aria-hidden="true" />
            <input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="Search cases, Organizations, events, assets, reports or IDs…"
              aria-label="Search Trust & Safety cases"
              className={inputCls}
            />
          </div>
          <select value={range} onChange={(e) => setRange(e.target.value)} className={selectCls} aria-label="Filter by time range">
            {TIME_RANGES.map(([v, l]) => (
              <option key={v} value={v}>
                {l}
              </option>
            ))}
          </select>
          <select value={priority} onChange={(e) => setPriority(e.target.value)} className={selectCls} aria-label="Filter by priority">
            <option value="all">All priorities</option>
            {PRIORITIES.map((p) => (
              <option key={p} value={p}>
                {label(p)}
              </option>
            ))}
          </select>
          <select value={caseState} onChange={(e) => setCaseState(e.target.value)} className={selectCls} aria-label="Filter by case state">
            <option value="all">All case states</option>
            {CASE_STATES.map((s) => (
              <option key={s} value={s}>
                {label(s)}
              </option>
            ))}
          </select>
          <select value={family} onChange={(e) => setFamily(e.target.value)} className={selectCls} aria-label="Filter by policy family">
            <option value="all">All policy families</option>
            {POLICY_FAMILIES.map((f) => (
              <option key={f} value={f}>
                {f}
              </option>
            ))}
          </select>
          <select value={org} onChange={(e) => setOrg(e.target.value)} className={selectCls} aria-label="Filter by Organization">
            <option value="all">All Organizations</option>
            {ORGANIZATIONS.map((o) => (
              <option key={o} value={o}>
                {o}
              </option>
            ))}
          </select>
          <select value={contentType} onChange={(e) => setContentType(e.target.value)} className={selectCls} aria-label="Filter by content type">
            <option value="all">All content types</option>
            {CONTENT_TYPES.map((t) => (
              <option key={t} value={t}>
                {CONTENT_TYPE_LABEL[t]}
              </option>
            ))}
          </select>
          <select value={liveContext} onChange={(e) => setLiveContext(e.target.value)} className={selectCls} aria-label="Filter by live context">
            <option value="all">Any live context</option>
            {LIVE_CONTEXTS.map((l) => (
              <option key={l} value={l}>
                {label(l)}
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
          <select value={owner} onChange={(e) => setOwner(e.target.value)} className={selectCls} aria-label="Filter by owner">
            <option value="all">All owners</option>
            {OWNERS.map((o) => (
              <option key={o} value={o}>
                {o}
              </option>
            ))}
          </select>
          <select value={sla} onChange={(e) => setSla(e.target.value)} className={selectCls} aria-label="Filter by SLA state">
            <option value="all">Any SLA state</option>
            {SLA_STATES.map((s) => (
              <option key={s} value={s}>
                {label(s)}
              </option>
            ))}
          </select>
          <select value={appealState} onChange={(e) => setAppealState(e.target.value)} className={selectCls} aria-label="Filter by appeal state">
            <option value="all">Any appeal state</option>
            {APPEAL_STATES.map((s) => (
              <option key={s} value={s}>
                {label(s)}
              </option>
            ))}
          </select>
          <span className={cx("ml-auto text-[11px] leading-snug", CONSOLE.faint)}>
            Region and jurisdiction are operational routing facts — never a legal conclusion.
          </span>
        </div>
      </Panel>

      {/* §7.1 KPI strip. Live mode only; test activity never consumes live attention. */}
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-3 xl:grid-cols-6">
        <KpiCard
          label="Urgent live reviews"
          value={kpis.urgentLive}
          note="Open cases on currently live content · live mode only"
          tone={kpis.urgentLive ? "text-rose-600 dark:text-rose-400" : undefined}
          pressed={liveContext === "LIVE"}
          onClick={() => setLiveContext(liveContext === "LIVE" ? "all" : "LIVE")}
        />
        <KpiCard
          label="Open cases"
          value={kpis.open}
          note="All active states excluding Resolved and Closed"
          pressed={range === "open"}
          onClick={() => setRange(range === "open" ? "all" : "open")}
        />
        <KpiCard
          label="Awaiting evidence"
          value={kpis.awaitingEvidence}
          note="Blocked on required evidence or external information"
          tone={kpis.awaitingEvidence ? "text-amber-600 dark:text-amber-400" : undefined}
          pressed={caseState === "EVIDENCE_REQUIRED"}
          onClick={() => setCaseState(caseState === "EVIDENCE_REQUIRED" ? "all" : "EVIDENCE_REQUIRED")}
        />
        <KpiCard
          label="Appeals"
          value={kpis.appeals}
          note="Filed or under independent review"
          pressed={appealState === "FILED"}
          onClick={() => setAppealState(appealState === "FILED" ? "all" : "FILED")}
        />
        <KpiCard
          label="SLA at risk"
          value={kpis.slaAtRisk}
          note={`${kpis.breached} breached · inside the configured warning window or past due`}
          tone={kpis.slaAtRisk ? "text-amber-600 dark:text-amber-400" : undefined}
          pressed={sla === "WARNING"}
          onClick={() => setSla(sla === "WARNING" ? "all" : "WARNING")}
        />
        <KpiCard
          label="Verification pending"
          value={kpis.verifying}
          note="Executed actions awaiting authoritative confirmation"
          tone={kpis.verifying ? "text-amber-600 dark:text-amber-400" : undefined}
          pressed={caseState === "VERIFYING"}
          onClick={() => setCaseState(caseState === "VERIFYING" ? "all" : "VERIFYING")}
        />
      </div>

      <div className="grid gap-4 2xl:grid-cols-[1.6fr_1fr]">
        {/* §8 S08-V01 Case queue */}
        <Panel
          title="Case queue"
          description="Critical live-context cases first, then critical, then high live. Organization service tier is only ever the final tiebreaker."
          count={kpis.urgentLive}
          flush
        >
          <DataTable
            columns={columns}
            rows={rows}
            rowKey={(c) => c.case_id}
            onRowClick={openRow}
            pageSize={6}
            minWidth={1900}
            empty={{
              icon: FiShield,
              title: "No cases match these filters",
              description:
                "No case in scope satisfies every active filter. Open workload is the default range — switch to All cases if you expected a resolved one.",
              action: (
                <Button variant="secondary" size="sm" onClick={clearFilters}>
                  Clear filters
                </Button>
              ),
            }}
          />
        </Panel>

        {/* §10 S08-V02 Urgent Live Review */}
        <div className="space-y-4">
          <Panel
            title="Urgent live review"
            description={urgent ? `${urgent.case_id} · ${urgent.org}` : undefined}
            action={urgent ? <Badge tone="danger" dot>Live</Badge> : undefined}
          >
            {!urgent ? (
              <p className={cx("py-8 text-center text-[13px]", CONSOLE.faint)}>
                No open case is attached to currently live content. Nothing here is time-critical.
              </p>
            ) : (
              <>
                <p className={cx("text-[14px] font-semibold leading-snug", CONSOLE.heading)}>{urgent.title}</p>
                <dl className="mt-2">
                  <DetailField label="Event" value={urgent.content_ref} />
                  <DetailField label="Risk classification" value={`${urgent.risk_classification}${urgent.unrepeatable ? " · unrepeatable" : ""}`} />
                  <DetailField label="Viewers" value={urgent.viewers} />
                  <DetailField label="Started" value={`${urgent.opened_local} · ${urgent.opened_utc}`} />
                  <DetailField label="Case age / due" value={`${urgent.age} · due ${urgent.due_utc}`} />
                  <DetailField label="Owner" value={urgent.owner} />
                </dl>

                {/* §10.1 protected preview gate — no content until justified elevation. */}
                <div className={cx("mt-3 rounded-lg border p-3", CONSOLE.inset)}>
                  <p className={cx("text-[11px] font-semibold uppercase tracking-[0.14em]", CONSOLE.faint)}>
                    Protected content preview
                  </p>
                  <p className={cx("mt-1 text-[12px] leading-snug", CONSOLE.body)}>
                    Access requires justified Trust &amp; Safety elevation. Preview is watermarked, logged,
                    time-bounded, and never autoplays with sound.
                  </p>
                  <Button variant="secondary" size="sm" leftIcon={FiLock} className="mt-2" onClick={() => { setFocusId(urgent.case_id); setPreviewOpen(true); }}>
                    Request protected preview
                  </Button>
                </div>

                {/* Evidence strip */}
                <div className={cx("mt-3 border-t pt-3", CONSOLE.divider)}>
                  <p className={cx("mb-1.5 text-[10px] font-semibold uppercase tracking-[0.14em]", CONSOLE.faint)}>
                    Evidence strip
                  </p>
                  <ul className="space-y-1.5">
                    {urgent.evidence.slice(0, 3).map((e) => (
                      <li key={e.evidence_id} className="flex items-start justify-between gap-2">
                        <span className={cx("min-w-0 text-[12px]", CONSOLE.body)}>
                          <span className={cx("font-semibold", type.mono)}>{e.evidence_id}</span> · {e.type}
                        </span>
                        <Badge tone={e.integrity === "verified" ? "success" : "info"} size="sm">
                          {label(e.integrity)}
                        </Badge>
                      </li>
                    ))}
                  </ul>
                </div>

                {/* Policy cues — candidates only, never an auto-selected final finding. */}
                <div className={cx("mt-3 border-t pt-3", CONSOLE.divider)}>
                  <p className={cx("mb-1.5 text-[10px] font-semibold uppercase tracking-[0.14em]", CONSOLE.faint)}>
                    Policy cues
                  </p>
                  {urgent.assessments.map((a) => (
                    <div key={a.clause} className="flex items-start justify-between gap-2">
                      <span className={cx("min-w-0 text-[12px]", CONSOLE.body)}>
                        <span className={cx("font-semibold", type.mono)}>{a.clause}</span> · {a.family}
                        <span className={cx("block text-[11px]", CONSOLE.faint)}>{a.version_at_content}</span>
                      </span>
                      <Badge tone={FINDING_TONE[a.finding]} size="sm">
                        {label(a.finding)}
                      </Badge>
                    </div>
                  ))}
                  <p className={cx("mt-1.5 text-[11px] leading-snug", CONSOLE.faint)}>
                    Candidate clauses only. The reviewer owns the final finding.
                  </p>
                </div>

                {/* Operational impact */}
                <div className={cx("mt-3 border-t pt-3", CONSOLE.divider)}>
                  <p className={cx("mb-1.5 text-[10px] font-semibold uppercase tracking-[0.14em]", CONSOLE.faint)}>
                    Operational impact of intervention
                  </p>
                  <ul className={cx("space-y-1 text-[12px]", CONSOLE.muted)}>
                    <li>Interruption stops delivery to {urgent.viewers} and ends the recording in progress.</li>
                    <li>Event restriction narrows access without ending the broadcast, and is reversible.</li>
                    <li>
                      {urgent.unrepeatable
                        ? "This event occurs once. Interruption is irreversible — that raises the evidence and authorization bar, it does not grant immunity."
                        : "The event is repeatable, so interruption is recoverable by rescheduling."}
                    </li>
                    <li>Preservation continues under the existing retention policy unless the approved action changes it.</li>
                  </ul>
                </div>

                <Button variant="primary" size="sm" className="mt-3 w-full" onClick={() => openRow(urgent)}>
                  Open Case 360
                </Button>
              </>
            )}
          </Panel>
        </div>
      </div>

      {/* §15 S08-V06 Decision & Enforcement, scoped to the focused case. */}
      <Panel
        title="Decision and enforcement"
        description={`${focused.case_id} · ${focused.org} — open a queue row above to work a different case.`}
      >
        <div className="mb-3">
          <AxisRow c={focused} />
        </div>

        <p className={cx("mb-2 text-[10px] font-semibold uppercase tracking-[0.14em]", CONSOLE.faint)}>
          Available outcomes · narrowest first
        </p>
        <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
          {OUTCOMES.map((o) => {
            const isChosen = chosenOutcome === o.id;
            const severe = o.severity === "severe";
            return (
              <button
                key={o.id}
                type="button"
                aria-pressed={isChosen}
                onClick={() => {
                  setChosenOutcome(isChosen ? null : o.id);
                  if (o.id === "ORGANIZATION_SUSPENSION_PROPOSAL" || o.id === "ORGANIZATION_RESTRICTION") {
                    if (focused.proposal) setProposalOpen(true);
                  }
                }}
                className={cx(
                  "rounded-lg border p-3 text-left transition duration-150 motion-reduce:transition-none",
                  focusRing,
                  isChosen
                    ? severe
                      ? "border-rose-400 bg-rose-50 dark:border-rose-500/50 dark:bg-rose-500/10"
                      : "border-violet-400 bg-violet-50 dark:border-violet-500/50 dark:bg-violet-500/10"
                    : cx(CONSOLE.inset, "hover:border-slate-300 dark:hover:border-white/20")
                )}
              >
                <span className="flex items-start justify-between gap-2">
                  <span className={cx("text-[13px] font-semibold", CONSOLE.heading)}>{o.label}</span>
                  {severe && <Badge tone="danger" size="sm">Severe</Badge>}
                </span>
                <span className={cx("mt-0.5 block text-[11px] leading-snug", CONSOLE.faint)}>{o.meaning}</span>
              </button>
            );
          })}
        </div>

        {chosenOutcome && (
          <div className={cx("mt-3 rounded-lg border px-4 py-3", CONSOLE.inset)}>
            <p className={cx("text-[12px] font-semibold", CONSOLE.heading)}>
              {OUTCOMES.find((o) => o.id === chosenOutcome).label} — what this action would require
            </p>
            <ul className={cx("mt-1.5 space-y-1 text-[12px]", CONSOLE.muted)}>
              <li>An explicit action label and an exact scope preview: Organization, event, asset, session or user.</li>
              <li>The policy finding and the clause version it rests on, plus the supporting evidence references.</li>
              <li>An impact summary, step-up authentication, and typed confirmation for high-impact actions.</li>
              <li>A second authorized approver where the policy requires it; the requester is always excluded.</li>
              <li>Execution through the authoritative service, immutable audit, then verified post-action state.</li>
              <li>A rollback route where one exists — and an explicit statement where none does.</li>
            </ul>
          </div>
        )}

        <p className={cx("mt-3 border-t pt-3 text-[12px] leading-snug", CONSOLE.divider, CONSOLE.faint)}>
          Every action requires a policy basis, exact scope, reason, impact review, authorization, audit and
          verification. Nothing here executes in this build, and an accepted request would never be shown as
          success — only authoritative verification is.
        </p>
      </Panel>

      {/* §18 S08-V08 Appeals */}
      <Panel
        title="Appeals"
        description="Structured independent review. A severe-action appeal is never reviewed by the sole original decision maker."
        count={kpis.appeals}
        flush
      >
        <DataTable
          columns={appealColumns}
          rows={appealRows}
          rowKey={(c) => c.case_id}
          onRowClick={openRow}
          pageSize={5}
          minWidth={1400}
          empty={{ icon: FiShield, title: "No appeal rights have arisen", description: "No case in scope carries an appeal." }}
        />
      </Panel>

      <div className="grid gap-4 xl:grid-cols-2">
        {/* §19 S08-V09 Repeat violations */}
        <Panel
          title="Repeat violations"
          description={`${focused.org} — decision context only, never an automatic strike system.`}
        >
          <dl>
            <DetailField label="Final adverse outcomes counted" value={String(focused.repeat.final_adverse)} />
            <DetailField label="Overturned outcomes excluded" value={String(focused.repeat.overturned_excluded)} />
            <DetailField label="Visible window" value={focused.repeat.window} />
          </dl>
          {focused.repeat.items.length === 0 ? (
            <p className={cx("mt-3 border-t pt-3 text-[13px]", CONSOLE.divider, CONSOLE.muted)}>
              {focused.repeat.note}
            </p>
          ) : (
            <>
              <ol className={cx("relative mt-3 space-y-3 border-t pl-6 pt-3", CONSOLE.divider)}>
                <span
                  className="absolute left-[5px] top-6 h-[calc(100%-30px)] w-px bg-slate-200 dark:bg-white/10"
                  aria-hidden="true"
                />
                {focused.repeat.items.map((r) => (
                  <li key={r.case_id} className="relative">
                    <span
                      className={cx(
                        "absolute -left-6 top-1.5 h-[11px] w-[11px] rounded-full ring-2 ring-white dark:ring-black",
                        r.counts ? "bg-rose-500" : "bg-slate-400"
                      )}
                      aria-hidden="true"
                    />
                    <div className="flex flex-wrap items-start justify-between gap-2">
                      <p className={cx("text-[13px] font-medium", CONSOLE.heading)}>
                        <span className={type.mono}>{r.case_id}</span> · {r.outcome}
                      </p>
                      <Badge tone={r.counts ? "danger" : "neutral"} size="sm">
                        {r.counts ? "Counted" : "Not counted"}
                      </Badge>
                    </div>
                    <p className={cx("text-[11px]", CONSOLE.faint)}>
                      {r.at} · {r.family}
                    </p>
                    <p className={cx("mt-0.5 text-[12px] leading-snug", CONSOLE.muted)}>{r.note}</p>
                  </li>
                ))}
              </ol>
              <p className={cx("mt-3 border-t pt-3 text-[12px] leading-snug", CONSOLE.divider, CONSOLE.faint)}>
                {focused.repeat.note}
              </p>
            </>
          )}
        </Panel>

        {/* §21 S08-V12 Post-enforcement verification + §22 communications */}
        <Panel
          title="Post-enforcement verification"
          description={`${focused.case_id} — an accepted request is not a verified outcome.`}
        >
          {focused.verification.length === 0 ? (
            <p className={cx("py-4 text-[13px]", CONSOLE.muted)}>
              No enforcement has executed on this case, so there is nothing to verify. The console will not
              show a verification result it has not observed.
            </p>
          ) : (
            <ol className="relative space-y-3 pl-6">
              <span
                className="absolute left-[5px] top-1.5 h-[calc(100%-12px)] w-px bg-slate-200 dark:bg-white/10"
                aria-hidden="true"
              />
              {focused.verification.map((v) => {
                const verified = v.result.startsWith("Verified");
                const pending = v.result.startsWith("Pending");
                return (
                  <li key={v.check} className="relative">
                    <span
                      className={cx(
                        "absolute -left-6 top-1.5 h-[11px] w-[11px] rounded-full ring-2 ring-white dark:ring-black",
                        verified ? "bg-green-500" : pending ? "bg-amber-500" : "bg-slate-400"
                      )}
                      aria-hidden="true"
                    />
                    <div className="flex flex-wrap items-start justify-between gap-2">
                      <p className={cx("text-[13px] font-medium", CONSOLE.heading)}>{v.check}</p>
                      <Badge tone={verified ? "success" : pending ? "warning" : "neutral"} size="sm">
                        {v.result.split(" · ")[0]}
                      </Badge>
                    </div>
                    <p className={cx("text-[11px] leading-snug", CONSOLE.faint)}>{v.expected}</p>
                    {v.at !== "—" && <p className={cx("text-[11px]", type.mono, CONSOLE.faint)}>{v.at}</p>}
                  </li>
                );
              })}
            </ol>
          )}

          <div className={cx("mt-4 border-t pt-3", CONSOLE.divider)}>
            <p className={cx("mb-1.5 text-[10px] font-semibold uppercase tracking-[0.14em]", CONSOLE.faint)}>
              Communication history
            </p>
            {focused.communications.length === 0 ? (
              <p className={cx("text-[12px]", CONSOLE.faint)}>No communication has been sent on this case.</p>
            ) : (
              <ul className={cx("divide-y", CONSOLE.divideY)}>
                {focused.communications.map((m) => (
                  <li key={`${m.kind}-${m.sent}`} className="flex flex-wrap items-start justify-between gap-2 py-2">
                    <div className="min-w-0">
                      <p className={cx("text-[12px] font-medium", CONSOLE.heading)}>{m.kind}</p>
                      <p className={cx("text-[11px]", CONSOLE.faint)}>
                        {m.to} · {m.sent}
                      </p>
                    </div>
                    <Badge tone={m.delivery === "Delivered" ? "success" : "warning"} size="sm">
                      {m.delivery}
                    </Badge>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </Panel>
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        <Panel title="What this surface is not">
          <ul className={cx("space-y-2 text-[13px]", CONSOLE.body)}>
            <li>
              <strong className={CONSOLE.heading}>Not an audience moderation dashboard.</strong> There is no
              chat, Q&amp;A, reactions, comments, audience uploads, audience report queue or Moderator
              Dashboard at launch. Every case here concerns tenant-originated broadcast content.
            </li>
            <li>
              <strong className={CONSOLE.heading}>Not a stream engineering console.</strong> Live Operations
              owns service and session health. Trust &amp; Safety requests authorized intervention through
              the authoritative control service.
            </li>
            <li>
              <strong className={CONSOLE.heading}>Not a legal determination.</strong> A received lawful
              request is recorded and routed. Validity, applicability and disposition belong to authorized
              Legal and Compliance personnel.
            </li>
            <li>
              <strong className={CONSOLE.heading}>Not a media operations tool.</strong> Media owns asset
              processing, preservation and retention. Trust &amp; Safety owns the case and the enforcement
              state that Media then executes.
            </li>
          </ul>
        </Panel>

        <Panel title="No automated guilt">
          <p className={cx("text-[13px] leading-[20px]", CONSOLE.body)}>
            Automated systems may create signals, prioritize review, extract evidence and recommend a next
            step. They may not silently convert confidence into a violation or a severe action.
          </p>
          <ul className={cx("mt-3 space-y-1.5 text-[13px]", CONSOLE.muted)}>
            <li>Signal confidence and reviewer confidence are separate fields and are never merged.</li>
            <li>Every final finding names a policy clause and the version in force at content time.</li>
            <li>Detector outputs retain their rule or model version so precision can be measured.</li>
            <li>Unresolved allegations never count as final violations in repeat-violation analysis.</li>
            <li>Overturned decisions are excluded from adverse counts but stay in the history.</li>
            <li>Provenance is a signal. Its presence or absence never proves content true, false, safe or harmful.</li>
          </ul>
        </Panel>
      </div>

      <Case360Drawer
        caseRecord={selected}
        open={Boolean(selected)}
        onClose={() => setSelected(null)}
        onRequestPreview={() => setPreviewOpen(true)}
        onProposeRestriction={() => setProposalOpen(true)}
      />

      <ProtectedPreviewDialog
        caseRecord={selected || focused}
        open={previewOpen}
        onClose={() => setPreviewOpen(false)}
        onGrant={(s) => {
          setGrant(s);
          setRemaining(s.minutes * 60);
        }}
      />

      <RestrictionProposalDialog
        caseRecord={(selected || focused)?.proposal ? selected || focused : CASES.find((c) => c.proposal)}
        open={proposalOpen}
        onClose={() => setProposalOpen(false)}
      />
    </ConsoleScreen>
  );
}
