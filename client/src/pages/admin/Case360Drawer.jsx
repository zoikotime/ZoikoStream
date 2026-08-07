import { useState } from "react";
import { FiAlertTriangle, FiCheckCircle, FiFileText, FiInfo, FiLock, FiShield } from "react-icons/fi";
import {
  Badge, Button, DataTable, DetailField, Panel, TabStrip, CONSOLE, cx, focusRing, type,
} from "../../components/admin";
import Drawer from "../../ui/Drawer";
import {
  APPEAL_TONE, AVAILABILITY_TONE, CASE_STATE_TONE, CONFIDENCE_TONE, ENFORCEMENT_TONE,
  EVIDENCE_INTEGRITY_TONE, FINDING_TONE, LIVE_TONE, OUTCOMES, PRIORITY_TONE,
  PROPORTIONALITY_FACTORS, REGIONS, SIGNAL_LABEL, SIGNAL_TREATMENT, SLA_TONE, label,
} from "./trustSafetyData";

// S08-V03 Case 360 — the authoritative case record, opened from a queue row.
//
// The spec's hardest requirement is the one this file spends the most code on: eight state axes
// that must never collapse into one. A case can be VERIFYING, with a VIOLATION_CONFIRMED
// finding, a REVERSED enforcement, an OVERTURNED appeal and AVAILABLE content all at once —
// and each of those tells the reviewer to do something different.
//
// The second is NO AUTOMATED GUILT. Signal confidence is rendered in a deliberately neutral
// tone next to the treatment text for its source ("Signal only; requires human review"), so a
// HIGH-confidence detector hit can never read as a decided violation.
//
// Nothing here mutates. §36 makes the policy registry, Identity & Access, Audit and the evidence
// store build blockers, so the decision controls state what they would require and are inert.

const TABS = [
  { key: "overview", label: "Overview" },
  { key: "content", label: "Content Context" },
  { key: "evidence", label: "Evidence" },
  { key: "policy", label: "Policy Assessment" },
  { key: "decision", label: "Decision" },
  { key: "enforcement", label: "Enforcement" },
  { key: "communications", label: "Communications" },
  { key: "appeal", label: "Appeal" },
  { key: "history", label: "History" },
];

const BLOCKER_SKIN = {
  danger: "border-rose-200 bg-rose-50 text-rose-700 dark:border-rose-500/25 dark:bg-rose-500/10 dark:text-rose-300",
  warning: "border-amber-200 bg-amber-50 text-amber-800 dark:border-amber-500/25 dark:bg-amber-500/10 dark:text-amber-200",
  info: "border-blue-200 bg-blue-50 text-blue-800 dark:border-blue-500/25 dark:bg-blue-500/10 dark:text-blue-200",
};

const KIND_DOT = {
  signal: "bg-blue-500",
  assessment: "bg-violet-500",
  evidence: "bg-blue-500",
  decision: "bg-amber-500",
  enforcement: "bg-rose-500",
  appeal: "bg-green-500",
  communication: "bg-slate-400",
  assignment: "bg-slate-400",
};
const KIND_LABEL = {
  signal: "Signal / intake",
  assessment: "Policy assessment",
  evidence: "Evidence change",
  decision: "Decision",
  enforcement: "Enforcement",
  appeal: "Appeal",
  communication: "Communication",
  assignment: "Assignment",
};

const regionLabel = (id) => REGIONS.find((r) => r.id === id)?.label || id;

function Pairs({ rows }) {
  return (
    <dl>
      {rows.map(([k, v]) => (
        <DetailField key={k} label={k} value={v} />
      ))}
    </dl>
  );
}

// The eight state axes as one row. Rendered identically in the header and on Overview, so the
// two can never drift apart.
export function AxisRow({ c }) {
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <Badge tone={c.mode === "TEST" ? "warning" : "neutral"}>{c.mode === "TEST" ? "Test mode" : "Live mode"}</Badge>
      <Badge tone={PRIORITY_TONE[c.priority]} dot>
        Priority · {label(c.priority)}
      </Badge>
      <Badge tone={CASE_STATE_TONE[c.case_state]}>Case · {label(c.case_state)}</Badge>
      <Badge tone={CONFIDENCE_TONE[c.confidence]}>Signal confidence · {label(c.confidence)}</Badge>
      <Badge tone={FINDING_TONE[c.finding]}>Finding · {label(c.finding)}</Badge>
      <Badge tone={ENFORCEMENT_TONE[c.enforcement_state]}>Enforcement · {label(c.enforcement_state)}</Badge>
      <Badge tone={APPEAL_TONE[c.appeal_state]}>Appeal · {label(c.appeal_state)}</Badge>
      <Badge tone={AVAILABILITY_TONE[c.content_availability]}>Content · {label(c.content_availability)}</Badge>
      <Badge tone={LIVE_TONE[c.live_context]} dot={c.live_context === "LIVE"}>
        Live · {label(c.live_context)}
      </Badge>
    </div>
  );
}

export default function Case360Drawer({ caseRecord: c, open, onClose, onRequestPreview, onProposeRestriction }) {
  const [tab, setTab] = useState("overview");
  if (!c) return null;

  const evidenceColumns = [
    {
      key: "evidence_id",
      header: "Evidence",
      sortable: true,
      render: (e) => (
        <div className="min-w-0">
          <p className={cx("text-[12px] font-semibold", type.mono, CONSOLE.heading)}>{e.evidence_id}</p>
          <p className={cx("text-[11px]", CONSOLE.faint)}>{e.type}</p>
        </div>
      ),
    },
    {
      key: "source",
      header: "Source / collector",
      render: (e) => (
        <div className="min-w-0">
          <p className={cx("text-[12px]", CONSOLE.body)}>{e.source}</p>
          <p className={cx("text-[11px]", CONSOLE.faint)}>collected by {e.collector}</p>
        </div>
      ),
    },
    {
      key: "confidence",
      header: "Confidence",
      render: (e) =>
        e.confidence && e.confidence !== "—" ? (
          <Badge tone={CONFIDENCE_TONE[e.confidence] || "neutral"}>{label(e.confidence)}</Badge>
        ) : (
          <span className={cx("text-[12px]", CONSOLE.faint)} title="Confidence applies to inferential evidence only">
            —
          </span>
        ),
    },
    {
      key: "integrity",
      header: "Integrity",
      render: (e) => (
        <div className="min-w-0">
          <Badge tone={EVIDENCE_INTEGRITY_TONE[e.integrity]}>{label(e.integrity)}</Badge>
          <p className={cx("mt-0.5 text-[11px]", type.mono, CONSOLE.faint)}>{e.integrity_ref}</p>
        </div>
      ),
    },
    {
      key: "sensitivity",
      header: "Classification",
      render: (e) => <span className={cx("text-[12px]", CONSOLE.body)}>{e.sensitivity}</span>,
    },
    {
      key: "relation",
      header: "Chain of custody",
      render: (e) => (
        <div className="min-w-0">
          <p className={cx("text-[12px]", CONSOLE.body)}>{e.relation}</p>
          <p className={cx("text-[11px]", CONSOLE.faint)}>{e.created}</p>
        </div>
      ),
    },
  ];

  return (
    <Drawer open={open} onClose={onClose} width="w-[960px] max-w-[96vw]" title={`Case 360 · ${c.case_id}`}>
      {/* §11 Header — every axis is its own field. */}
      <div className={cx("rounded-lg border p-4", CONSOLE.divider)}>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <h3 className={cx("text-[17px] font-semibold tracking-tight", CONSOLE.heading)}>{c.title}</h3>
            <p className={cx("mt-0.5 text-[11px]", type.mono, CONSOLE.faint)}>
              {c.case_id} · {c.tenant_id} · {c.policy_version}
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-1.5">
            {c.unrepeatable && <Badge tone="danger" dot>Unrepeatable</Badge>}
            <Badge tone={SLA_TONE[c.sla_state]}>SLA · {label(c.sla_state)}</Badge>
          </div>
        </div>

        <div className="mt-3">
          <AxisRow c={c} />
        </div>

        <div className="mt-3 grid gap-x-6 sm:grid-cols-2">
          <dl>
            <DetailField label="Organization" value={`${c.org} · ${c.tier}`} />
            <DetailField label="Content scope" value={`${c.content_ref}${c.asset_ref !== "—" ? ` · ${c.asset_ref}` : ""}`} />
            <DetailField label="Primary reason" value={`${c.reason_family} — ${c.reason}`} />
            <DetailField label="Production region" value={regionLabel(c.region)} />
          </dl>
          <dl>
            <DetailField label="Signal sources">
              <ul className="space-y-0.5">
                {c.signal_sources.map((s) => (
                  <li key={s}>
                    {SIGNAL_LABEL[s]} <span className={CONSOLE.faint}>— {SIGNAL_TREATMENT[s]}</span>
                  </li>
                ))}
              </ul>
            </DetailField>
            <DetailField label="Assigned reviewer" value={c.owner} />
            <DetailField label="Opened" value={`${c.opened_local} · ${c.opened_utc} · age ${c.age}`} />
            <DetailField label="Due" value={`${c.due_local} · ${c.due_utc}`} />
          </dl>
        </div>
      </div>

      <div className="mt-4">
        <TabStrip
          tabs={TABS.map((t) =>
            t.key === "evidence"
              ? { ...t, count: c.evidence.length }
              : t.key === "communications"
                ? { ...t, count: c.communications.length }
                : t
          )}
          active={tab}
          onChange={setTab}
          label="Case 360 sections"
          idPrefix="c360"
        />
      </div>

      <div role="tabpanel" id={`c360panel-${tab}`} aria-labelledby={`c360-${tab}`} tabIndex={-1} className="mt-4 space-y-4">
        {tab === "overview" && (
          <>
            {c.blockers.length > 0 && (
              <div className="space-y-2">
                {c.blockers.map((b) => (
                  <p
                    key={b.text}
                    className={cx("flex items-start gap-2 rounded-lg border px-4 py-3 text-[12px] leading-snug", BLOCKER_SKIN[b.tone])}
                  >
                    {b.tone === "info" ? (
                      <FiInfo className="mt-0.5 shrink-0" aria-hidden="true" />
                    ) : (
                      <FiAlertTriangle className="mt-0.5 shrink-0" aria-hidden="true" />
                    )}
                    {b.text}
                  </p>
                ))}
              </div>
            )}

            <Panel title="Summary and next action">
              <div className="mb-3">
                <AxisRow c={c} />
              </div>
              <dl>
                <DetailField label="Required next action" value={c.next_action} />
                <DetailField label="Live exposure" value={c.viewers} />
                <DetailField label="Risk classification" value={c.risk_classification} />
                <DetailField label="Legal escalation" value={c.legal_escalation} />
                <DetailField label="Evidence on file" value={`${c.evidence.length} items`} />
                <DetailField label="Verification" value={c.verification.length ? `${c.verification.length} checks recorded` : "No enforcement to verify"} />
              </dl>
            </Panel>

            {c.urgent_live && (
              <Panel title="Protected content preview" description="Case metadata permission does not imply content-view permission.">
                <p className={cx("text-[13px] leading-[20px]", CONSOLE.body)}>
                  Reviewing this content requires an active Trust &amp; Safety elevation with content-access
                  scope and a case-linked justification. The session is time-bounded, watermarked where
                  supported, starts muted, and every access is audit-linked. Raw download is not offered
                  from live review.
                </p>
                <Button variant="secondary" size="sm" leftIcon={FiLock} className="mt-3" onClick={onRequestPreview}>
                  Request protected preview
                </Button>
                <p className={cx("mt-3 border-t pt-3 text-[11px] leading-snug", CONSOLE.divider, CONSOLE.faint)}>
                  Text and evidence-only review stays available when content exposure should be minimized.
                </p>
              </Panel>
            )}

            {c.proposal && (
              <Panel title="Organization restriction proposal" description="Capability-scoped. Full suspension is not the default.">
                <dl>
                  <DetailField label="Proposed capabilities" value={`${c.proposal.capabilities.filter((x) => x.proposed === "Restricted").length} of ${c.proposal.capabilities.length} restricted`} />
                  <DetailField label="Supporting final cases" value={`${c.proposal.supporting_cases.filter((x) => x.counts).length} final and comparable`} />
                  <DetailField label="Approval" value={c.proposal.approval} />
                </dl>
                <Button variant="secondary" size="sm" leftIcon={FiShield} className="mt-3" onClick={onProposeRestriction}>
                  Open restriction proposal
                </Button>
              </Panel>
            )}
          </>
        )}

        {tab === "content" && (
          <Panel title="Content context" description="Event and asset metadata, distribution scope, exposure and Organization history.">
            <Pairs rows={c.content_context} />
          </Panel>
        )}

        {tab === "evidence" && (
          <>
            <Panel title="Evidence workspace" description="Original evidence is never overwritten. Annotations and redactions create linked derived items." flush>
              <DataTable
                columns={evidenceColumns}
                rows={c.evidence}
                rowKey={(e) => e.evidence_id}
                minWidth={1180}
                searchable
                searchPlaceholder="Search evidence ID, type, source or classification…"
                getSearchText={(e) => `${e.evidence_id} ${e.type} ${e.source} ${e.sensitivity} ${e.integrity}`}
                pageSize={6}
                empty={{ icon: FiFileText, title: "No evidence attached", description: "No evidence item is on this case record yet." }}
              />
            </Panel>

            {c.evidence.some((e) => e.relation.startsWith("Challenges")) && (
              <div className={cx("rounded-lg border px-4 py-3", BLOCKER_SKIN.warning)}>
                <p className="text-[12px] font-semibold">Authoritative sources disagree on this case</p>
                <p className={cx("mt-0.5 text-[12px] leading-snug", CONSOLE.body)}>
                  Source disagreement is shown explicitly. The console never silently selects a preferred
                  source, and a mandatory finding cannot rest on an unresolved conflict.
                </p>
              </div>
            )}

            <Panel title="Chain of custody rules">
              <ul className={cx("space-y-1.5 text-[13px]", CONSOLE.muted)}>
                <li>Original evidence is never overwritten; annotations and redactions create derived items linked to their source.</li>
                <li>Evidence exports include a manifest, integrity references, the policy version and the export actor.</li>
                <li>Every protected-content access is linked to this case ID and a recorded purpose.</li>
                <li>Deletion is unavailable where case retention or a legal hold requires preservation.</li>
                <li>Provenance is a signal, not a verdict — its presence or absence never proves content true, false, safe or harmful.</li>
              </ul>
            </Panel>
          </>
        )}

        {tab === "policy" && (
          <>
            {c.assessments.map((a) => (
              <Panel
                key={a.clause}
                title={`${a.clause} · ${a.family}`}
                action={<Badge tone={FINDING_TONE[a.finding]}>{label(a.finding)}</Badge>}
              >
                <p className={cx("rounded-lg border px-3 py-2 text-[13px] leading-snug", CONSOLE.inset, CONSOLE.body)}>
                  “{a.clause_text}”
                </p>
                <dl className="mt-3">
                  <DetailField label="Policy version at content time" value={a.version_at_content} />
                  <DetailField
                    label="Current policy version"
                    value={
                      a.version_current === a.version_at_content
                        ? `${a.version_current} — unchanged`
                        : `${a.version_current} — differs from the version in force at content time`
                    }
                  />
                  <DetailField label="Applicability" value={a.applicability} />
                  <DetailField label="Supporting evidence" value={a.supporting.join(", ") || "—"} />
                  <DetailField label="Challenging evidence" value={a.challenging.join(", ") || "None recorded"} />
                  <DetailField label="Reviewer confidence" value={a.reviewer_confidence} />
                  <DetailField label="Countervailing context" value={a.countervailing} />
                  <DetailField label="Jurisdiction context" value={a.jurisdiction} />
                  <DetailField label="Rationale" value={a.rationale} />
                </dl>
              </Panel>
            ))}
            <Panel title="Who owns the finding">
              <p className={cx("text-[13px] leading-[20px]", CONSOLE.body)}>
                Candidate clauses may be surfaced automatically, but a qualified human reviewer owns the
                final finding. Signal confidence and reviewer confidence are separate fields: a detector
                can be certain about what it heard and a reviewer still uncertain about what it means.
              </p>
              <p className={cx("mt-3 border-t pt-3 text-[12px] leading-snug", CONSOLE.divider, CONSOLE.faint)}>
                Reason categories and clause identifiers are controlled, versioned policy data — not
                interface constants. A change requires policy versioning and migration rules for
                historical reporting.
              </p>
            </Panel>
          </>
        )}

        {tab === "decision" && (
          <>
            <Panel
              title="Decision record"
              action={<Badge tone={FINDING_TONE[c.finding]}>{label(c.finding)}</Badge>}
            >
              <dl>
                <DetailField
                  label="Outcome"
                  value={c.decision.outcome === "—" ? "—" : OUTCOMES.find((o) => o.id === c.decision.outcome)?.label || c.decision.outcome}
                />
                <DetailField label="Scope" value={c.decision.scope} />
                <DetailField label="Recorded by" value={c.decision.recorded_by} />
                <DetailField label="Recorded at" value={c.decision.recorded_at} />
                <DetailField label="Approvers" value={c.decision.approvers} />
                <DetailField label="Reversibility" value={c.decision.reversibility} />
                <DetailField label="Rationale" value={c.decision.rationale} />
              </dl>
            </Panel>

            <Panel title="Proportionality review" description="Nine required questions. Deliberately not a score.">
              <dl>
                {PROPORTIONALITY_FACTORS.map(([factor, question]) => (
                  <DetailField key={factor} label={factor} value={question} />
                ))}
              </dl>
            </Panel>
          </>
        )}

        {tab === "enforcement" && (
          <>
            <Panel
              title="Enforcement execution"
              action={<Badge tone={ENFORCEMENT_TONE[c.enforcement.state]}>{label(c.enforcement.state)}</Badge>}
            >
              <dl>
                <DetailField label="Requested" value={c.enforcement.requested} />
                <DetailField label="Approved" value={c.enforcement.approved} />
                <DetailField label="Executing" value={c.enforcement.executing} />
                <DetailField label="Verified" value={c.enforcement.verified} />
                <DetailField label="Execution correlation" value={c.enforcement.correlation} />
                <DetailField label="Residual exposure" value={c.enforcement.residual} />
              </dl>
              <p className={cx("mt-3 border-t pt-3 text-[12px] leading-snug", CONSOLE.divider, CONSOLE.faint)}>
                {c.enforcement.note}
              </p>
            </Panel>

            <Panel title="Post-enforcement verification" count={c.verification.filter((v) => v.result.startsWith("Pending")).length} flush>
              {c.verification.length === 0 ? (
                <p className={cx("px-5 py-8 text-center text-[13px]", CONSOLE.faint)}>
                  No enforcement has executed, so there is nothing to verify.
                </p>
              ) : (
                <ul className={cx("divide-y", CONSOLE.divideY)}>
                  {c.verification.map((v) => (
                    <li key={v.check} className="transition-colors duration-150 hover:bg-slate-50 motion-reduce:transition-none dark:hover:bg-white/[0.03] flex flex-wrap items-start justify-between gap-3 px-5 py-3">
                      <div className="min-w-0">
                        <p className={cx("text-[13px] font-medium", CONSOLE.heading)}>{v.check}</p>
                        <p className={cx("mt-0.5 text-[12px]", CONSOLE.muted)}>{v.expected}</p>
                      </div>
                      <div className="shrink-0 text-right">
                        <Badge tone={v.result.startsWith("Verified") ? "success" : v.result.startsWith("Pending") ? "warning" : "neutral"}>
                          {v.result.split(" · ")[0]}
                        </Badge>
                        <p className={cx("mt-0.5 max-w-[280px] text-[11px] leading-snug", CONSOLE.faint)}>
                          {v.result.includes(" · ") ? v.result.split(" · ").slice(1).join(" · ") : ""} {v.at !== "—" ? `· ${v.at}` : ""}
                        </p>
                      </div>
                    </li>
                  ))}
                </ul>
              )}
            </Panel>
          </>
        )}

        {tab === "communications" && (
          <Panel title="Communications" count={c.communications.length} flush>
            {c.communications.length === 0 ? (
              <p className={cx("px-5 py-8 text-center text-[13px]", CONSOLE.faint)}>
                No communication has been sent on this case.
              </p>
            ) : (
              <ul className={cx("divide-y", CONSOLE.divideY)}>
                {c.communications.map((m) => (
                  <li key={`${m.kind}-${m.sent}`} className="transition-colors duration-150 hover:bg-slate-50 motion-reduce:transition-none dark:hover:bg-white/[0.03] px-5 py-3">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <p className={cx("text-[13px] font-semibold", CONSOLE.heading)}>{m.kind}</p>
                      <Badge tone={m.delivery === "Delivered" ? "success" : "warning"}>{m.delivery}</Badge>
                    </div>
                    <dl className="mt-1">
                      <DetailField label="Recipient" value={m.to} />
                      <DetailField label="Sent" value={m.sent} />
                      <DetailField label="Content" value={m.summary} />
                    </dl>
                  </li>
                ))}
              </ul>
            )}
            <div className={cx("border-t px-5 py-4", CONSOLE.divider)}>
              <p className={cx("text-[12px] leading-snug", CONSOLE.faint)}>
                Where a statement of reasons is required, it is generated from the structured verified
                decision fields and the applicable policy version — never from free-form reviewer
                shorthand, and never from internal notes copied verbatim. A public statement is not
                generated here; it routes to authorized Communications and Legal.
              </p>
            </div>
          </Panel>
        )}

        {tab === "appeal" && (
          <>
            <Panel
              title="Appeal"
              action={<Badge tone={APPEAL_TONE[c.appeal.state]}>{label(c.appeal.state)}</Badge>}
            >
              <dl>
                <DetailField label="Eligibility" value={c.appeal.eligibility} />
                <DetailField label="Deadline" value={c.appeal.deadline} />
                <DetailField label="Reviewer independence" value={c.appeal.reviewer} />
                <DetailField label="Submission" value={c.appeal.submission} />
                <DetailField label="Outcome" value={c.appeal.outcome} />
                <DetailField label="Enforcement effect" value={c.appeal.effect} />
              </dl>
            </Panel>
            <Panel title="Repeat-violation context" description="Decision context only. Never an automatic strike system.">
              <dl>
                <DetailField label="Final adverse outcomes counted" value={String(c.repeat.final_adverse)} />
                <DetailField label="Overturned outcomes excluded" value={String(c.repeat.overturned_excluded)} />
                <DetailField label="Visible window" value={c.repeat.window} />
                <DetailField label="Note" value={c.repeat.note} />
              </dl>
              {c.repeat.items.length > 0 && (
                <ul className={cx("mt-3 divide-y border-t", CONSOLE.divideY, CONSOLE.divider)}>
                  {c.repeat.items.map((r) => (
                    <li key={r.case_id} className="flex flex-wrap items-start justify-between gap-3 py-2">
                      <div className="min-w-0">
                        <p className={cx("text-[13px] font-medium", CONSOLE.heading)}>
                          <span className={type.mono}>{r.case_id}</span> · {r.family}
                        </p>
                        <p className={cx("mt-0.5 text-[11px] leading-snug", CONSOLE.faint)}>
                          {r.at} · {r.note}
                        </p>
                      </div>
                      <Badge tone={r.counts ? "danger" : "neutral"}>{r.counts ? "Counted" : "Not counted"}</Badge>
                    </li>
                  ))}
                </ul>
              )}
            </Panel>
          </>
        )}

        {tab === "history" && (
          <Panel title="Decision timeline" description="Signals, evidence, policy versions, decisions, enforcement and verification.">
            <ol className="relative space-y-4 pl-6">
              <span
                className="absolute left-[5px] top-1.5 h-[calc(100%-12px)] w-px bg-slate-200 dark:bg-white/10"
                aria-hidden="true"
              />
              {c.history.map((h) => (
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
              Every case and enforcement action correlates into the immutable audit record. This timeline
              references those records; it cannot edit them.
            </p>
          </Panel>
        )}
      </div>

      <div className={cx("mt-5 flex flex-wrap items-center gap-2 border-t pt-4", CONSOLE.divider)}>
        <span className={cx("inline-flex items-center gap-1.5 text-[11px]", CONSOLE.faint)}>
          <FiCheckCircle aria-hidden="true" />
          Case metadata permission never implies content-view permission.
        </span>
        <button
          type="button"
          onClick={onClose}
          className={cx("ml-auto rounded-lg px-3 py-1.5 text-[13px] font-medium", CONSOLE.segmentOff, focusRing)}
        >
          Close
        </button>
      </div>
    </Drawer>
  );
}
