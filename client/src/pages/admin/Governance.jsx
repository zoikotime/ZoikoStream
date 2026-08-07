import { useMemo, useState } from "react";
import { FiDownload, FiFileText, FiRefreshCw, FiSearch, FiShield, FiTrash2 } from "react-icons/fi";
import {
  Badge, Button, DataTable, DetailField, KpiCard, Panel, TabStrip, CONSOLE, cx, focusRing, type,
} from "../../components/admin";
import ConsoleScreen from "../../components/admin/ConsoleScreen";
import useInterval from "../../hooks/useInterval";
import DeletionPreviewDialog from "./DeletionPreviewDialog";
import PolicyRecordDrawer from "./PolicyRecordDrawer";
import {
  ACCESS_REVIEWS, ACTIONS, APPLICABILITY_TONE, ASSURANCE_TONE, BOARD_ITEMS, CONTROLS,
  DELETION_PREVIEW, DPIAS, DPIA_TONE, EVIDENCE, EVIDENCE_STATES, EVIDENCE_TONE, EXCEPTIONS,
  EXCEPTION_TONE, HISTORY, HOLD_TONE, JURISDICTIONS, LEGAL_HOLDS, OBLIGATIONS, OBLIGATION_TONE,
  ORGANIZATIONS, OWNERS, POLICIES, POLICY_TONE, PRIORITIES, PRIORITY_TONE, PRIVACY_REQUESTS,
  PRIVACY_TONE, PROCESSING_ACTIVITIES, RECORD_TYPES, REGIONS, RESIDENCY, RESIDENCY_TONE,
  RETENTION_POLICIES, RETENTION_PRECEDENCE, SUBPROCESSORS, SUBPROCESSOR_TONE, TIMING_TONE, label,
} from "./governanceData";

// Governance (ZST-WF-SA-GOV-001 · screen S14) — the accountability control plane. It turns legal,
// contractual, privacy, security and evidence obligations into controlled records with named
// owners, mapped controls, current evidence, known risk and verified outcomes.
//
// WHAT THIS BUILD IS: the canonical interface over a STATIC demonstration payload. §35 lists the
// Obligations Registry, Identity & Access, Audit, the data inventory, residency telemetry, the
// retention evaluator, the legal-hold service and the evidence store as build blockers. Nothing
// fetches and nothing mutates — and for this page in particular that is the honest state, because
// every destructive governance action here is defined to fail closed without a durable audit path.
//
// The rule that shapes the whole page is NO UNIVERSAL COMPLIANCE SCORE. There is no percentage
// anywhere: a single number hides expired evidence, high-severity exceptions, jurisdictional gaps
// and untested controls behind one reassuring figure. What replaces it is scoped counts on
// separate axes, each of which links to the records behind it.
//
// The second rule is FAIL-CLOSED DISPLAY. Residency UNKNOWN renders as a warning, never as
// compliant, and the interface distinguishes "no conflict detected" from "placement verified
// compliant". Not Tested and Not Applicable are likewise never rendered as Effective.

// Field skins come from the console tokens so a hover or focus change lands on every filter
// row at once, instead of being re-typed per page.
const inputCls = CONSOLE.search;
const selectCls = CONSOLE.select;

// §5 information architecture, one section per zone.
const SECTIONS = [
  { key: "queue", label: "Action queue" },
  { key: "obligations", label: "Obligations" },
  { key: "policy", label: "Policy library" },
  { key: "datamap", label: "Data map" },
  { key: "residency", label: "Residency" },
  { key: "retention", label: "Retention & deletion" },
  { key: "holds", label: "Legal holds" },
  { key: "privacy", label: "Privacy rights" },
  { key: "dpia", label: "DPIA" },
  { key: "subprocessors", label: "Subprocessors" },
  { key: "controls", label: "Control assurance" },
  { key: "exceptions", label: "Exceptions" },
  { key: "board", label: "Review board" },
  { key: "evidence", label: "Evidence" },
  { key: "access", label: "Access review" },
  { key: "history", label: "History" },
];

const DUE_HORIZONS = [
  ["all", "Any due date"],
  ["overdue", "Overdue"],
  ["24h", "Due in 24 hours"],
  ["7d", "Due in 7 days"],
  ["30d", "Due in 30 days"],
];

const PRIORITY_RANK = { CRITICAL: 0, HIGH: 1, MEDIUM: 2, LOW: 3 };
const TIMING_RANK = { OVERDUE: 0, WARNING: 1, ON_TRACK: 2 };

// §8.1 prioritization ladder, encoded as a rank so the queue's default order is the spec's order
// rather than whatever the data happened to be written in.
const TYPE_RANK = { Hold: 0, Residency: 1, Privacy: 2, DPIA: 3, Assurance: 4, Exception: 5, Retention: 6, Policy: 7, Obligation: 8 };

const regionLabel = (id) => REGIONS.find((r) => r.id === id)?.label || id;

// The state axis a queue row belongs to decides which tone map reads it. Collapsing them into one
// map would be the "single compliance status" this page is built to avoid.
const AXIS_TONE = {
  residency: RESIDENCY_TONE,
  hold: HOLD_TONE,
  privacy: PRIVACY_TONE,
  dpia: DPIA_TONE,
  assurance: ASSURANCE_TONE,
  exception: EXCEPTION_TONE,
  obligation: OBLIGATION_TONE,
};

function Pairs({ rows }) {
  return (
    <dl>
      {rows.map(([k, v]) => (
        <DetailField key={k} label={k} value={v} />
      ))}
    </dl>
  );
}

export default function Governance() {
  // §7 header filters
  const [q, setQ] = useState("");
  const [scope, setScope] = useState("all");
  const [org, setOrg] = useState("all");
  const [jurisdiction, setJurisdiction] = useState("all");
  const [recordType, setRecordType] = useState("all");
  const [owner, setOwner] = useState("all");
  const [priority, setPriority] = useState("all");
  const [due, setDue] = useState("all");
  const [evidenceState, setEvidenceState] = useState("all");
  const [risk, setRisk] = useState("all");

  const [section, setSection] = useState("queue");
  const [policy, setPolicy] = useState(null);
  const [deletionOpen, setDeletionOpen] = useState(false);
  const [releaseHold, setReleaseHold] = useState(null);

  const [ageSeconds, setAgeSeconds] = useState(0);
  useInterval(() => setAgeSeconds((s) => s + 1), 1000);

  const clearFilters = () => {
    setQ("");
    setScope("all");
    setOrg("all");
    setJurisdiction("all");
    setRecordType("all");
    setOwner("all");
    setPriority("all");
    setDue("all");
    setEvidenceState("all");
    setRisk("all");
  };

  const riskIds = useMemo(() => [...new Set(ACTIONS.flatMap((a) => a.risks))].sort(), []);

  const actions = useMemo(() => {
    const query = q.trim().toLowerCase();
    return ACTIONS.filter((a) => {
      if (query && !`${a.id} ${a.item} ${a.scope} ${a.owner} ${a.source_record_id}`.toLowerCase().includes(query)) return false;
      if (scope === "platform" && a.org !== "All Organizations") return false;
      if (scope === "organization" && a.org === "All Organizations") return false;
      if (org !== "all" && a.org !== org) return false;
      if (jurisdiction !== "all" && a.jurisdiction !== jurisdiction) return false;
      if (recordType !== "all" && a.record_type !== recordType) return false;
      if (owner !== "all" && a.owner !== owner) return false;
      if (priority !== "all" && a.priority !== priority) return false;
      if (evidenceState !== "all" && a.evidence_state !== evidenceState) return false;
      if (risk !== "all" && !a.risks.includes(risk)) return false;
      if (due === "overdue" && a.timing !== "OVERDUE") return false;
      if (due === "24h" && !["OVERDUE", "WARNING"].includes(a.timing)) return false;
      return true;
    }).sort(
      (a, b) =>
        PRIORITY_RANK[a.priority] - PRIORITY_RANK[b.priority] ||
        TYPE_RANK[a.record_type] - TYPE_RANK[b.record_type] ||
        TIMING_RANK[a.timing] - TIMING_RANK[b.timing]
    );
  }, [q, scope, org, jurisdiction, recordType, owner, priority, due, evidenceState, risk]);

  // Scoped coverage counts. Deliberately several separate figures rather than one score.
  const kpis = useMemo(
    () => ({
      criticalObligations: ACTIONS.filter((a) => a.priority === "CRITICAL").length,
      openExceptions: EXCEPTIONS.filter((e) => ["ACTIVE", "UNDER_REVIEW", "SUBMITTED"].includes(e.state)).length,
      expiringExceptions: EXCEPTIONS.filter((e) => e.state === "ACTIVE" && e.expires_at.includes("Aug")).length,
      privacyDue: PRIVACY_REQUESTS.filter((p) => ["WARNING", "OVERDUE"].includes(p.timing)).length,
      evidenceExpiring: EVIDENCE.filter((e) => ["EXPIRING", "EXPIRED"].includes(e.status)).length,
      evidenceExpired: EVIDENCE.filter((e) => e.status === "EXPIRED").length,
      residencyConflicts: RESIDENCY.filter((r) => r.posture === "CONFLICT").length,
      residencyUnknown: RESIDENCY.filter((r) => r.posture === "UNKNOWN").length,
      controlsNotEffective: CONTROLS.filter((c) => ["PARTIALLY_EFFECTIVE", "INEFFECTIVE"].includes(c.assurance_state)).length,
      controlsNotTested: CONTROLS.filter((c) => c.assurance_state === "NOT_TESTED").length,
    }),
    []
  );

  const actionColumns = [
    {
      key: "priority",
      header: "Priority",
      sortable: true,
      sortValue: (a) => PRIORITY_RANK[a.priority],
      render: (a) => (
        <Badge tone={PRIORITY_TONE[a.priority]} dot={a.priority === "CRITICAL"}>
          {label(a.priority)}
        </Badge>
      ),
    },
    {
      key: "item",
      header: "Item",
      sortable: true,
      render: (a) => (
        <div className="min-w-0 max-w-[380px]">
          <p className={cx("text-[13px] font-medium leading-snug", CONSOLE.heading)}>{a.item}</p>
          <p className={cx("text-[11px]", type.mono, CONSOLE.faint)}>
            {a.id} · {a.source_record_id}
          </p>
        </div>
      ),
    },
    { key: "record_type", header: "Type", sortable: true, render: (a) => <Badge tone="neutral">{a.record_type}</Badge> },
    {
      key: "scope",
      header: "Scope",
      render: (a) => (
        <div className="min-w-0 max-w-[240px]">
          <p className={cx("text-[12px] leading-snug", CONSOLE.body)}>{a.scope}</p>
          <p className={cx("text-[11px]", CONSOLE.faint)}>{a.jurisdiction}</p>
        </div>
      ),
    },
    {
      key: "state",
      header: "State",
      sortable: true,
      render: (a) => (
        <Badge tone={(AXIS_TONE[a.state_axis] || {})[a.state] || "neutral"}>{label(a.state)}</Badge>
      ),
    },
    {
      key: "evidence_state",
      header: "Evidence",
      sortable: true,
      render: (a) => <Badge tone={EVIDENCE_TONE[a.evidence_state]}>{label(a.evidence_state)}</Badge>,
    },
    {
      key: "due",
      header: "Due",
      sortable: true,
      sortValue: (a) => TIMING_RANK[a.timing],
      render: (a) => (
        <div className="min-w-0">
          <Badge tone={TIMING_TONE[a.timing]}>{label(a.timing)}</Badge>
          <p className={cx("mt-0.5 whitespace-nowrap text-[11px]", type.mono, CONSOLE.faint)}>{a.due_utc}</p>
        </div>
      ),
    },
    { key: "owner", header: "Owner", sortable: true, render: (a) => <span className={cx("text-[13px]", CONSOLE.body)}>{a.owner}</span> },
    {
      key: "dependency",
      header: "Dependency",
      render: (a) => (
        <span className={cx("block max-w-[220px] text-[12px] leading-snug", CONSOLE.muted)}>{a.dependency}</span>
      ),
    },
    {
      key: "action",
      header: "Action",
      align: "right",
      render: (a) => (
        <Button
          variant="secondary"
          size="sm"
          onClick={() => {
            const target = {
              Residency: "residency",
              Hold: "holds",
              Privacy: "privacy",
              DPIA: "dpia",
              Assurance: "controls",
              Exception: "exceptions",
              Retention: "retention",
              Policy: "policy",
              Obligation: "obligations",
            }[a.record_type];
            if (target) setSection(target);
          }}
          aria-label={`Open the authoritative record for ${a.id}`}
        >
          Open record
        </Button>
      ),
    },
  ];

  return (
    <ConsoleScreen
      title="Governance"
      subtitle="Evidence-backed accountability across policy, privacy, residency, retention and assurance. There is no single compliance score — a percentage would hide exactly what matters."
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
            title="Display only in this build — sensitive evidence export is purpose-, recipient-, scope- and audit-controlled"
          >
            Export
          </Button>
        </>
      }
    >
      {/* §7 header and filters */}
      <Panel title="Governance scope" flush>
        <div className="flex flex-wrap items-center gap-3 px-4 py-3">
          <div className="relative min-w-[240px] flex-1">
            <FiSearch className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" aria-hidden="true" />
            <input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="Search obligations, policies, holds, privacy requests, evidence or IDs…"
              aria-label="Search governance records"
              className={inputCls}
            />
          </div>
          <select value={scope} onChange={(e) => setScope(e.target.value)} className={selectCls} aria-label="Filter by scope">
            <option value="all">All scopes</option>
            <option value="platform">Platform</option>
            <option value="organization">Organization</option>
          </select>
          <select value={org} onChange={(e) => setOrg(e.target.value)} className={selectCls} aria-label="Filter by Organization">
            <option value="all">All Organizations</option>
            {ORGANIZATIONS.filter((o) => o !== "All Organizations").map((o) => (
              <option key={o} value={o}>
                {o}
              </option>
            ))}
          </select>
          <select
            value={jurisdiction}
            onChange={(e) => setJurisdiction(e.target.value)}
            className={selectCls}
            aria-label="Filter by jurisdiction"
            title="A governed applicability dimension. Selecting it does not assert legal applicability."
          >
            <option value="all">Any jurisdiction</option>
            {JURISDICTIONS.map((j) => (
              <option key={j} value={j}>
                {j}
              </option>
            ))}
          </select>
          <select value={recordType} onChange={(e) => setRecordType(e.target.value)} className={selectCls} aria-label="Filter by record type">
            <option value="all">All record types</option>
            {RECORD_TYPES.map((t) => (
              <option key={t} value={t}>
                {t}
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
          <select value={priority} onChange={(e) => setPriority(e.target.value)} className={selectCls} aria-label="Filter by priority">
            <option value="all">All priorities</option>
            {PRIORITIES.map((p) => (
              <option key={p} value={p}>
                {label(p)}
              </option>
            ))}
          </select>
          <select value={due} onChange={(e) => setDue(e.target.value)} className={selectCls} aria-label="Filter by due horizon">
            {DUE_HORIZONS.map(([v, l]) => (
              <option key={v} value={v}>
                {l}
              </option>
            ))}
          </select>
          <select
            value={evidenceState}
            onChange={(e) => setEvidenceState(e.target.value)}
            className={selectCls}
            aria-label="Filter by evidence state"
          >
            <option value="all">Any evidence state</option>
            {EVIDENCE_STATES.map((s) => (
              <option key={s} value={s}>
                {label(s)}
              </option>
            ))}
          </select>
          <select value={risk} onChange={(e) => setRisk(e.target.value)} className={selectCls} aria-label="Filter by enterprise risk reference">
            <option value="all">Any risk reference</option>
            {riskIds.map((r) => (
              <option key={r} value={r}>
                {r}
              </option>
            ))}
          </select>
          <span className={cx("ml-auto text-[11px] leading-snug", CONSOLE.faint)}>
            Jurisdiction is a governed applicability dimension. Selecting it never asserts legal applicability.
          </span>
        </div>
      </Panel>

      {/* §7.1 KPI strip — scoped counts on separate axes, never one score. */}
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-3 xl:grid-cols-6">
        <KpiCard
          label="Critical obligations"
          value={kpis.criticalObligations}
          note="Active obligations with overdue or blocked critical actions"
          tone={kpis.criticalObligations ? "text-rose-600 dark:text-rose-400" : undefined}
          pressed={priority === "CRITICAL"}
          onClick={() => {
            setPriority(priority === "CRITICAL" ? "all" : "CRITICAL");
            setSection("queue");
          }}
        />
        <KpiCard
          label="Open exceptions"
          value={kpis.openExceptions}
          note={`${kpis.expiringExceptions} expiring this month · no silent auto-renewal`}
          tone={kpis.openExceptions ? "text-amber-600 dark:text-amber-400" : undefined}
          pressed={section === "exceptions"}
          onClick={() => setSection("exceptions")}
        />
        <KpiCard
          label="Privacy requests due"
          value={kpis.privacyDue}
          note="Inside the warning window or overdue · timing is separate from workflow state"
          tone={kpis.privacyDue ? "text-amber-600 dark:text-amber-400" : undefined}
          pressed={section === "privacy"}
          onClick={() => setSection("privacy")}
        />
        <KpiCard
          label="Evidence expiring"
          value={kpis.evidenceExpiring}
          note={`${kpis.evidenceExpired} already expired · expired evidence cannot support assurance`}
          tone={kpis.evidenceExpired ? "text-rose-600 dark:text-rose-400" : "text-amber-600 dark:text-amber-400"}
          pressed={section === "evidence"}
          onClick={() => setSection("evidence")}
        />
        <KpiCard
          label="Residency conflicts"
          value={kpis.residencyConflicts}
          note={`${kpis.residencyUnknown} unknown — unknown is never shown as compliant`}
          tone={kpis.residencyConflicts ? "text-rose-600 dark:text-rose-400" : undefined}
          pressed={section === "residency"}
          onClick={() => setSection("residency")}
        />
        <KpiCard
          label="Controls not effective"
          value={kpis.controlsNotEffective}
          note={`${kpis.controlsNotTested} not tested · not tested is not effective`}
          tone={kpis.controlsNotEffective ? "text-rose-600 dark:text-rose-400" : undefined}
          pressed={section === "controls"}
          onClick={() => setSection("controls")}
        />
      </div>

      <TabStrip tabs={SECTIONS} active={section} onChange={setSection} label="Governance sections" idPrefix="gov" />

      <div role="tabpanel" id={`govpanel-${section}`} aria-labelledby={`gov-${section}`} tabIndex={-1} className="space-y-4">
        {/* S14-V01 */}
        {section === "queue" && (
          <Panel
            title="Governance action queue"
            description="Hold conflicts first, then residency conflicts on live data, overdue privacy requests, blocked DPIAs, expired evidence, expiring high-impact exceptions, ineffective controls, then policy changes inside the implementation window."
            count={kpis.criticalObligations}
            flush
          >
            <DataTable
              columns={actionColumns}
              rows={actions}
              rowKey={(a) => a.id}
              pageSize={8}
              minWidth={1700}
              empty={{
                icon: FiShield,
                title: "No governance actions match these filters",
                description: "No item in scope satisfies every active filter.",
                action: (
                  <Button variant="secondary" size="sm" onClick={clearFilters}>
                    Clear filters
                  </Button>
                ),
              }}
            />
          </Panel>
        )}

        {/* S14-V02 */}
        {section === "obligations" && (
          <>
            <Panel
              title="Obligations register"
              description="External and internal requirements with recorded applicability and accountable implementation."
              flush
            >
              <DataTable
                columns={[
                  {
                    key: "obligation_id",
                    header: "Obligation",
                    sortable: true,
                    render: (o) => (
                      <div className="min-w-0 max-w-[300px]">
                        <p className={cx("text-[13px] font-medium leading-snug", CONSOLE.heading)}>{o.title}</p>
                        <p className={cx("text-[11px]", type.mono, CONSOLE.faint)}>{o.obligation_id}</p>
                      </div>
                    ),
                  },
                  {
                    key: "source_type",
                    header: "Source",
                    sortable: true,
                    render: (o) => (
                      <div className="min-w-0 max-w-[240px]">
                        <p className={cx("text-[12px]", CONSOLE.body)}>{o.source_type}</p>
                        <p className={cx("text-[11px] leading-snug", CONSOLE.faint)}>{o.source_reference}</p>
                      </div>
                    ),
                  },
                  { key: "jurisdiction", header: "Jurisdiction", sortable: true },
                  {
                    key: "applicability_status",
                    header: "Applicability",
                    sortable: true,
                    render: (o) => (
                      <div className="min-w-0">
                        <Badge tone={APPLICABILITY_TONE[o.applicability_status]}>
                          {o.applicability_status === "PROPOSED" ? "Suggested" : label(o.applicability_status)}
                        </Badge>
                        <p className={cx("mt-0.5 max-w-[200px] text-[11px] leading-snug", CONSOLE.faint)}>
                          {o.applicability_owner}
                        </p>
                      </div>
                    ),
                  },
                  {
                    key: "lifecycle",
                    header: "Lifecycle",
                    sortable: true,
                    render: (o) => <Badge tone={OBLIGATION_TONE[o.lifecycle]}>{label(o.lifecycle)}</Badge>,
                  },
                  {
                    key: "scope",
                    header: "Scope",
                    render: (o) => <span className={cx("block max-w-[240px] text-[12px] leading-snug", CONSOLE.muted)}>{o.scope}</span>,
                  },
                  {
                    key: "controls",
                    header: "Controls",
                    render: (o) => (
                      <span className={cx("text-[12px]", type.mono, CONSOLE.body)}>{o.controls.join(", ") || "—"}</span>
                    ),
                  },
                  {
                    key: "coverage",
                    header: "Evidence coverage",
                    render: (o) => <span className={cx("block max-w-[220px] text-[12px] leading-snug", CONSOLE.muted)}>{o.coverage}</span>,
                  },
                  {
                    key: "risk_ids",
                    header: "Risks",
                    render: (o) => <span className={cx("text-[12px]", type.mono, CONSOLE.body)}>{o.risk_ids.join(", ") || "—"}</span>,
                  },
                  { key: "owner", header: "Owner", sortable: true },
                  {
                    key: "review_by",
                    header: "Review by",
                    render: (o) => <span className={cx("whitespace-nowrap text-[12px]", CONSOLE.body)}>{o.review_by}</span>,
                  },
                ]}
                rows={OBLIGATIONS.filter(
                  (o) =>
                    (jurisdiction === "all" || o.jurisdiction === jurisdiction) &&
                    (owner === "all" || o.owner === owner) &&
                    (!q.trim() || `${o.obligation_id} ${o.title} ${o.requirement}`.toLowerCase().includes(q.trim().toLowerCase()))
                )}
                rowKey={(o) => o.obligation_id}
                pageSize={6}
                minWidth={1900}
                empty={{ icon: FiFileText, title: "No obligations match", description: "Clear the filters to see the full register." }}
              />
            </Panel>
            <Panel title="Applicability control">
              <p className={cx("text-[13px] leading-[20px]", CONSOLE.body)}>
                Automated regulatory intelligence may suggest a potentially applicable obligation. Only an
                authorized Legal or Governance owner may confirm applicability, and suggested obligations
                are visibly distinct from confirmed ones — a feed item is never rendered as an active duty.
              </p>
              <p className={cx("mt-3 border-t pt-3 text-[12px] leading-snug", CONSOLE.divider, CONSOLE.faint)}>
                The status summary on each obligation is derived from its control and evidence posture. It
                is never a free-form green status typed in by hand.
              </p>
            </Panel>
          </>
        )}

        {/* S14-V03 */}
        {section === "policy" && (
          <>
            <div className="grid gap-3 lg:grid-cols-2 xl:grid-cols-3">
              {POLICIES.map((p) => (
                <button
                  key={p.policy_id}
                  type="button"
                  onClick={() => setPolicy(p)}
                  className={cx(CONSOLE.panel, CONSOLE.panelHover, focusRing, "p-4 text-left transition duration-150 motion-reduce:transition-none")}
                >
                  <span className="flex flex-wrap items-start justify-between gap-2">
                    <span className={cx("text-[13px] font-semibold leading-snug", CONSOLE.heading)}>{p.title}</span>
                    <Badge tone={POLICY_TONE[p.status]} dot>
                      {label(p.status)}
                    </Badge>
                  </span>
                  <span className={cx("mt-1 block text-[11px]", type.mono, CONSOLE.faint)}>
                    {p.policy_id} · {p.version}
                  </span>
                  <dl className="mt-2">
                    <DetailField label="Owner" value={p.owner} />
                    <DetailField label="Effective" value={p.effective} />
                    <DetailField label="Versions" value={`${p.versions.length} on record`} />
                    <DetailField label="Next review" value={p.next_review} />
                  </dl>
                  <span className={cx("mt-2 block text-[12px] font-semibold", CONSOLE.link)}>Open policy record →</span>
                </button>
              ))}
            </div>
            <Panel title="Policy versioning rule">
              <p className={cx("text-[13px] leading-[20px]", CONSOLE.body)}>
                A policy is never edited in place. Every change creates a new immutable version, the prior
                version is marked Superseded, and the history is preserved — so the version in force at the
                time something happened stays recoverable for an assessment, a decision or an audit.
              </p>
            </Panel>
          </>
        )}

        {/* S14-V04 */}
        {section === "datamap" && (
          <>
            <Panel
              title="Data map / record of processing activities"
              description="A governance projection of actual processing, reconciled to the authoritative service and data inventory."
              flush
            >
              <DataTable
                columns={[
                  {
                    key: "name",
                    header: "Processing activity",
                    sortable: true,
                    render: (a) => (
                      <div className="min-w-0 max-w-[260px]">
                        <p className={cx("text-[13px] font-medium leading-snug", CONSOLE.heading)}>{a.name}</p>
                        <p className={cx("text-[11px]", type.mono, CONSOLE.faint)}>{a.id}</p>
                        <p className={cx("mt-0.5 text-[11px] leading-snug", CONSOLE.muted)}>{a.purpose}</p>
                      </div>
                    ),
                  },
                  { key: "org_role", header: "Role", sortable: true },
                  {
                    key: "data_categories",
                    header: "Data categories",
                    render: (a) => (
                      <div className="min-w-0 max-w-[240px]">
                        <p className={cx("text-[12px] leading-snug", CONSOLE.body)}>{a.data_categories}</p>
                        <p className={cx("mt-0.5 text-[11px] leading-snug", CONSOLE.faint)}>{a.sensitive}</p>
                      </div>
                    ),
                  },
                  {
                    key: "systems",
                    header: "Systems",
                    render: (a) => (
                      <div className="min-w-0 max-w-[200px]">
                        <p className={cx("text-[12px] leading-snug", CONSOLE.body)}>{a.systems}</p>
                        <p className={cx("text-[11px]", CONSOLE.faint)}>{a.lifecycle_stage}</p>
                      </div>
                    ),
                  },
                  {
                    key: "regions",
                    header: "Regions",
                    render: (a) => (
                      <span className={cx("block max-w-[200px] text-[12px] leading-snug", CONSOLE.body)}>
                        {a.regions.map(regionLabel).join(", ")}
                      </span>
                    ),
                  },
                  {
                    key: "subprocessors",
                    header: "Subprocessors",
                    render: (a) => (
                      <div className="min-w-0 max-w-[200px]">
                        <p className={cx("text-[12px]", type.mono, CONSOLE.body)}>{a.subprocessors.join(", ") || "None"}</p>
                        <p className={cx("text-[11px] leading-snug", CONSOLE.faint)}>{a.transfer_mechanism}</p>
                      </div>
                    ),
                  },
                  {
                    key: "retention_policy",
                    header: "Retention",
                    render: (a) => <span className={cx("block max-w-[180px] text-[12px] leading-snug", CONSOLE.body)}>{a.retention_policy}</span>,
                  },
                  {
                    key: "dpia",
                    header: "DPIA",
                    sortable: true,
                    render: (a) => (
                      <Badge tone={a.dpia.includes("BLOCKED") ? "danger" : a.dpia.includes("conditions") ? "warning" : "success"}>
                        {a.dpia}
                      </Badge>
                    ),
                  },
                  {
                    key: "evidence_freshness",
                    header: "Reconciliation",
                    render: (a) => (
                      <span className={cx("block max-w-[220px] text-[11px] leading-snug", CONSOLE.faint)}>{a.evidence_freshness}</span>
                    ),
                  },
                ]}
                rows={PROCESSING_ACTIVITIES}
                rowKey={(a) => a.id}
                pageSize={6}
                minWidth={2000}
                empty={{ icon: FiFileText, title: "No processing activities recorded" }}
              />
            </Panel>
            <Panel title="Why this is not a spreadsheet">
              <p className={cx("text-[13px] leading-[20px]", CONSOLE.body)}>
                Each activity carries the timestamp of its last reconciliation against the authoritative
                service and data inventory. A record that has drifted from the systems it describes is worse
                than no record, because it is trusted.
              </p>
            </Panel>
          </>
        )}

        {/* S14-V05 */}
        {section === "residency" && (
          <>
            <div className="grid gap-3 lg:grid-cols-2 xl:grid-cols-3">
              {RESIDENCY.map((r) => (
                <div key={r.id} className={cx(CONSOLE.panel, CONSOLE.panelHover, "transition-colors duration-150 motion-reduce:transition-none", "p-4")}>
                  <div className="flex flex-wrap items-start justify-between gap-2">
                    <div className="min-w-0">
                      <p className={cx("text-[13px] font-semibold", CONSOLE.heading)}>{r.org}</p>
                      <p className={cx("text-[11px]", type.mono, CONSOLE.faint)}>
                        {r.id} · {r.activity}
                      </p>
                    </div>
                    <Badge tone={RESIDENCY_TONE[r.posture]} dot={["CONFLICT", "UNKNOWN"].includes(r.posture)}>
                      {label(r.posture)}
                    </Badge>
                  </div>
                  <dl className="mt-2">
                    <DetailField label="Required footprint" value={r.required} />
                    <DetailField label="Observed processing" value={r.observed_processing} />
                    <DetailField label="Observed storage" value={r.observed_storage} />
                    <DetailField label="Subprocessor locations" value={r.subprocessor_locations} />
                    <DetailField label="Transfer record" value={r.transfer_record} />
                    <DetailField label="Telemetry freshness" value={r.telemetry_freshness} />
                    <DetailField label="Exception" value={r.exception} />
                    <DetailField label="Remediation" value={r.remediation} />
                  </dl>
                  <p
                    className={cx(
                      "mt-2 rounded-lg border px-3 py-2 text-[12px] leading-snug",
                      r.posture === "CONFLICT"
                        ? "border-rose-200 bg-rose-50 text-rose-700 dark:border-rose-500/25 dark:bg-rose-500/10 dark:text-rose-300"
                        : r.posture === "UNKNOWN"
                          ? "border-amber-200 bg-amber-50 text-amber-800 dark:border-amber-500/25 dark:bg-amber-500/10 dark:text-amber-200"
                          : cx(CONSOLE.inset, CONSOLE.muted)
                    )}
                  >
                    {r.note}
                  </p>
                </div>
              ))}
            </div>
            <Panel title="Fail-closed residency display">
              <p className={cx("text-[13px] leading-[20px]", CONSOLE.body)}>
                Unknown or stale placement evidence is never shown as compliant. The interface distinguishes
                &ldquo;no conflict detected&rdquo; from &ldquo;placement verified compliant&rdquo;, because
                telemetry that cannot prove a location is not evidence of a permitted one.
              </p>
              <p className={cx("mt-3 border-t pt-3 text-[12px] leading-snug", CONSOLE.divider, CONSOLE.faint)}>
                Remediation executes in the authoritative infrastructure and data services, never from this
                console. Governance records the requirement, the conflict and the decision.
              </p>
            </Panel>
          </>
        )}

        {/* S14-V06 */}
        {section === "retention" && (
          <>
            <Panel title="Retention precedence" description="The interface always shows which rule won, and why.">
              <ol className={cx("list-inside list-decimal space-y-1 text-[13px]", CONSOLE.muted)}>
                {RETENTION_PRECEDENCE.map((p) => (
                  <li key={p}>{p}</li>
                ))}
              </ol>
              <p className={cx("mt-3 border-t pt-3 text-[12px] leading-snug", CONSOLE.divider, CONSOLE.faint)}>
                A conflict between sources renders as an explicit conflict state. Destructive execution stays
                blocked until the authoritative governance service resolves it.
              </p>
            </Panel>
            <Panel title="Retention policies" flush>
              <DataTable
                columns={[
                  { key: "id", header: "Policy", sortable: true, mono: true },
                  { key: "scope", header: "Scope", render: (r) => <span className={cx("block max-w-[280px] text-[12px] leading-snug", CONSOLE.body)}>{r.scope}</span> },
                  { key: "trigger", header: "Trigger", sortable: true },
                  { key: "duration", header: "Duration", render: (r) => <span className={cx("text-[12px]", CONSOLE.body)}>{r.duration}</span> },
                  { key: "action", header: "Action", render: (r) => <Badge tone="neutral">{r.action}</Badge> },
                  { key: "winning_rule", header: "Winning rule", render: (r) => <span className={cx("block max-w-[240px] text-[12px] leading-snug", CONSOLE.muted)}>{r.winning_rule}</span> },
                  { key: "exceptions", header: "Exceptions", render: (r) => <span className={cx("block max-w-[200px] text-[12px] leading-snug", CONSOLE.muted)}>{r.exceptions}</span> },
                  { key: "verification", header: "Verification", render: (r) => <span className={cx("text-[12px]", CONSOLE.body)}>{r.verification}</span> },
                  { key: "effective_version", header: "Effective version", render: (r) => <span className={cx("block max-w-[220px] text-[11px] leading-snug", CONSOLE.faint)}>{r.effective_version}</span> },
                ]}
                rows={RETENTION_POLICIES}
                rowKey={(r) => r.id}
                pageSize={5}
                minWidth={1700}
                empty={{ icon: FiFileText, title: "No retention policies recorded" }}
              />
            </Panel>
            <Panel title="Deletion control" description="Governance authorizes the rule. Authoritative services perform the deletion.">
              <ul className={cx("space-y-1.5 text-[13px]", CONSOLE.muted)}>
                <li>The deletion preview lists data classes, systems, Organizations, regions, dependencies, holds and irreversibility.</li>
                <li>Legal holds always block conflicting deletion.</li>
                <li>Bulk and cross-tenant destructive changes require dual authorization.</li>
                <li>Success requires authoritative completion and verification — job acceptance is not completion.</li>
                <li>A failed or partial deletion stays open with its residual locations and a remediation owner named.</li>
              </ul>
              <Button variant="secondary" size="sm" leftIcon={FiTrash2} className="mt-3" onClick={() => setDeletionOpen(true)}>
                Open deletion preview
              </Button>
            </Panel>
          </>
        )}

        {/* S14-V07 */}
        {section === "holds" && (
          <>
            <Panel title="Legal holds" description="Preservation controls. The interface prevents accidental release, hidden scope reduction and deletion conflict." flush>
              <DataTable
                columns={[
                  {
                    key: "hold_id",
                    header: "Hold",
                    sortable: true,
                    render: (h) => (
                      <div className="min-w-0">
                        <p className={cx("text-[12px] font-semibold", type.mono, CONSOLE.heading)}>{h.hold_id}</p>
                        <p className={cx("text-[11px]", CONSOLE.faint)}>{h.org}</p>
                      </div>
                    ),
                  },
                  {
                    key: "authority",
                    header: "Authority / basis",
                    render: (h) => (
                      <div className="min-w-0 max-w-[240px]">
                        <p className={cx("text-[12px] leading-snug", CONSOLE.body)}>{h.authority}</p>
                        <p className={cx("text-[11px]", type.mono, CONSOLE.faint)}>{h.basis}</p>
                      </div>
                    ),
                  },
                  { key: "scope", header: "Scope", render: (h) => <span className={cx("block max-w-[280px] text-[12px] leading-snug", CONSOLE.body)}>{h.scope}</span> },
                  { key: "state", header: "State", sortable: true, render: (h) => <Badge tone={HOLD_TONE[h.state]} dot={h.state === "ACTIVE"}>{label(h.state)}</Badge> },
                  { key: "conflicts", header: "Conflicts", render: (h) => <span className={cx("block max-w-[280px] text-[12px] leading-snug", CONSOLE.muted)}>{h.conflicts}</span> },
                  { key: "custodians", header: "Custodians", render: (h) => <span className={cx("block max-w-[220px] text-[12px] leading-snug", CONSOLE.muted)}>{h.custodians}</span> },
                  { key: "placed", header: "Placed", render: (h) => (
                    <div className="min-w-0">
                      <p className={cx("whitespace-nowrap text-[12px]", CONSOLE.body)}>{h.placed_local}</p>
                      <p className={cx("whitespace-nowrap text-[11px]", type.mono, CONSOLE.faint)}>{h.placed_utc}</p>
                      <p className={cx("text-[11px]", CONSOLE.faint)}>{h.placed_by}</p>
                    </div>
                  ) },
                  { key: "review_date", header: "Review date", sortable: true, render: (h) => <span className={cx("whitespace-nowrap text-[12px]", CONSOLE.body)}>{h.review_date}</span> },
                  {
                    key: "action",
                    header: "Action",
                    align: "right",
                    render: (h) => (
                      <Button
                        variant="secondary"
                        size="sm"
                        disabled={h.state === "RELEASED"}
                        onClick={() => setReleaseHold(h)}
                        aria-label={`Open release workflow for ${h.hold_id}`}
                      >
                        Release…
                      </Button>
                    ),
                  },
                ]}
                rows={LEGAL_HOLDS.filter((h) => org === "all" || h.org === org)}
                rowKey={(h) => h.hold_id}
                pageSize={5}
                minWidth={1900}
                empty={{ icon: FiShield, title: "No legal holds recorded in this scope" }}
              />
            </Panel>
            <Panel title="Legal-hold release">
              <p className={cx("text-[13px] leading-[20px]", CONSOLE.body)}>
                Releasing a hold can expose preserved data to deletion. Release requires dual authorization,
                an impact preview, step-up authentication, immutable audit, and a post-release re-evaluation
                of the retention and deletion rules that then govern. A single actor cannot release a hold,
                and Media cannot remove or weaken one at all.
              </p>
            </Panel>
          </>
        )}

        {/* S14-V08 */}
        {section === "privacy" && (
          <>
            <Panel title="Privacy rights requests" description="Request state is separate from deadline state. A request cannot close while an authoritative source action is incomplete." flush>
              <DataTable
                columns={[
                  {
                    key: "id",
                    header: "Request",
                    sortable: true,
                    render: (p) => (
                      <div className="min-w-0">
                        <p className={cx("text-[12px] font-semibold", type.mono, CONSOLE.heading)}>{p.id}</p>
                        <p className={cx("text-[11px]", CONSOLE.faint)}>{p.org}</p>
                      </div>
                    ),
                  },
                  { key: "type", header: "Type", sortable: true, render: (p) => <Badge tone="neutral">{p.type}</Badge> },
                  { key: "jurisdiction", header: "Jurisdiction", sortable: true },
                  { key: "state", header: "Request state", sortable: true, render: (p) => <Badge tone={PRIVACY_TONE[p.state]}>{label(p.state)}</Badge> },
                  {
                    key: "timing",
                    header: "Deadline state",
                    sortable: true,
                    render: (p) => (
                      <div className="min-w-0">
                        <Badge tone={TIMING_TONE[p.timing]}>{label(p.timing)}</Badge>
                        <p className={cx("mt-0.5 whitespace-nowrap text-[11px]", type.mono, CONSOLE.faint)}>{p.due_utc}</p>
                      </div>
                    ),
                  },
                  { key: "identity_verification", header: "Identity verification", render: (p) => <span className={cx("block max-w-[220px] text-[12px] leading-snug", CONSOLE.body)}>{p.identity_verification}</span> },
                  { key: "scope", header: "Scope", render: (p) => <span className={cx("block max-w-[240px] text-[12px] leading-snug", CONSOLE.muted)}>{p.scope}</span> },
                  {
                    key: "sources",
                    header: "Source actions",
                    render: (p) => (
                      <ul className="min-w-0 max-w-[300px] space-y-0.5">
                        {p.sources.map((s) => (
                          <li key={s.system} className={cx("text-[11px] leading-snug", CONSOLE.body)}>
                            <span className={CONSOLE.heading}>{s.system}</span> — {s.result}
                          </li>
                        ))}
                      </ul>
                    ),
                  },
                  { key: "holds", header: "Holds", render: (p) => <span className={cx("block max-w-[220px] text-[12px] leading-snug", CONSOLE.muted)}>{p.holds}</span> },
                  { key: "reason_code", header: "Reason code", render: (p) => <span className={cx("block max-w-[200px] text-[12px] leading-snug", CONSOLE.body)}>{p.reason_code}</span> },
                ]}
                rows={PRIVACY_REQUESTS.filter((p) => org === "all" || p.org === org)}
                rowKey={(p) => p.id}
                pageSize={5}
                minWidth={2000}
                empty={{ icon: FiFileText, title: "No privacy requests in this scope" }}
              />
            </Panel>
            <Panel title="Request controls">
              <ul className={cx("space-y-1.5 text-[13px]", CONSOLE.muted)}>
                <li>Identity verification is proportionate and accessibility-compliant.</li>
                <li>Deadline calculation is jurisdiction- and policy-controlled, and shown as exact dates.</li>
                <li>Cross-Organization data is excluded unless the verified request scope lawfully includes it.</li>
                <li>Legal holds and preservation constraints are visible before any deletion.</li>
                <li>Response packages are access-controlled and time-limited where supported.</li>
                <li>Denied or partially fulfilled requests carry a controlled reason code and a review.</li>
                <li>Every fulfillment action correlates to the authoritative source system and to Audit.</li>
              </ul>
            </Panel>
          </>
        )}

        {/* S14-V09 */}
        {section === "dpia" && (
          <>
            {DPIAS.map((d) => (
              <Panel
                key={d.id}
                title={`${d.id} · ${d.name}`}
                description={`${d.activity} · owner ${d.owner} · decision owner ${d.decision_owner}`}
                action={<Badge tone={DPIA_TONE[d.state]} dot={d.state === "BLOCKED"}>{label(d.state)}</Badge>}
              >
                <ol className="relative space-y-3 pl-6">
                  <span className="absolute left-[5px] top-1.5 h-[calc(100%-12px)] w-px bg-slate-200 dark:bg-white/10" aria-hidden="true" />
                  {d.stages.map((s) => (
                    <li key={s.stage} className="relative">
                      <span
                        className={cx(
                          "absolute -left-6 top-1.5 h-[11px] w-[11px] rounded-full ring-2 ring-white dark:ring-black",
                          s.done ? "bg-green-500" : "bg-slate-300 dark:bg-white/20"
                        )}
                        aria-hidden="true"
                      />
                      <p className={cx("text-[13px] font-medium", CONSOLE.heading)}>
                        {s.stage} {!s.done && <span className={cx("font-normal", CONSOLE.faint)}>· not complete</span>}
                      </p>
                      <p className={cx("mt-0.5 text-[12px] leading-snug", CONSOLE.muted)}>{s.output}</p>
                    </li>
                  ))}
                </ol>

                {d.risks.length > 0 && (
                  <div className={cx("mt-4 border-t pt-3", CONSOLE.divider)}>
                    <p className={cx("mb-2 text-[10px] font-semibold uppercase tracking-[0.14em]", CONSOLE.faint)}>
                      Risks to individuals
                    </p>
                    <div className="grid gap-2 sm:grid-cols-2">
                      {d.risks.map((r) => (
                        <div key={r.risk} className={cx(CONSOLE.inset, CONSOLE.panelHover, "transition-colors duration-150 motion-reduce:transition-none", "p-3")}>
                          <p className={cx("text-[12px] font-medium leading-snug", CONSOLE.heading)}>{r.risk}</p>
                          <p className={cx("mt-1 text-[11px]", CONSOLE.faint)}>
                            Likelihood {r.likelihood} · severity {r.severity}
                          </p>
                          <p className={cx("text-[11px] leading-snug", CONSOLE.muted)}>Affected: {r.groups}</p>
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {d.mitigations.length > 0 && (
                  <div className={cx("mt-3 border-t pt-3", CONSOLE.divider)}>
                    <p className={cx("mb-2 text-[10px] font-semibold uppercase tracking-[0.14em]", CONSOLE.faint)}>
                      Mitigations and residual risk
                    </p>
                    <div className="grid gap-2 sm:grid-cols-2">
                      {d.mitigations.map((m) => (
                        <div key={m.mitigation} className={cx(CONSOLE.inset, CONSOLE.panelHover, "transition-colors duration-150 motion-reduce:transition-none", "p-3")}>
                          <p className={cx("text-[12px] font-medium leading-snug", CONSOLE.heading)}>{m.mitigation}</p>
                          <p className={cx("mt-1 text-[11px]", CONSOLE.faint)}>{m.state}</p>
                          <p className={cx("text-[11px] leading-snug", CONSOLE.muted)}>{m.effect}</p>
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {d.conditions.length > 0 && (
                  <div className={cx("mt-3 border-t pt-3", CONSOLE.divider)}>
                    <p className={cx("mb-2 text-[10px] font-semibold uppercase tracking-[0.14em]", CONSOLE.faint)}>
                      Tracked conditions
                    </p>
                    <ul className={cx("divide-y", CONSOLE.divideY)}>
                      {d.conditions.map((cn) => (
                        <li key={cn.condition} className="flex flex-wrap items-start justify-between gap-2 py-2">
                          <span className={cx("min-w-0 text-[12px]", CONSOLE.body)}>{cn.condition}</span>
                          <span className={cx("shrink-0 text-[11px]", CONSOLE.faint)}>
                            due {cn.due} · {cn.state}
                          </span>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}

                <p className={cx("mt-3 border-t pt-3 text-[12px] leading-snug", CONSOLE.divider, CONSOLE.faint)}>
                  {d.note}
                </p>
              </Panel>
            ))}
            <Panel title="No launch-by-deadline override">
              <p className={cx("text-[13px] leading-[20px]", CONSOLE.body)}>
                A blocked DPIA cannot become Approved because a launch date approaches. The governed options
                are risk reduction, scope change, consultation or escalation, and deferral. Nothing in this
                interface offers a fifth.
              </p>
            </Panel>
          </>
        )}

        {/* S14-V10 */}
        {section === "subprocessors" && (
          <>
            <Panel title="Subprocessors and third parties" flush>
              <DataTable
                columns={[
                  {
                    key: "entity",
                    header: "Vendor",
                    sortable: true,
                    render: (s) => (
                      <div className="min-w-0 max-w-[220px]">
                        <p className={cx("text-[13px] font-medium leading-snug", CONSOLE.heading)}>{s.entity}</p>
                        <p className={cx("text-[11px]", type.mono, CONSOLE.faint)}>{s.id}</p>
                        <p className={cx("text-[11px] leading-snug", CONSOLE.muted)}>{s.service}</p>
                      </div>
                    ),
                  },
                  { key: "purpose", header: "Purpose", render: (s) => <span className={cx("block max-w-[220px] text-[12px] leading-snug", CONSOLE.body)}>{s.purpose}</span> },
                  { key: "data_scope", header: "Data scope", render: (s) => <span className={cx("block max-w-[240px] text-[12px] leading-snug", CONSOLE.body)}>{s.data_scope}</span> },
                  { key: "regions", header: "Regions", sortable: true, render: (s) => <span className={cx("block max-w-[180px] text-[12px] leading-snug", CONSOLE.body)}>{s.regions}</span> },
                  { key: "contract_status", header: "Contract", render: (s) => <span className={cx("block max-w-[240px] text-[12px] leading-snug", CONSOLE.muted)}>{s.contract_status}</span> },
                  {
                    key: "evidence",
                    header: "Evidence",
                    render: (s) => (
                      <div className="min-w-0 max-w-[320px]">
                        <Badge tone={EVIDENCE_TONE[s.evidence_state]}>{label(s.evidence_state)}</Badge>
                        <p className={cx("mt-0.5 text-[11px] leading-snug", CONSOLE.muted)}>{s.evidence}</p>
                      </div>
                    ),
                  },
                  { key: "state", header: "State", sortable: true, render: (s) => <Badge tone={SUBPROCESSOR_TONE[s.state]}>{label(s.state)}</Badge> },
                  { key: "transfer_record", header: "Transfer record", render: (s) => <span className={cx("block max-w-[200px] text-[12px] leading-snug", CONSOLE.body)}>{s.transfer_record}</span> },
                  { key: "incidents", header: "Incidents", render: (s) => <span className={cx("block max-w-[200px] text-[12px] leading-snug", CONSOLE.muted)}>{s.incidents}</span> },
                  { key: "exit_plan", header: "Exit plan", render: (s) => <span className={cx("block max-w-[220px] text-[12px] leading-snug", CONSOLE.muted)}>{s.exit_plan}</span> },
                ]}
                rows={SUBPROCESSORS}
                rowKey={(s) => s.id}
                pageSize={5}
                minWidth={2200}
                empty={{ icon: FiFileText, title: "No subprocessors recorded" }}
              />
            </Panel>
            <Panel title="Evidence-backed claims">
              <p className={cx("text-[13px] leading-[20px]", CONSOLE.body)}>
                A vendor certification or report is recorded only with an exact scope, a period or version, an
                issuing body, an evidence reference and an expiry or review date. A marketing claim, a badge
                or a logo is not assurance evidence, and an approved vendor is not approved forever — the
                state moves through diligence, restriction, suspension and exit.
              </p>
            </Panel>
          </>
        )}

        {/* S14-V11 */}
        {section === "controls" && (
          <>
            <Panel title="Control assurance" description="Assurance describes tested effectiveness within a defined scope and period. It is not a permanent property." flush>
              <DataTable
                columns={[
                  {
                    key: "control_id",
                    header: "Control",
                    sortable: true,
                    render: (c) => (
                      <div className="min-w-0 max-w-[240px]">
                        <p className={cx("text-[13px] font-medium leading-snug", CONSOLE.heading)}>{c.title}</p>
                        <p className={cx("text-[11px]", type.mono, CONSOLE.faint)}>
                          {c.control_id} · {c.control_type}
                        </p>
                      </div>
                    ),
                  },
                  { key: "objective", header: "Objective", render: (c) => <span className={cx("block max-w-[240px] text-[12px] leading-snug", CONSOLE.body)}>{c.objective}</span> },
                  { key: "scope", header: "Scope", render: (c) => <span className={cx("block max-w-[240px] text-[12px] leading-snug", CONSOLE.muted)}>{c.scope}</span> },
                  {
                    key: "test_period",
                    header: "Test period",
                    render: (c) => (
                      <div className="min-w-0">
                        <p className={cx("whitespace-nowrap text-[12px]", CONSOLE.body)}>{c.test_period}</p>
                        <p className={cx("text-[11px] leading-snug", CONSOLE.faint)}>{c.test_method}</p>
                      </div>
                    ),
                  },
                  {
                    key: "evidence",
                    header: "Evidence",
                    render: (c) => (
                      <div className="min-w-0">
                        <Badge tone={EVIDENCE_TONE[c.evidence_state]}>{label(c.evidence_state)}</Badge>
                        <p className={cx("mt-0.5 text-[11px]", type.mono, CONSOLE.faint)}>{c.evidence.join(", ") || "none"}</p>
                      </div>
                    ),
                  },
                  { key: "findings", header: "Findings", render: (c) => <span className={cx("block max-w-[320px] text-[12px] leading-snug", CONSOLE.muted)}>{c.findings}</span> },
                  {
                    key: "assurance_state",
                    header: "Assurance",
                    sortable: true,
                    render: (c) => (
                      <Badge tone={ASSURANCE_TONE[c.assurance_state]} dot={["INEFFECTIVE", "PARTIALLY_EFFECTIVE"].includes(c.assurance_state)}>
                        {label(c.assurance_state)}
                      </Badge>
                    ),
                  },
                  { key: "compensating", header: "Compensating", render: (c) => <span className={cx("block max-w-[240px] text-[12px] leading-snug", CONSOLE.muted)}>{c.compensating}</span> },
                  { key: "next_test", header: "Next test", sortable: true, render: (c) => <span className={cx("whitespace-nowrap text-[12px]", CONSOLE.body)}>{c.next_test}</span> },
                  { key: "owner", header: "Owner", sortable: true, render: (c) => <span className={cx("block max-w-[180px] text-[12px]", CONSOLE.body)}>{c.owner}</span> },
                ]}
                rows={CONTROLS}
                rowKey={(c) => c.control_id}
                pageSize={6}
                minWidth={2300}
                empty={{ icon: FiShield, title: "No controls recorded" }}
              />
            </Panel>
            <Panel title="Assurance decision rules">
              <ul className={cx("space-y-1.5 text-[13px]", CONSOLE.muted)}>
                <li>Evidence expiry prevents a control from remaining current without reevaluation.</li>
                <li>An ineffective control tied to an active material obligation creates a Governance action.</li>
                <li>Compensating controls are explicit and separately identified — never counted as the control itself.</li>
                <li>Not Applicable requires a documented scope basis and approval. It is not Effective.</li>
                <li>Not Tested is not Effective either, and it never becomes so by the passage of time.</li>
                <li>Control evidence may include Audit records, but Governance cannot edit Audit.</li>
                <li>Assurance results retain the tester or reviewer identity and the methodology used.</li>
              </ul>
            </Panel>
          </>
        )}

        {/* S14-V12 */}
        {section === "exceptions" && (
          <>
            <Panel title="Exceptions and risk acceptance" flush>
              <DataTable
                columns={[
                  {
                    key: "id",
                    header: "Exception",
                    sortable: true,
                    render: (e) => (
                      <div className="min-w-0 max-w-[260px]">
                        <p className={cx("text-[12px] font-semibold", type.mono, CONSOLE.heading)}>{e.id}</p>
                        <p className={cx("text-[12px] leading-snug", CONSOLE.body)}>{e.requirement}</p>
                      </div>
                    ),
                  },
                  { key: "scope", header: "Scope", render: (e) => <span className={cx("block max-w-[220px] text-[12px] leading-snug", CONSOLE.muted)}>{e.scope}</span> },
                  { key: "reason", header: "Reason", render: (e) => <span className={cx("block max-w-[300px] text-[12px] leading-snug", CONSOLE.body)}>{e.reason}</span> },
                  { key: "risk", header: "Risk", render: (e) => <span className={cx("block max-w-[200px] text-[12px] leading-snug", CONSOLE.body)}>{e.risk}</span> },
                  { key: "mitigations", header: "Mitigation", render: (e) => <span className={cx("block max-w-[280px] text-[12px] leading-snug", CONSOLE.muted)}>{e.mitigations}</span> },
                  {
                    key: "approvers",
                    header: "Owner / approvers",
                    render: (e) => (
                      <div className="min-w-0 max-w-[220px]">
                        <p className={cx("text-[12px]", CONSOLE.body)}>{e.requester}</p>
                        <p className={cx("text-[11px] leading-snug", CONSOLE.faint)}>{e.approvers}</p>
                      </div>
                    ),
                  },
                  {
                    key: "expires_at",
                    header: "Expiry",
                    sortable: true,
                    render: (e) => (
                      <div className="min-w-0">
                        <p className={cx("whitespace-nowrap text-[12px]", CONSOLE.body)}>{e.expires_at}</p>
                        <p className={cx("text-[11px] leading-snug", CONSOLE.faint)}>{e.renewal}</p>
                      </div>
                    ),
                  },
                  { key: "state", header: "State", sortable: true, render: (e) => <Badge tone={EXCEPTION_TONE[e.state]} dot={e.state === "ACTIVE"}>{label(e.state)}</Badge> },
                  { key: "post_review", header: "Post-review", render: (e) => <span className={cx("block max-w-[240px] text-[12px] leading-snug", CONSOLE.muted)}>{e.post_review}</span> },
                ]}
                rows={EXCEPTIONS.filter((e) => org === "all" || e.org === org)}
                rowKey={(e) => e.id}
                pageSize={5}
                minWidth={2200}
                empty={{ icon: FiFileText, title: "No exceptions in this scope" }}
              />
            </Panel>
            <Panel title="Exceptions never change the baseline">
              <p className={cx("text-[13px] leading-[20px]", CONSOLE.body)}>
                An exception is a controlled deviation. It does not rewrite the requirement, erase the risk,
                or become permanent configuration by default. The requirement stays visible as unsatisfied
                or excepted, so compliant-by-design and operating-under-approved-exception never look alike.
              </p>
              <ul className={cx("mt-3 space-y-1.5 text-[13px]", CONSOLE.muted)}>
                {EXCEPTIONS.map((e) => (
                  <li key={e.id}>
                    <span className={cx("font-semibold", type.mono, CONSOLE.heading)}>{e.id}</span> — {e.baseline_note}
                  </li>
                ))}
              </ul>
            </Panel>
          </>
        )}

        {/* S14-V13 */}
        {section === "board" && (
          <>
            <div className="grid gap-3 xl:grid-cols-2">
              {BOARD_ITEMS.map((b) => (
                <div key={b.id} className={cx(CONSOLE.panel, CONSOLE.panelHover, "transition-colors duration-150 motion-reduce:transition-none", "p-4")}>
                  <div className="flex flex-wrap items-start justify-between gap-2">
                    <div className="min-w-0">
                      <p className={cx("text-[13px] font-semibold leading-snug", CONSOLE.heading)}>{b.decision_class}</p>
                      <p className={cx("mt-0.5 text-[12px] leading-snug", CONSOLE.body)}>{b.subject}</p>
                      <p className={cx("text-[11px]", type.mono, CONSOLE.faint)}>{b.id}</p>
                    </div>
                    <Badge tone={TIMING_TONE[b.timing]}>{label(b.timing)}</Badge>
                  </div>
                  <dl className="mt-2">
                    <DetailField label="Requested by" value={b.requested_by} />
                    <DetailField label="Approvals" value={b.approvals} />
                    <DetailField label="Due" value={b.due} />
                  </dl>
                  <div className={cx("mt-2 border-t pt-2", CONSOLE.divider)}>
                    <p className={cx("mb-1 text-[10px] font-semibold uppercase tracking-[0.14em]", CONSOLE.faint)}>
                      Review package
                    </p>
                    <Pairs rows={b.package} />
                  </div>
                  <div className="mt-2 flex flex-wrap gap-2">
                    <Button variant="secondary" size="sm" disabled={!b.complete} title="Display only in this build">
                      Record approval
                    </Button>
                    <Button variant="secondary" size="sm" title="Display only in this build">
                      Record rejection
                    </Button>
                  </div>
                </div>
              ))}
            </div>
            <Panel title="Not a meeting-notes page">
              <p className={cx("text-[13px] leading-[20px]", CONSOLE.body)}>
                Every decision class carries a minimum structured review package, and an approval control is
                unavailable until that package is complete. Dual authorization applies, and a requester is
                always excluded from approving their own item.
              </p>
            </Panel>
          </>
        )}

        {/* S14-V14 */}
        {section === "evidence" && (
          <>
            <Panel title="Evidence and attestations" description="Governed evidence inventory. Expired evidence cannot remain Current or silently support assurance." flush>
              <DataTable
                columns={[
                  { key: "id", header: "Evidence", sortable: true, mono: true },
                  { key: "evidence_class", header: "Class", sortable: true, render: (e) => <Badge tone="neutral">{e.evidence_class}</Badge> },
                  { key: "scope", header: "Scope", render: (e) => <span className={cx("block max-w-[320px] text-[12px] leading-snug", CONSOLE.body)}>{e.scope}</span> },
                  { key: "issuer", header: "Issuer", render: (e) => <span className={cx("block max-w-[220px] text-[12px] leading-snug", CONSOLE.body)}>{e.issuer}</span> },
                  {
                    key: "period",
                    header: "Period / version",
                    render: (e) => (
                      <div className="min-w-0">
                        <p className={cx("whitespace-nowrap text-[12px]", CONSOLE.body)}>{e.period}</p>
                        <p className={cx("text-[11px]", CONSOLE.faint)}>{e.version}</p>
                      </div>
                    ),
                  },
                  {
                    key: "valid_until",
                    header: "Valid until / review",
                    render: (e) => (
                      <div className="min-w-0">
                        <p className={cx("whitespace-nowrap text-[12px]", CONSOLE.body)}>{e.valid_until}</p>
                        <p className={cx("whitespace-nowrap text-[11px]", CONSOLE.faint)}>review {e.review_by}</p>
                      </div>
                    ),
                  },
                  { key: "status", header: "Status", sortable: true, render: (e) => <Badge tone={EVIDENCE_TONE[e.status]} dot={["EXPIRED", "CHALLENGED"].includes(e.status)}>{label(e.status)}</Badge> },
                  { key: "integrity", header: "Integrity", render: (e) => <span className={cx("block max-w-[200px] text-[11px] leading-snug", type.mono, CONSOLE.faint)}>{e.integrity}</span> },
                  { key: "sensitivity", header: "Sensitivity", render: (e) => <span className={cx("block max-w-[200px] text-[12px] leading-snug", CONSOLE.muted)}>{e.sensitivity}</span> },
                  { key: "linked", header: "Linked records", render: (e) => <span className={cx("block max-w-[220px] text-[12px] leading-snug", type.mono, CONSOLE.body)}>{e.linked}</span> },
                ]}
                rows={EVIDENCE.filter((e) => evidenceState === "all" || e.status === evidenceState)}
                rowKey={(e) => e.id}
                pageSize={6}
                minWidth={2300}
                empty={{ icon: FiFileText, title: "No evidence matches", description: "Clear the evidence-state filter to see the full inventory." }}
              />
            </Panel>
            <Panel title="Evidence package export">
              <ul className={cx("space-y-1.5 text-[13px]", CONSOLE.muted)}>
                <li>Exports are purpose- and recipient-scoped.</li>
                <li>The manifest lists evidence IDs, versions, scopes, periods, integrity references, redactions and omissions.</li>
                <li>PII, secrets and customer-confidential data are minimized or redacted according to authorization.</li>
                <li>Exports carry an expiry and a revocation path where supported.</li>
                <li>Every export is audited with the requestor, recipient class, purpose, scope and contents.</li>
                <li>No package claims assurance outside the evidence scope it actually contains.</li>
              </ul>
              <Button variant="secondary" size="sm" leftIcon={FiDownload} className="mt-3" title="Display only in this build">
                Create evidence package
              </Button>
            </Panel>
          </>
        )}

        {/* S14-V15 */}
        {section === "access" && (
          <>
            {ACCESS_REVIEWS.map((a) => (
              <Panel
                key={a.id}
                title={a.campaign}
                description={`${a.id} · reviewer ${a.reviewer}`}
                action={<Badge tone={TIMING_TONE[a.timing]} dot={a.timing === "OVERDUE"}>{label(a.timing)}</Badge>}
              >
                <dl>
                  <DetailField label="Scope and population" value={a.scope} />
                  <DetailField label="Systems" value={a.systems} />
                  <DetailField label="Due" value={`${a.due_local} · ${a.due_utc}`} />
                  <DetailField label="Entitlement evidence" value={a.entitlement_evidence} />
                  <DetailField label="Decisions" value={a.decisions} />
                  <DetailField label="Reviewer rationale" value={a.rationale_required} />
                  <DetailField label="Execution" value={a.execution} />
                  <DetailField label="Exceptions" value={a.exceptions} />
                  <DetailField label="Completion" value={a.completion} />
                </dl>
                <p className={cx("mt-3 border-t pt-3 text-[12px] leading-snug", CONSOLE.divider, CONSOLE.faint)}>
                  {a.note}
                </p>
              </Panel>
            ))}
            <Panel title="Where the boundary sits">
              <p className={cx("text-[13px] leading-[20px]", CONSOLE.body)}>
                Identity &amp; Access owns grants and removals. Governance owns the review obligation, the
                certification evidence, the overdue-review risk and the completion record. A campaign is
                complete only once Identity &amp; Access reports verified execution — a reviewer&apos;s click
                is a decision, not an outcome.
              </p>
            </Panel>
          </>
        )}

        {/* S14-V16 */}
        {section === "history" && (
          <>
            <Panel title="Governance history" description="Versions, decisions, evidence, reviews, exceptions and approvals.">
              <ol className="relative space-y-4 pl-6">
                <span className="absolute left-[5px] top-1.5 h-[calc(100%-12px)] w-px bg-slate-200 dark:bg-white/10" aria-hidden="true" />
                {HISTORY.map((h) => (
                  <li key={`${h.at}-${h.text}`} className="relative">
                    <span
                      className={cx(
                        "absolute -left-6 top-1.5 h-[11px] w-[11px] rounded-full ring-2 ring-white dark:ring-black",
                        {
                          policy: "bg-violet-500",
                          approval: "bg-green-500",
                          review: "bg-blue-500",
                          evidence: "bg-blue-500",
                          assurance: "bg-amber-500",
                          exception: "bg-amber-500",
                          hold: "bg-rose-500",
                          residency: "bg-rose-500",
                        }[h.kind] || "bg-slate-400"
                      )}
                      aria-hidden="true"
                    />
                    <p className={cx("flex flex-wrap items-baseline gap-x-2 text-[11px]", CONSOLE.faint)}>
                      <span className={cx("font-semibold uppercase tracking-wider", CONSOLE.muted)}>{h.kind}</span>
                      <span className={type.mono}>{h.at}</span>
                      <span>· {h.actor}</span>
                    </p>
                    <p className={cx("mt-0.5 text-[13px] leading-snug", CONSOLE.body)}>{h.text}</p>
                  </li>
                ))}
              </ol>
            </Panel>
            <Panel title="Audit is authoritative elsewhere">
              <p className={cx("text-[13px] leading-[20px]", CONSOLE.body)}>
                Governance displays Audit references and uses them as evidence. It cannot amend, delete or
                backdate the authoritative Audit record — which is exactly why an Audit record makes
                acceptable control evidence in the first place.
              </p>
            </Panel>
          </>
        )}
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        <Panel title="Why there is no compliance score">
          <p className={cx("text-[13px] leading-[20px]", CONSOLE.body)}>
            A single percentage can hide expired evidence, a high-severity exception, a jurisdictional gap
            or an untested control behind one reassuring figure. This page shows scoped counts on separate
            axes instead, and every count links to the records behind it.
          </p>
          <ul className={cx("mt-3 space-y-1.5 text-[13px]", CONSOLE.muted)}>
            <li>A green badge without evidence is not governance.</li>
            <li>Unknown residency is not compliant residency.</li>
            <li>Not Tested and Not Applicable are not Effective.</li>
            <li>An expired exception does not make the underlying gap disappear.</li>
            <li>Job acceptance is not deletion completion.</li>
            <li>A reviewer&apos;s click is not access-review execution.</li>
          </ul>
        </Panel>

        <Panel title="Safe intervention contract">
          <p className={cx("text-[13px] leading-[20px]", CONSOLE.body)}>
            Every privileged governance action states the exact affected scope, the source obligation or
            policy and its version, the current evidence and its freshness, and an impact preview covering
            Organizations, data, regions, services, deletion consequences and downstream dependencies.
          </p>
          <ul className={cx("mt-3 space-y-1.5 text-[13px]", CONSOLE.muted)}>
            <li>Step-up authentication, plus typed confirmation for destructive or irreversible actions.</li>
            <li>Dual authorization for hold release, retention override, high-impact exceptions and cross-tenant destructive changes.</li>
            <li>Execution through the authoritative downstream service, never from this console.</li>
            <li>An immutable audit record, then a verified result with exception or remediation follow-up.</li>
            <li>A rollback path where one is technically and legally possible — and an explicit statement where none is.</li>
          </ul>
        </Panel>
      </div>

      <PolicyRecordDrawer policy={policy} open={Boolean(policy)} onClose={() => setPolicy(null)} />

      <DeletionPreviewDialog
        mode="deletion"
        preview={DELETION_PREVIEW}
        open={deletionOpen}
        onClose={() => setDeletionOpen(false)}
      />

      <DeletionPreviewDialog
        mode="release"
        hold={releaseHold}
        open={Boolean(releaseHold)}
        onClose={() => setReleaseHold(null)}
      />
    </ConsoleScreen>
  );
}
