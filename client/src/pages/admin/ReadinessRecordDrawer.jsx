import { useState } from "react";
import {
  FiAlertTriangle, FiArrowUpRight, FiCheckCircle, FiClock, FiFileText, FiRotateCcw,
  FiShield, FiUserPlus,
} from "react-icons/fi";
import {
  Badge, Button, DataTable, DetailField, Panel, TabStrip, CONSOLE, SEVERITY, cx, focusRing, type,
} from "../../components/admin";
import Drawer from "../../ui/Drawer";
import {
  EVIDENCE_LABEL, EVIDENCE_TONE, GATE_LABEL, GATE_TONE, REGIONS, RISK_LABEL, RISK_TONE,
} from "./readinessData";

// S04-V02 Event Readiness Record — the authoritative per-event workspace, opened from a
// pipeline row.
//
// Two spec rules do the most work in here:
//   · STD-04 / §5 — lifecycle, risk, gate and evidence are SEPARATE axes. There is no
//     combined "status" anywhere; a blocked event with valid contribution evidence has to
//     read as exactly that.
//   · §3.2 NO MANUAL GATE EDIT — the gate controls resolve evidence, request re-tests or open
//     a governed exception. None of them writes a gate state, and the panel says so, because
//     a control that looks like it sets the gate invites someone to try during a launch.
//
// Every action below is display-only in this build: there is no readiness policy engine to
// call yet (§34 lists it as a build blocker), and a button that silently does nothing is
// worse than a button that tells you it is a wireframe.

const GATE_COPY = {
  PASSED: { title: "Ready to operate", body: "All mandatory readiness requirements have valid evidence." },
  CONDITIONAL: {
    title: "Ready with open conditions",
    body: "Mandatory requirements are satisfied. Resolve the remaining conditions before their deadlines.",
  },
  BLOCKED: { title: "Not ready to operate", body: "One or more mandatory readiness requirements are not satisfied." },
  IN_PROGRESS: {
    title: "Readiness in progress",
    body: "At least one applicable requirement is being worked. No final gate decision exists yet.",
  },
  NOT_STARTED: {
    title: "Readiness not started",
    body: "This event is inside the readiness horizon, but no applicable evidence work has begun.",
  },
};

const GATE_BANNER = {
  PASSED: "border-green-200 bg-green-50 dark:border-green-500/25 dark:bg-green-500/10",
  CONDITIONAL: "border-amber-200 bg-amber-50 dark:border-amber-500/25 dark:bg-amber-500/10",
  BLOCKED: "border-rose-200 bg-rose-50 dark:border-rose-500/25 dark:bg-rose-500/10",
  IN_PROGRESS: "border-blue-200 bg-blue-50 dark:border-blue-500/25 dark:bg-blue-500/10",
  NOT_STARTED: "border-slate-200 bg-slate-50 dark:border-white/10 dark:bg-white/[0.03]",
};

// Timeline entry kind → dot color. Text carries the meaning too; color never carries it alone.
const KIND_DOT = {
  evaluation: "bg-violet-500",
  requirement: "bg-amber-500",
  evidence: "bg-blue-500",
  assignment: "bg-slate-400",
  transition: "bg-rose-500",
};
const KIND_LABEL = {
  evaluation: "Policy evaluation",
  requirement: "Requirement change",
  evidence: "Evidence change",
  assignment: "Assignment",
  transition: "Transition attempt",
};

const TABS = [
  { key: "overview", label: "Overview" },
  { key: "evidence", label: "Evidence" },
  { key: "contribution", label: "Contribution" },
  { key: "production", label: "Production" },
  { key: "security", label: "Security & Access" },
  { key: "delivery", label: "Delivery" },
  { key: "preservation", label: "Preservation" },
  { key: "people", label: "People & Runbook" },
  { key: "exceptions", label: "Exceptions" },
  { key: "history", label: "History" },
];

const regionLabel = (id) => REGIONS.find((r) => r.id === id)?.label || id;

function EvidencePill({ state }) {
  return <Badge tone={EVIDENCE_TONE[state] || "neutral"}>{EVIDENCE_LABEL[state] || state}</Badge>;
}

// A pair-list section: [[label, value], …]. Every technical tab is this shape, which is why
// the tab bodies below stay short enough to read.
function Pairs({ rows }) {
  return (
    <dl>
      {rows.map(([label, value]) => (
        <DetailField key={label} label={label} value={value} />
      ))}
    </dl>
  );
}

function BlockerCard({ b }) {
  return (
    <div className={cx("rounded-lg border p-4", GATE_BANNER.BLOCKED)}>
      <div className="flex flex-wrap items-start justify-between gap-2">
        <p className="flex items-start gap-2 text-[13px] font-semibold text-rose-700 dark:text-rose-300">
          <FiAlertTriangle className="mt-0.5 shrink-0" aria-hidden="true" />
          {b.title}
        </p>
        <EvidencePill state={b.state} />
      </div>
      <dl className="mt-2">
        <DetailField label="Requirement" value={b.requirement} />
        <DetailField label="Stage" value={b.stage} />
        <DetailField label="Why blocked" value={b.why} />
        <DetailField label="Required evidence" value={b.required} />
        <DetailField label="Owner" value={b.owner} />
        <DetailField label="Deadline" value={b.deadline} />
        <DetailField label="Runbook" value={b.runbook} />
        <DetailField label="Dependencies" value={b.dependencies} />
        <DetailField label="Recommended action">
          <ul className="space-y-0.5">
            {b.actions.map((a) => (
              <li key={a}>{a}</li>
            ))}
          </ul>
        </DetailField>
      </dl>
    </div>
  );
}

export default function ReadinessRecordDrawer({ event, open, onClose }) {
  const [tab, setTab] = useState("overview");
  if (!event) return null;

  const copy = GATE_COPY[event.gate];
  const mandatoryOpen = event.requirements.filter(
    (r) => r.mandatory && !["valid", "not_applicable"].includes(r.state)
  );
  const conditionsOpen = event.requirements.filter(
    (r) => !r.mandatory && !["valid", "not_applicable"].includes(r.state)
  );

  const evidenceColumns = [
    {
      key: "requirement",
      header: "Requirement",
      sortable: true,
      sortValue: (r) => r.id,
      render: (r) => (
        <div className="min-w-0">
          <p className={cx("text-[13px] font-semibold", CONSOLE.heading)}>{r.label}</p>
          <p className={cx("text-[11px]", type.mono, CONSOLE.faint)}>
            {r.id} · {r.stage} · {r.mandatory ? "Mandatory" : "Conditional mandatory"}
          </p>
        </div>
      ),
    },
    {
      key: "state",
      header: "Evidence state",
      sortable: true,
      render: (r) => <EvidencePill state={r.state} />,
    },
    { key: "owner", header: "Owner", sortable: true },
    {
      key: "evidence",
      header: "Evidence",
      render: (r) => (
        <div className="min-w-0">
          <p className={cx("text-[12px]", CONSOLE.body)}>{r.result}</p>
          <p className={cx("text-[11px]", type.mono, CONSOLE.faint)}>{r.evidence_id || "no artifact"}</p>
        </div>
      ),
    },
    {
      key: "recorded",
      header: "Recorded / valid until",
      render: (r) => (
        <div className="min-w-0">
          <p className={cx("text-[12px]", CONSOLE.body)}>{r.recorded}</p>
          <p className={cx("text-[11px]", CONSOLE.faint)}>valid until {r.valid_until}</p>
        </div>
      ),
    },
  ];

  // §14 operator actions. Every one is a request into an authoritative service — none of them
  // sets a gate — so the labels name the request, not the outcome.
  const gateActions = [
    { label: "Resolve evidence gap", icon: FiCheckCircle, note: "Opens the failing requirement's remediation route" },
    { label: "Request re-test", icon: FiRotateCcw, note: "Platform Operations elevation · audited" },
    { label: "Assign operator", icon: FiUserPlus, note: "Platform Operations elevation · audited + notified" },
    { label: "Escalate resources", icon: FiArrowUpRight, note: "Platform Operations elevation · audited" },
    {
      label: "Request single-path exception",
      icon: FiShield,
      note: "Dual authorization · requester excluded from approval · Governance review",
      danger: true,
      only: event.risk === "unrepeatable" && event.gate === "BLOCKED",
    },
  ].filter((a) => a.only !== false);

  return (
    <Drawer
      open={open}
      onClose={onClose}
      width="w-[880px] max-w-[96vw]"
      title={`Readiness record · ${event.name}`}
    >
      {/* §9.1 Header — every axis is its own field. */}
      <div className={cx("rounded-lg border p-4", CONSOLE.divider)}>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <h3 className={cx("text-[17px] font-semibold tracking-tight", CONSOLE.heading)}>{event.name}</h3>
            <p className={cx("mt-0.5 text-[11px]", type.mono, CONSOLE.faint)}>
              {event.event_id} · {event.tenant_id}
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-1.5">
            <Badge tone="neutral">Live mode</Badge>
            <Badge tone={RISK_TONE[event.risk]} dot>
              {RISK_LABEL[event.risk]}
            </Badge>
            <Badge tone={GATE_TONE[event.gate]} dot>
              {GATE_LABEL[event.gate]}
            </Badge>
          </div>
        </div>

        <div className="mt-3 grid gap-x-6 sm:grid-cols-2">
          <dl>
            <DetailField label="Organization" value={event.org} />
            <DetailField label="Scheduled start" value={`${event.start_local} · ${event.start_utc}`} />
            <DetailField label="Time to start" value={`${event.starts_in} · starts ${event.start_utc}`} />
            <DetailField label="Production region" value={regionLabel(event.region)} />
          </dl>
          <dl>
            <DetailField label="Service tier" value={event.tier} />
            <DetailField label="Primary operator" value={`${event.operator} · ${event.operator_oncall}`} />
            <DetailField label="Backup operator" value={`${event.backup_operator} · ${event.backup_operator_oncall}`} />
            <DetailField label="Last evaluated" value={`${event.evaluated_at} · ${event.policy_version}`} />
          </dl>
        </div>
      </div>

      {/* Derived gate verdict, stated in the canonical copy of §29. */}
      <div className={cx("mt-3 rounded-lg border px-4 py-3", GATE_BANNER[event.gate])}>
        <p className={cx("text-[13px] font-semibold", CONSOLE.heading)}>{copy.title}</p>
        <p className={cx("mt-0.5 text-[12px] leading-snug", CONSOLE.body)}>{copy.body}</p>
        {event.risk === "unrepeatable" && event.gate === "BLOCKED" && (
          <p className="mt-2 border-t border-rose-200/70 pt-2 text-[12px] leading-snug text-rose-700 dark:border-rose-500/25 dark:text-rose-300">
            <strong>Independent backup path not verified.</strong> This unrepeatable event cannot enter
            commercial live operation until an independent backup contribution path is verified, or a
            governed exception is approved.
          </p>
        )}
      </div>

      <div className="mt-4">
        <TabStrip
          tabs={TABS.map((t) =>
            t.key === "evidence"
              ? { ...t, count: mandatoryOpen.length + conditionsOpen.length }
              : t.key === "exceptions"
                ? { ...t, count: event.exceptions.length }
                : t
          )}
          active={tab}
          onChange={setTab}
          label="Readiness record sections"
          idPrefix="rr"
        />
      </div>

      <div
        role="tabpanel"
        id={`rrpanel-${tab}`}
        aria-labelledby={`rr-${tab}`}
        tabIndex={-1}
        className="mt-4 space-y-4"
      >
        {tab === "overview" && (
          <>
            <div className="grid gap-4 lg:grid-cols-[1.4fr_1fr]">
              <Panel title="Gate summary">
                <dl>
                  <DetailField label="Gate state" value={`${GATE_LABEL[event.gate]} — derived by the policy engine`} />
                  <DetailField label="Mandatory blockers" value={String(event.blockers)} />
                  <DetailField label="Open conditions" value={String(conditionsOpen.length)} />
                  <DetailField label="Nearest evidence expiry" value={event.evidence_expiry} />
                  <DetailField label="Audience exposure" value={event.audience_expectation} />
                  <DetailField
                    label="Downstream permission"
                    value={
                      event.gate === "BLOCKED"
                        ? "Commercial live transition is unavailable"
                        : "Commercial live transition permitted by the current gate"
                    }
                  />
                </dl>
              </Panel>

              <Panel title="Gate controls" description="Requests into authoritative services.">
                <div className="space-y-2">
                  {gateActions.map((a) => (
                    <div key={a.label}>
                      <Button
                        variant={a.danger ? "danger" : "secondary"}
                        size="sm"
                        leftIcon={a.icon}
                        className="w-full justify-start"
                        title="Display only in this wireframe build"
                      >
                        {a.label}
                      </Button>
                      <p className={cx("mt-0.5 px-1 text-[11px] leading-snug", CONSOLE.faint)}>{a.note}</p>
                    </div>
                  ))}
                </div>
                <p className={cx("mt-3 border-t pt-3 text-[11px] leading-snug", CONSOLE.divider, CONSOLE.faint)}>
                  No control here edits the gate. Operators resolve evidence, correct
                  configuration, or submit a governed exception; the gate is derived by the
                  readiness policy engine.
                </p>
              </Panel>
            </div>

            <Panel
              title={event.blockers > 0 ? "Blockers and remediation" : "No mandatory blockers"}
              count={event.blockers}
            >
              {event.blocker_details.length > 0 ? (
                <div className="space-y-3">
                  {event.blocker_details.map((b) => (
                    <BlockerCard key={b.requirement} b={b} />
                  ))}
                </div>
              ) : (
                <p className={cx("text-[13px]", CONSOLE.muted)}>
                  Every mandatory requirement this event&apos;s applicability rules select is satisfied
                  by valid evidence. A previous pass does not freeze readiness — expiring evidence or a
                  configuration change can move this back to Blocked.
                </p>
              )}
            </Panel>
          </>
        )}

        {tab === "evidence" && (
          <Panel
            title="Requirement and evidence matrix"
            description="Fifteen policy-controlled rules; applicability decides which apply to this event."
            flush
          >
            <DataTable
              columns={evidenceColumns}
              rows={event.requirements}
              rowKey={(r) => r.id}
              minWidth={860}
              searchable
              searchPlaceholder="Search requirement, owner or artifact…"
              getSearchText={(r) => `${r.id} ${r.label} ${r.stage} ${r.owner} ${r.state} ${r.result}`}
              pageSize={8}
              empty={{ icon: FiFileText, title: "No requirements match", description: "Clear the search to see the full matrix." }}
            />
          </Panel>
        )}

        {tab === "contribution" && (
          <>
            <Panel title="Contribution paths" description="ER-C01 primary and ER-C02 independent backup.">
              <Pairs rows={event.detail.contribution} />
            </Panel>
            <StageRequirements requirements={event.requirements} stage="Contribute" />
          </>
        )}

        {tab === "production" && (
          <>
            <Panel title="Production readiness" description="Transcoding, packaging, captions, translation.">
              <Pairs rows={event.detail.production} />
            </Panel>
            <StageRequirements requirements={event.requirements} stage="Produce" />
          </>
        )}

        {tab === "security" && (
          <>
            <Panel title="Playback authorization" description="Access policy and restricted-grantee readiness.">
              <Pairs rows={event.detail.security} />
            </Panel>
            <StageRequirements requirements={event.requirements} stage="Secure" />
          </>
        )}

        {tab === "delivery" && (
          <>
            <Panel title="Delivery readiness" description="Regional delivery and pre-warm evidence.">
              <Pairs rows={event.detail.delivery} />
            </Panel>
            <StageRequirements requirements={event.requirements} stage="Deliver" />
            <StageRequirements requirements={event.requirements} stage="Ingest" />
          </>
        )}

        {tab === "preservation" && (
          <>
            <Panel title="Preservation commitments" description="Recording, retention and replay commitment.">
              <Pairs rows={event.detail.preservation} />
            </Panel>
            <StageRequirements requirements={event.requirements} stage="Preserve" />
          </>
        )}

        {tab === "people" && (
          <>
            <Panel title="Operators, runbook and rehearsal">
              <dl>
                <DetailField label={event.people.primary.role} value={`${event.people.primary.name} · ${event.people.primary.detail}`} />
                <DetailField label={event.people.backup.role} value={`${event.people.backup.name} · ${event.people.backup.detail}`} />
                <DetailField label="Runbook" value={event.people.runbook} />
                <DetailField label="Rehearsal" value={event.people.rehearsal} />
                <DetailField label="Handover" value={event.people.handover} />
              </dl>
            </Panel>
            <Panel title="Contact tree" description="ER-O03 — validated inside the configured window.">
              <ul className={cx("divide-y", CONSOLE.divideY)}>
                {event.contacts.map((c) => (
                  <li key={c.name} className="flex flex-wrap items-baseline justify-between gap-2 py-2">
                    <div className="min-w-0">
                      <p className={cx("text-[13px] font-medium", CONSOLE.heading)}>{c.name}</p>
                      <p className={cx("text-[11px]", CONSOLE.faint)}>{c.role}</p>
                    </div>
                    <span className={cx("text-[12px]", CONSOLE.muted)}>{c.detail}</span>
                  </li>
                ))}
              </ul>
            </Panel>
            <StageRequirements requirements={event.requirements} stage="Operations" />
          </>
        )}

        {tab === "exceptions" && (
          <Panel title="Conditions and governed exceptions" count={event.exceptions.length}>
            {event.exceptions.length === 0 ? (
              <p className={cx("text-[13px]", CONSOLE.muted)}>
                No conditions and no governed exception on this event. An exception is intentionally
                expensive to obtain — it needs step-up authentication, a substantive justification, an
                explicit risk acknowledgment, and two distinct approvers who are not the requester.
              </p>
            ) : (
              <div className="space-y-3">
                {event.exceptions.map((x) => (
                  <div key={x.id} className={cx("rounded-lg border p-4", CONSOLE.inset)}>
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <p className={cx("text-[13px] font-semibold", CONSOLE.heading)}>{x.requirement}</p>
                      <span className={cx("rounded-full px-2 py-0.5 text-[11px] font-semibold", SEVERITY.conditional)}>
                        {x.status}
                      </span>
                    </div>
                    <dl className="mt-2">
                      <DetailField label="Exception ID" value={x.id} />
                      <DetailField label="Requester" value={x.requester} />
                      <DetailField label="Justification" value={x.justification} />
                      <DetailField label="Approval" value={x.approvers} />
                      <DetailField label="Effective window" value={x.window} />
                      <DetailField label="Governance review" value={x.review} />
                    </dl>
                  </div>
                ))}
              </div>
            )}
          </Panel>
        )}

        {tab === "history" && (
          <Panel
            title="Decision timeline"
            description="Policy evaluations, evidence changes, approvals, gate transitions and attempted state transitions."
          >
            <ol className="relative space-y-4 pl-6">
              <span
                className={cx("absolute left-[5px] top-1.5 h-[calc(100%-12px)] w-px", "bg-slate-200 dark:bg-white/10")}
                aria-hidden="true"
              />
              {event.history.map((h) => (
                <li key={`${h.at}-${h.text}`} className="relative">
                  <span
                    className={cx(
                      "absolute -left-6 top-1.5 h-[11px] w-[11px] rounded-full ring-2 ring-white dark:ring-black",
                      KIND_DOT[h.kind] || "bg-slate-400"
                    )}
                    aria-hidden="true"
                  />
                  <p className={cx("flex flex-wrap items-baseline gap-x-2 text-[11px]", CONSOLE.faint)}>
                    <span className={cx("font-semibold uppercase tracking-wider", CONSOLE.muted)}>
                      {KIND_LABEL[h.kind] || h.kind}
                    </span>
                    <span className={type.mono}>{h.at}</span>
                    <span>· {h.actor}</span>
                  </p>
                  <p className={cx("mt-0.5 text-[13px] leading-snug", CONSOLE.body)}>{h.text}</p>
                </li>
              ))}
            </ol>
            <p className={cx("mt-4 border-t pt-3 text-[11px] leading-snug", CONSOLE.divider, CONSOLE.faint)}>
              The timeline references immutable audit records. It cannot edit them.
            </p>
          </Panel>
        )}
      </div>

      <div className={cx("mt-5 flex flex-wrap items-center gap-2 border-t pt-4", CONSOLE.divider)}>
        <span className={cx("inline-flex items-center gap-1.5 text-[11px]", CONSOLE.faint)}>
          <FiClock aria-hidden="true" />
          Evaluated {event.evaluated_at}
        </span>
        <button
          type="button"
          onClick={onClose}
          className={cx(
            "ml-auto rounded-lg px-3 py-1.5 text-[13px] font-medium",
            CONSOLE.segmentOff,
            focusRing
          )}
        >
          Close
        </button>
      </div>
    </Drawer>
  );
}

// The per-stage requirement list the technical tabs share. Same evidence vocabulary as the
// matrix, so a rule cannot read one way here and another way there.
function StageRequirements({ requirements, stage }) {
  const rows = requirements.filter((r) => r.stage === stage);
  if (rows.length === 0) return null;
  return (
    <Panel title={`${stage} requirements`} flush>
      <ul className={cx("divide-y", CONSOLE.divideY)}>
        {rows.map((r) => (
          <li key={r.id} className="transition-colors duration-150 hover:bg-slate-50 motion-reduce:transition-none dark:hover:bg-white/[0.03] flex flex-wrap items-start justify-between gap-3 px-5 py-3">
            <div className="min-w-0">
              <p className={cx("text-[13px] font-medium", CONSOLE.heading)}>{r.label}</p>
              <p className={cx("mt-0.5 text-[11px]", CONSOLE.faint)}>
                <span className={type.mono}>{r.id}</span> · {r.mandatory ? "Mandatory" : "Conditional mandatory"} ·{" "}
                {r.owner}
              </p>
              <p className={cx("mt-1 text-[12px] leading-snug", CONSOLE.muted)}>{r.artifact}</p>
            </div>
            <div className="shrink-0 text-right">
              <EvidencePill state={r.state} />
              <p className={cx("mt-1 text-[11px]", CONSOLE.faint)}>{r.result}</p>
            </div>
          </li>
        ))}
      </ul>
    </Panel>
  );
}
