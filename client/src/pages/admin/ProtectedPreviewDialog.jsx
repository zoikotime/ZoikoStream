import { useState } from "react";
import { FiEye, FiLock } from "react-icons/fi";
import { Badge, Button, DetailField, CONSOLE, cx, focusRing, type } from "../../components/admin";
import Modal from "../../ui/Modal";

// S08-V02 §10.1 protected live preview gate.
//
// The rule this dialog exists to enforce: case metadata permission does not imply content-view
// permission. A reviewer can read every axis, every evidence reference and the whole timeline
// without ever seeing a frame of the customer's broadcast — and getting to the frame is a
// separate, justified, time-bounded, audited act.
//
// This build issues no session and plays no media: §36 makes Identity & Access and Audit build
// blockers, and the audit sink is precisely what makes this gate meaningful rather than
// decorative. What it does implement is the shape of the gate.
const DURATIONS = [
  [10, "10 minutes"],
  [20, "20 minutes"],
  [30, "30 minutes"],
];

// §10.1 + §30. Rendered rather than summarized, because the reviewer is about to operate under
// them and an unread rule is not a control.
const RULES = [
  ["Default", "No content is visible from case metadata permission alone."],
  ["Elevation", "Active Trust & Safety elevation with content-access scope is required."],
  ["Justification", "Case-linked and mandatory before access; stored with the access log."],
  ["Duration", "Time-bounded. Extension requires reauthorization."],
  ["Banner", "A persistent Protected Review banner stays visible for the whole session."],
  ["Watermark", "Watermarked with the operator and session identifier where supported."],
  ["Audio", "Never autoplays. Explicit unmute is required."],
  ["Download", "Raw download is not offered from live review."],
  ["Alternative", "Text and evidence-only review stays available when exposure should be minimized."],
  ["Audit", "Every access, seek and permitted action is audit-linked to this case."],
];

const MIN_JUSTIFICATION = 40;
const textareaCls = CONSOLE.textarea;
// Field skins come from the console tokens so a hover or focus change lands on every filter
// row at once, instead of being re-typed per page.
const selectCls = cx(CONSOLE.select, "w-full");

const PURPOSES = [
  "Assess a live safety signal before deciding on intervention",
  "Confirm or rule out an alleged policy violation",
  "Verify the scope of an existing enforcement action",
  "Review contested evidence for an appeal",
];

export default function ProtectedPreviewDialog({ caseRecord: c, open, onClose, onGrant }) {
  const [purpose, setPurpose] = useState(PURPOSES[0]);
  const [justification, setJustification] = useState("");
  const [minutes, setMinutes] = useState(10);
  const [acknowledged, setAcknowledged] = useState(false);

  if (!c) return null;

  const short = justification.trim().length < MIN_JUSTIFICATION;
  const ready = !short && acknowledged;

  const submit = () => {
    if (!ready) return;
    onGrant({ case_id: c.case_id, org: c.org, content_ref: c.content_ref, purpose, minutes });
    setJustification("");
    setAcknowledged(false);
    onClose();
  };

  return (
    <Modal open={open} onClose={onClose} size="xl" title="Request protected content preview">
      {/* The rules list makes this dialog taller than a laptop viewport and Modal's body does not
          scroll on its own, so the request scrolls and the decision row stays put. */}
      <div className="max-h-[56vh] overflow-y-auto pr-1">
        <div className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 dark:border-amber-500/25 dark:bg-amber-500/10">
          <p className="flex items-center gap-2 text-[13px] font-semibold text-amber-800 dark:text-amber-200">
            <FiLock aria-hidden="true" />
            Protected content preview
          </p>
          <p className={cx("mt-1 text-[12px] leading-snug", CONSOLE.body)}>
            You have permission to read this case. That is not permission to watch the
            Organization&apos;s broadcast. Record a case-linked purpose and a substantive
            justification to continue.
          </p>
        </div>

        <dl className="mt-4">
          <DetailField label="Case" value={`${c.case_id} · ${c.title}`} />
          <DetailField label="Organization" value={`${c.org} · ${c.tenant_id}`} />
          <DetailField label="Content scope" value={c.content_ref} />
          <DetailField label="Live context" value={c.live_context === "LIVE" ? "LIVE — currently reaching viewers" : c.live_context} />
          <DetailField label="Required profile" value="Trust & Safety with content-access scope" />
          <DetailField label="Elevation" value="Just-in-time. No standing production content access exists." />
          {c.unrepeatable && (
            <DetailField
              label="Unrepeatable event"
              value="This event occurs once. Reviewing it is not itself consequential, but any intervention that follows is irreversible."
            />
          )}
        </dl>

        <div className="mt-4 space-y-3">
          <label className="block">
            <span className={cx("mb-1 block text-[12px] font-medium", CONSOLE.body)}>Review purpose</span>
            <select value={purpose} onChange={(e) => setPurpose(e.target.value)} className={selectCls}>
              {PURPOSES.map((p) => (
                <option key={p} value={p}>
                  {p}
                </option>
              ))}
            </select>
          </label>

          <label className="block">
            <span className={cx("mb-1 block text-[12px] font-medium", CONSOLE.body)}>Session duration</span>
            <select value={minutes} onChange={(e) => setMinutes(Number(e.target.value))} className={selectCls}>
              {DURATIONS.map(([v, l]) => (
                <option key={v} value={v}>
                  {l}
                </option>
              ))}
            </select>
          </label>

          <label className="block">
            <span className={cx("mb-1 block text-[12px] font-medium", CONSOLE.body)}>
              Justification{" "}
              <span className={CONSOLE.faint}>· minimum {MIN_JUSTIFICATION} characters, stored with the access log</span>
            </span>
            <textarea
              rows={3}
              value={justification}
              onChange={(e) => setJustification(e.target.value)}
              placeholder="What are you trying to establish, and why does answering it require watching the content rather than reading the evidence already on the case?"
              className={textareaCls}
              aria-describedby="pp-justification-count"
            />
            <span
              id="pp-justification-count"
              className={cx("mt-1 block text-[11px]", type.mono, short ? "text-amber-600 dark:text-amber-400" : CONSOLE.faint)}
            >
              {justification.trim().length} / {MIN_JUSTIFICATION}
            </span>
          </label>

          <label className={cx("flex cursor-pointer items-start gap-2 text-[12px] leading-snug", CONSOLE.body)}>
            <input
              type="checkbox"
              checked={acknowledged}
              onChange={(e) => setAcknowledged(e.target.checked)}
              className={cx(CONSOLE.checkbox, "mt-0.5", focusRing)}
            />
            I acknowledge that this session views a customer&apos;s broadcast, that it is audit-linked to
            this case, that audio starts muted, and that the session expires automatically.
          </label>
        </div>

        <div className={cx("mt-4 rounded-lg border p-4", CONSOLE.inset)}>
          <p className={cx("mb-2 text-[10px] font-semibold uppercase tracking-[0.14em]", CONSOLE.faint)}>
            Rules that apply to this session
          </p>
          <dl>
            {RULES.map(([k, v]) => (
              <DetailField key={k} label={k} value={v} />
            ))}
          </dl>
        </div>
      </div>

      <div className={cx("mt-4 flex flex-wrap items-center justify-between gap-3 border-t pt-4", CONSOLE.divider)}>
        <Badge tone="brand">Wireframe · no session is issued and no media plays</Badge>
        <div className="flex gap-2">
          <Button variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" leftIcon={FiEye} disabled={!ready} onClick={submit}>
            Request preview
          </Button>
        </div>
      </div>
    </Modal>
  );
}
