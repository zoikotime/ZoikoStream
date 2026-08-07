import { useState } from "react";
import { FiAlertTriangle, FiTrash2 } from "react-icons/fi";
import { Badge, Button, DetailField, CONSOLE, cx, focusRing, type } from "../../components/admin";
import Modal from "../../ui/Modal";

// S14-V06 §13.2 deletion preview, and the hold-release variant of the same dialog.
//
// Both exist to prevent the same two mistakes:
//   · Treating job acceptance as completion. Deletion is complete only on verified per-system
//     execution, so the dialog says what "success" would actually require.
//   · Letting a hold be released, or a held asset deleted, by one person. A hold always blocks
//     conflicting deletion, and release needs dual authorization plus post-release re-evaluation.
//
// Nothing executes. §35 makes the retention evaluator, the legal-hold service, Identity & Access
// and Audit build blockers — this is precisely the class of action that must fail closed without
// them.
const TYPED_PHRASE = "DELETE";
const RELEASE_PHRASE = "RELEASE HOLD";

// Field skins come from the console tokens so a hover or focus change lands on every filter
// row at once, instead of being re-typed per page.
const inputCls = CONSOLE.field;
const textareaCls = CONSOLE.textarea;

// mode: "deletion" (preview a scheduled deletion) | "release" (release a legal hold)
export default function DeletionPreviewDialog({ mode = "deletion", preview, hold, open, onClose }) {
  const [typed, setTyped] = useState("");
  const [reason, setReason] = useState("");
  const [acknowledged, setAcknowledged] = useState(false);

  const release = mode === "release";
  const phrase = release ? RELEASE_PHRASE : TYPED_PHRASE;
  const subject = release ? hold : preview;
  if (!subject) return null;

  const ready = typed.trim() === phrase && reason.trim().length >= 40 && acknowledged;

  return (
    <Modal
      open={open}
      onClose={onClose}
      size="xl"
      title={release ? "Release legal hold" : "Deletion preview"}
    >
      <div className="max-h-[56vh] overflow-y-auto pr-1">
        <div className="rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 dark:border-rose-500/25 dark:bg-rose-500/10">
          <p className="flex items-center gap-2 text-[13px] font-semibold text-rose-700 dark:text-rose-300">
            <FiAlertTriangle aria-hidden="true" />
            {release ? "Releasing a hold can expose preserved data to deletion" : "Irreversible for the essence itself"}
          </p>
          <p className={cx("mt-1 text-[12px] leading-snug", CONSOLE.body)}>
            {release
              ? "Release requires dual authorization, an impact preview, step-up authentication, immutable audit, and a post-release re-evaluation of the retention and deletion rules that then govern."
              : "Governance authorizes the rule. Authoritative storage, media and data services perform the deletion, and completion requires verified per-system results — job acceptance is not completion."}
          </p>
        </div>

        {release ? (
          <dl className="mt-4">
            <DetailField label="Hold" value={`${hold.hold_id} · ${hold.org}`} />
            <DetailField label="Authority / basis" value={`${hold.authority} · ${hold.basis}`} />
            <DetailField label="Scope" value={hold.scope} />
            <DetailField label="Custodians / systems" value={hold.custodians} />
            <DetailField label="Deletion consequences" value={hold.conflicts} />
            <DetailField label="Evidence collection" value={hold.collection} />
            <DetailField label="Release approval" value={hold.release_approval} />
            <DetailField label="Release verification" value={hold.release_verification} />
          </dl>
        ) : (
          <>
            <dl className="mt-4">
              <DetailField label="Target" value={preview.target} />
              <DetailField label="Data classes" value={preview.data_classes} />
              <DetailField label="Systems" value={preview.systems} />
              <DetailField label="Organizations" value={preview.organizations} />
              <DetailField label="Regions" value={preview.regions} />
              <DetailField label="Dependencies" value={preview.dependencies} />
              <DetailField label="Legal holds" value={preview.holds} />
              <DetailField label="Irreversibility" value={preview.irreversibility} />
              <DetailField label="Authorization" value={preview.authorization} />
              <DetailField label="Completion" value={preview.completion} />
            </dl>

            <p className={cx("mb-2 mt-4 text-[10px] font-semibold uppercase tracking-[0.14em]", CONSOLE.faint)}>
              Per-asset outcome
            </p>
            <ul className={cx("divide-y rounded-lg border", CONSOLE.divideY, CONSOLE.divider)}>
              {preview.blocked.map((b) => (
                <li key={b.asset} className="transition-colors duration-150 hover:bg-slate-50 motion-reduce:transition-none dark:hover:bg-white/[0.03] flex flex-wrap items-start justify-between gap-2 px-3 py-2.5">
                  <div className="min-w-0">
                    <p className={cx("text-[13px] font-medium", CONSOLE.heading)}>{b.asset}</p>
                    <p className={cx("text-[11px] leading-snug", CONSOLE.faint)}>
                      {b.reason} — {b.outcome}
                    </p>
                  </div>
                  <Badge tone="danger">Blocked</Badge>
                </li>
              ))}
              {preview.eligible.map((e) => (
                <li key={e.asset} className="transition-colors duration-150 hover:bg-slate-50 motion-reduce:transition-none dark:hover:bg-white/[0.03] flex flex-wrap items-start justify-between gap-2 px-3 py-2.5">
                  <div className="min-w-0">
                    <p className={cx("text-[13px] font-medium", CONSOLE.heading)}>{e.asset}</p>
                    <p className={cx("text-[11px] leading-snug", CONSOLE.faint)}>
                      {e.reason} — {e.outcome}
                    </p>
                  </div>
                  <Badge tone={e.outcome.includes("held for review") ? "warning" : "info"}>
                    {e.outcome.includes("held for review") ? "Held" : "Eligible"}
                  </Badge>
                </li>
              ))}
            </ul>
          </>
        )}

        <div className="mt-4 space-y-3">
          <label className="block">
            <span className={cx("mb-1 block text-[12px] font-medium", CONSOLE.body)}>
              Reason <span className={CONSOLE.faint}>· minimum 40 characters, stored with the audit record</span>
            </span>
            <textarea
              rows={3}
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder={
                release
                  ? "Why has the basis for preservation ended, and what retention rule governs once the hold is released?"
                  : "Why is this deletion authorized now, and which effective retention rule requires it?"
              }
              className={textareaCls}
            />
          </label>

          <label className="block">
            <span className={cx("mb-1 block text-[12px] font-medium", CONSOLE.body)}>
              Type <span className={cx("font-semibold", type.mono, CONSOLE.heading)}>{phrase}</span> to confirm
            </span>
            <input value={typed} onChange={(e) => setTyped(e.target.value)} className={inputCls} placeholder={phrase} />
          </label>

          <label className={cx("flex cursor-pointer items-start gap-2 text-[12px] leading-snug", CONSOLE.body)}>
            <input
              type="checkbox"
              checked={acknowledged}
              onChange={(e) => setAcknowledged(e.target.checked)}
              className={cx(CONSOLE.checkbox, "mt-0.5", focusRing)}
            />
            {release
              ? "I acknowledge that a second authorized approver is required, that I cannot approve my own release, and that retention and deletion rules are re-resolved and verified after release."
              : "I acknowledge that held assets are rejected authoritatively, that essence deletion is irreversible, and that success is claimed only on verified per-system completion."}
          </label>
        </div>
      </div>

      <div className={cx("mt-4 flex flex-wrap items-center justify-between gap-3 border-t pt-4", CONSOLE.divider)}>
        <Badge tone="brand">Wireframe · nothing is deleted or released</Badge>
        <div className="flex gap-2">
          <Button variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="danger" leftIcon={FiTrash2} disabled={!ready} onClick={onClose}>
            {release ? "Request hold release" : "Request authorized deletion"}
          </Button>
        </div>
      </div>
    </Modal>
  );
}
