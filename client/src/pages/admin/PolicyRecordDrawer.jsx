import { useState } from "react";
import { FiFileText } from "react-icons/fi";
import { Badge, Button, DetailField, Panel, TabStrip, CONSOLE, cx, focusRing, type } from "../../components/admin";
import Drawer from "../../ui/Drawer";
import { POLICY_TONE, label } from "./governanceData";

// S14-V03 Policy Record, opened from a policy card.
//
// The rule this drawer exists to make visible: a policy is never edited in place. Every change
// creates a new immutable version, the prior version is marked Superseded, and the history is
// never overwritten — so the version in force when something happened stays recoverable.
//
// Nothing here mutates. §35 makes the Governance/Obligations Registry, Identity & Access and
// Audit build blockers, and policy approval is exactly the class of action that must fail closed
// without them.
const TABS = [
  { key: "overview", label: "Overview" },
  { key: "versions", label: "Versions" },
  { key: "approvals", label: "Approvals" },
  { key: "scope", label: "Scope" },
  { key: "controls", label: "Controls & evidence" },
  { key: "history", label: "Change history" },
];

const KIND_DOT = { policy: "bg-violet-500", approval: "bg-green-500", review: "bg-blue-500" };

export default function PolicyRecordDrawer({ policy: p, open, onClose }) {
  const [tab, setTab] = useState("overview");
  if (!p) return null;

  return (
    <Drawer open={open} onClose={onClose} width="w-[860px] max-w-[96vw]" title={`Policy record · ${p.policy_id}`}>
      <div className={cx("rounded-lg border p-4", CONSOLE.divider)}>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <h3 className={cx("text-[17px] font-semibold tracking-tight", CONSOLE.heading)}>{p.title}</h3>
            <p className={cx("mt-0.5 text-[11px]", type.mono, CONSOLE.faint)}>
              {p.policy_id} · {p.version}
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-1.5">
            <Badge tone={POLICY_TONE[p.status]} dot>
              {label(p.status)}
            </Badge>
            <Badge tone="neutral">{p.version}</Badge>
          </div>
        </div>
        <dl className="mt-3">
          <DetailField label="Owner" value={p.owner} />
          <DetailField label="Effective" value={p.effective} />
          <DetailField label="Next review" value={p.next_review} />
        </dl>
      </div>

      <div className="mt-4">
        <TabStrip
          tabs={TABS.map((t) => (t.key === "versions" ? { ...t, count: p.versions.length } : t))}
          active={tab}
          onChange={setTab}
          label="Policy record sections"
          idPrefix="pol"
        />
      </div>

      <div role="tabpanel" id={`polpanel-${tab}`} aria-labelledby={`pol-${tab}`} tabIndex={-1} className="mt-4 space-y-4">
        {tab === "overview" && (
          <Panel title="Requirements and change summary">
            <dl>
              <DetailField label="Requirements" value={p.requirements} />
              <DetailField label="Change summary" value={p.change_summary} />
              <DetailField label="Permitted exceptions" value={p.exceptions} />
              <DetailField label="Acknowledgment" value={p.acknowledgment} />
              <DetailField label="Mapped obligations" value={p.obligations.join(", ") || "None mapped"} />
            </dl>
          </Panel>
        )}

        {tab === "versions" && (
          <Panel title="Version history" description="Versions are immutable. An edit creates a new draft; it never overwrites a version." flush>
            <ul className={cx("divide-y", CONSOLE.divideY)}>
              {p.versions.map((v) => (
                <li key={v.version} className="transition-colors duration-150 hover:bg-slate-50 motion-reduce:transition-none dark:hover:bg-white/[0.03] flex flex-wrap items-start justify-between gap-3 px-5 py-3">
                  <div className="min-w-0">
                    <p className={cx("text-[13px] font-semibold", CONSOLE.heading)}>
                      <span className={type.mono}>{v.version}</span> · effective {v.effective}
                    </p>
                    <p className={cx("mt-0.5 text-[12px] leading-snug", CONSOLE.muted)}>{v.note}</p>
                  </div>
                  <Badge tone={POLICY_TONE[v.status]}>{label(v.status)}</Badge>
                </li>
              ))}
            </ul>
          </Panel>
        )}

        {tab === "approvals" && (
          <>
            <Panel title="Approval authority">
              <dl>
                <DetailField label="Approvers" value={p.approvers} />
                <DetailField label="Owner" value={p.owner} />
                <DetailField label="Separation of duties" value="The drafter cannot be the sole approver where the policy class requires separation." />
                <DetailField label="Effective date" value={p.effective} />
              </dl>
            </Panel>
            <Panel title="Policy change workflow" description="The ten steps a version passes through before it takes effect.">
              <ol className={cx("list-inside list-decimal space-y-1 text-[13px]", CONSOLE.muted)}>
                <li>Draft a new policy version.</li>
                <li>Identify changed obligations and impacted services and Organizations.</li>
                <li>Run the impact assessment across data, security, operations, contracts and accessibility.</li>
                <li>Obtain the required reviewers and approvals.</li>
                <li>Set a future effective date unless the emergency procedure applies.</li>
                <li>Create implementation actions with named owners.</li>
                <li>Notify affected audiences where required.</li>
                <li>Verify implementation evidence.</li>
                <li>Mark the prior version Superseded — never overwrite history.</li>
                <li>Schedule the next review.</li>
              </ol>
            </Panel>
          </>
        )}

        {tab === "scope" && (
          <Panel title="Effective scope">
            <dl>
              <DetailField label="Scope" value={p.scope} />
              <DetailField label="Requirements" value={p.requirements} />
              <DetailField label="Permitted exception class" value={p.exceptions} />
              <DetailField label="Acknowledgment audiences" value={p.acknowledgment} />
            </dl>
          </Panel>
        )}

        {tab === "controls" && (
          <Panel title="Mapped controls and required evidence">
            <dl>
              <DetailField label="Mapped controls" value={p.controls.join(", ") || "None mapped"} />
              <DetailField label="Required evidence" value={p.evidence} />
              <DetailField label="Mapped obligations" value={p.obligations.join(", ") || "None mapped"} />
            </dl>
            <p className={cx("mt-3 border-t pt-3 text-[12px] leading-snug", CONSOLE.divider, CONSOLE.faint)}>
              A mapping is not assurance. Whether each control is actually effective, and whether its
              evidence is current, is recorded separately in Control Assurance.
            </p>
          </Panel>
        )}

        {tab === "history" && (
          <Panel title="Change history">
            <ol className="relative space-y-4 pl-6">
              <span className="absolute left-[5px] top-1.5 h-[calc(100%-12px)] w-px bg-slate-200 dark:bg-white/10" aria-hidden="true" />
              {p.history.map((h) => (
                <li key={`${h.at}-${h.text}`} className="relative">
                  <span
                    className={cx(
                      "absolute -left-6 top-1.5 h-[11px] w-[11px] rounded-full ring-2 ring-white dark:ring-black",
                      KIND_DOT[h.kind] || "bg-slate-400"
                    )}
                    aria-hidden="true"
                  />
                  <p className={cx("flex flex-wrap items-baseline gap-x-2 text-[11px]", CONSOLE.faint)}>
                    <span className={cx("font-semibold uppercase tracking-wider", CONSOLE.muted)}>{h.kind}</span>
                    <span className={type.mono}>{h.at}</span>
                  </p>
                  <p className={cx("mt-0.5 text-[13px] leading-snug", CONSOLE.body)}>{h.text}</p>
                </li>
              ))}
            </ol>
          </Panel>
        )}
      </div>

      <div className={cx("mt-5 flex flex-wrap items-center gap-2 border-t pt-4", CONSOLE.divider)}>
        <Button variant="secondary" size="sm" leftIcon={FiFileText} title="Display only in this build">
          Create new draft version
        </Button>
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
