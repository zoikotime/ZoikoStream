import { useState } from "react";
import { FiAlertTriangle, FiShield } from "react-icons/fi";
import { Badge, Button, DetailField, CONSOLE, cx, focusRing, type } from "../../components/admin";
import Modal from "../../ui/Modal";

// S08-V11 Organization Restriction / Suspension Proposal.
//
// The whole design of this dialog is an argument against one habit: reaching for suspension
// because it is the biggest lever available. So the capability list is per-capability and
// defaults to the narrow proposal on the case; full suspension is a separate, deliberate
// escalation that turns on typed confirmation and Governance review; and unresolved allegations
// are rendered as explicitly not counted next to the final ones that are.
//
// Nothing executes. §36 makes Identity & Access and Audit build blockers, and this is exactly
// the class of action that must fail closed without them.
const TYPED_PHRASE = "SUSPEND ORGANIZATION";

// Field skins come from the console tokens so a hover or focus change lands on every filter
// row at once, instead of being re-typed per page.
const inputCls = CONSOLE.field;
const textareaCls = CONSOLE.textarea;

function ImpactCard({ label, value, tone }) {
  return (
    <div className={cx(CONSOLE.inset, CONSOLE.panelHover, "transition-colors duration-150 motion-reduce:transition-none", "p-3")}>
      <p className={cx("text-[11px]", CONSOLE.muted)}>{label}</p>
      <p className={cx("mt-0.5 text-[13px] font-semibold leading-snug", tone || CONSOLE.heading)}>{value}</p>
    </div>
  );
}

export default function RestrictionProposalDialog({ caseRecord: c, open, onClose }) {
  const p = c?.proposal;
  // Local selection starts from the case's proposed scope, so the dialog opens on the narrow
  // proposal rather than on a blank slate an operator might fill in too widely.
  const [selected, setSelected] = useState(() =>
    new Set((p?.capabilities || []).filter((x) => x.proposed === "Restricted").map((x) => x.name))
  );
  const [fullSuspension, setFullSuspension] = useState(false);
  const [typed, setTyped] = useState("");
  const [justification, setJustification] = useState("");
  const [acknowledged, setAcknowledged] = useState(false);

  if (!c || !p) return null;

  const toggle = (name) => {
    const next = new Set(selected);
    next.has(name) ? next.delete(name) : next.add(name);
    setSelected(next);
  };

  const finalCases = p.supporting_cases.filter((x) => x.counts);
  const unresolved = p.supporting_cases.filter((x) => !x.final);
  const typedOk = !fullSuspension || typed.trim() === TYPED_PHRASE;
  const ready = selected.size > 0 && justification.trim().length >= 40 && acknowledged && typedOk;

  return (
    <Modal open={open} onClose={onClose} size="xl" title="Organization restriction proposal">
      <div className="max-h-[58vh] overflow-y-auto pr-1">
        <div className="rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 dark:border-rose-500/25 dark:bg-rose-500/10">
          <p className="flex items-center gap-2 text-[13px] font-semibold text-rose-700 dark:text-rose-300">
            <FiAlertTriangle aria-hidden="true" />
            High-impact Organization-level action
          </p>
          <p className={cx("mt-1 text-[12px] leading-snug", CONSOLE.body)}>
            Select only the capabilities the verified conduct implicates. Full suspension is not the
            default. Two distinct authorized approvers are required and the requester is excluded from
            approving their own proposal.
          </p>
        </div>

        <dl className="mt-4">
          <DetailField label="Organization" value={`${c.org} · ${c.tenant_id} · ${c.tier}`} />
          <DetailField label="Originating case" value={`${c.case_id} — ${c.title}`} />
          <DetailField label="Policy basis" value={`${c.assessments[0]?.clause} · ${c.policy_version}`} />
          <DetailField label="Effective time" value={p.effective_time} />
          <DetailField label="Recovery criteria" value={p.recovery_criteria} />
          <DetailField label="Communication plan" value={p.communication_plan} />
          <DetailField label="Governance review" value={p.governance_review} />
        </dl>

        {/* §20.20 — the operational context an approver needs before agreeing to anything. */}
        <p className={cx("mb-2 mt-4 text-[10px] font-semibold uppercase tracking-[0.14em]", CONSOLE.faint)}>
          Impact preview
        </p>
        <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
          <ImpactCard label="Active live events" value={p.active_live_events} />
          <ImpactCard
            label="Scheduled unrepeatable events"
            value={p.scheduled_unrepeatable}
            tone={p.scheduled_unrepeatable.startsWith("None") ? undefined : "text-rose-600 dark:text-rose-400"}
          />
          <ImpactCard label="Stored media" value={p.stored_media} />
          <ImpactCard label="Grantee access" value={p.grantee_access} />
          <ImpactCard label="API dependencies" value={p.api_dependencies} />
          <ImpactCard
            label="Legal holds"
            value={p.legal_holds}
            tone={p.legal_holds.startsWith("None") ? undefined : "text-amber-600 dark:text-amber-400"}
          />
        </div>

        {/* §20.19 — capability selection, not a single switch. */}
        <p className={cx("mb-2 mt-4 text-[10px] font-semibold uppercase tracking-[0.14em]", CONSOLE.faint)}>
          Capabilities proposed for restriction
        </p>
        <ul className={cx("divide-y rounded-lg border", CONSOLE.divideY, CONSOLE.divider)}>
          {p.capabilities.map((cap) => (
            <li key={cap.name} className="transition-colors duration-150 hover:bg-slate-50 motion-reduce:transition-none dark:hover:bg-white/[0.03] flex flex-wrap items-start gap-3 px-3 py-2.5">
              <input
                type="checkbox"
                id={`cap-${cap.name}`}
                checked={selected.has(cap.name)}
                onChange={() => toggle(cap.name)}
                className={cx(CONSOLE.checkbox, "mt-0.5", focusRing)}
              />
              <label htmlFor={`cap-${cap.name}`} className="min-w-0 flex-1 cursor-pointer">
                <span className={cx("block text-[13px] font-medium", CONSOLE.heading)}>{cap.name}</span>
                <span className={cx("block text-[11px] leading-snug", CONSOLE.faint)}>{cap.basis}</span>
              </label>
              <Badge tone={selected.has(cap.name) ? "danger" : "neutral"}>
                {selected.has(cap.name) ? "Restricted" : "Unchanged"}
              </Badge>
            </li>
          ))}
        </ul>

        {/* §20.21 — final and comparable vs unresolved, stated separately. */}
        <p className={cx("mb-2 mt-4 text-[10px] font-semibold uppercase tracking-[0.14em]", CONSOLE.faint)}>
          Supporting cases
        </p>
        <ul className={cx("divide-y rounded-lg border", CONSOLE.divideY, CONSOLE.divider)}>
          {p.supporting_cases.map((s) => (
            <li key={s.case_id} className="transition-colors duration-150 hover:bg-slate-50 motion-reduce:transition-none dark:hover:bg-white/[0.03] flex flex-wrap items-center justify-between gap-2 px-3 py-2">
              <span className={cx("text-[13px]", CONSOLE.body)}>
                <span className={cx("font-semibold", type.mono, CONSOLE.heading)}>{s.case_id}</span> · {s.outcome} · {s.at}
              </span>
              <Badge tone={s.counts ? "danger" : "neutral"}>
                {s.counts ? "Final · counted" : s.final ? "Final · excluded" : "Unresolved · not counted"}
              </Badge>
            </li>
          ))}
        </ul>
        <p className={cx("mt-1.5 text-[11px] leading-snug", CONSOLE.faint)}>
          {finalCases.length} final and comparable prior outcome{finalCases.length === 1 ? "" : "s"} support this
          proposal. {unresolved.length} unresolved allegation{unresolved.length === 1 ? " is" : "s are"} shown for
          context and never counted as a final violation.
        </p>

        <div className="mt-4 space-y-3">
          <label className="block">
            <span className={cx("mb-1 block text-[12px] font-medium", CONSOLE.body)}>
              Justification <span className={CONSOLE.faint}>· minimum 40 characters</span>
            </span>
            <textarea
              rows={3}
              value={justification}
              onChange={(e) => setJustification(e.target.value)}
              placeholder="Why is this scope proportionate to the verified harm, and why is a narrower action insufficient?"
              className={textareaCls}
            />
          </label>

          <label className={cx("flex cursor-pointer items-start gap-2 text-[12px] leading-snug", CONSOLE.body)}>
            <input
              type="checkbox"
              checked={fullSuspension}
              onChange={(e) => {
                setFullSuspension(e.target.checked);
                setTyped("");
              }}
              className={cx(CONSOLE.checkbox, "mt-0.5", focusRing)}
            />
            Escalate to a <strong>full Organization suspension</strong> instead of a capability restriction.
            This requires dual authorization, typed confirmation and a Governance review.
          </label>

          {fullSuspension && (
            <label className="block">
              <span className={cx("mb-1 block text-[12px] font-medium", CONSOLE.body)}>
                Type <span className={cx("font-semibold", type.mono, CONSOLE.heading)}>{TYPED_PHRASE}</span> to confirm
              </span>
              <input value={typed} onChange={(e) => setTyped(e.target.value)} className={inputCls} placeholder={TYPED_PHRASE} />
            </label>
          )}

          <label className={cx("flex cursor-pointer items-start gap-2 text-[12px] leading-snug", CONSOLE.body)}>
            <input
              type="checkbox"
              checked={acknowledged}
              onChange={(e) => setAcknowledged(e.target.checked)}
              className={cx(CONSOLE.checkbox, "mt-0.5", focusRing)}
            />
            I acknowledge that this is a proposal, that a separate authorized approver must agree, that I
            cannot approve it myself, and that execution runs through the authoritative Organization,
            entitlement and access services with post-action verification.
          </label>
        </div>
      </div>

      <div className={cx("mt-4 flex flex-wrap items-center justify-between gap-3 border-t pt-4", CONSOLE.divider)}>
        <Badge tone="brand">Wireframe · no proposal is submitted</Badge>
        <div className="flex gap-2">
          <Button variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button variant={fullSuspension ? "danger" : "primary"} leftIcon={FiShield} disabled={!ready} onClick={onClose}>
            {fullSuspension ? "Submit suspension proposal" : "Submit restriction proposal"}
          </Button>
        </div>
      </div>
    </Modal>
  );
}
