import { useState } from "react";
import { FiEye, FiLock } from "react-icons/fi";
import {
  Badge,
  Button,
  DetailField,
  CONSOLE,
  cx,
  focusRing,
  type,
} from "../../components/admin";
import Modal from "../../ui/Modal";

// S06-V08 Protected Media Access Session (§12).
//
// The default is that no customer content essence is visible. Opening Asset 360 metadata does
// not authorize playback of the customer's media — the essence preview is a SEPARATE
// privileged session with a case reference, a substantive justification, a bounded duration,
// a persistent banner and an immutable access log.
//
// This build creates no session and plays no media: there is no content-access service or
// audit sink to call yet, and §36 makes the Audit service a build blocker for exactly this
// workflow. What it does implement is the shape of the gate, so the workflow can be reviewed
// and so nothing in the interface implies that metadata access ever granted content access.
const DURATIONS = [
  [15, "15 minutes"],
  [30, "30 minutes"],
  [60, "60 minutes"],
];

const PURPOSES = [
  "Support investigation — playback fault reported by the Organization",
  "Trust & Safety case review",
  "Processing failure diagnosis",
  "Preservation or evidence-export verification",
];

// §12 access rules. Rendered rather than summarized, because the operator is about to accept
// them and an unread rule is not a control.
const RULES = [
  ["Default", "No content essence is visible."],
  ["Scope", "This single asset. Never broad cross-tenant browsing."],
  ["Duration", "Shortest practical window; policy-controlled."],
  ["Justification", "Stored with the access log and validated for substance."],
  [
    "Playback",
    "Starts muted and requires an explicit play. A banner stays visible throughout.",
  ],
  ["Download", "Disabled. It requires separate explicit permission."],
  ["PII", "Grantee and user-level identifiers are gated separately."],
  ["Audit", "The read is audited even though no mutation occurs."],
];

const MIN_JUSTIFICATION = 40;
const textareaCls = CONSOLE.textarea;
// Field skins come from the console tokens so a hover or focus change lands on every filter
// row at once, instead of being re-typed per page.
const inputCls = CONSOLE.field;
const selectCls = cx(CONSOLE.select, "w-full");

export default function ContentAccessDialog({ asset, open, onClose, onGrant }) {
  const [purpose, setPurpose] = useState(PURPOSES[0]);
  const [caseRef, setCaseRef] = useState("");
  const [justification, setJustification] = useState("");
  const [minutes, setMinutes] = useState(15);
  const [acknowledged, setAcknowledged] = useState(false);

  if (!asset) return null;

  const short = justification.trim().length < MIN_JUSTIFICATION;
  const ready = !short && caseRef.trim().length > 0 && acknowledged;

  const submit = () => {
    if (!ready) return;
    onGrant({
      asset_id: asset.asset_id,
      asset_name: asset.name,
      org: asset.org,
      purpose,
      caseRef,
      minutes,
    });
    setJustification("");
    setCaseRef("");
    setAcknowledged(false);
    onClose();
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      size="xl"
      title="Request customer content access"
    >
      {/* The form plus the access rules are taller than a laptop viewport, and Modal's body
          does not scroll on its own — so the request scrolls and the decision row below it
          stays put. An operator must never have to scroll to find the Cancel button. */}
      <div className="max-h-[58vh] overflow-y-auto pr-1">
        <div
          className={cx(
            "rounded-lg border px-4 py-3",
            "border-amber-200 bg-amber-50 dark:border-amber-500/25 dark:bg-amber-500/10",
          )}
        >
          <p className="flex items-center gap-2 text-[13px] font-semibold text-amber-800 dark:text-amber-200">
            <FiLock aria-hidden="true" />
            Media playback is protected
          </p>
          <p className={cx("mt-1 text-[12px] leading-snug", CONSOLE.body)}>
            Elevate access and record a valid operational purpose to continue.
            Opening this asset&apos;s metadata did not authorize playback of the
            customer&apos;s media.
          </p>
        </div>

        <dl className="mt-4">
          <DetailField
            label="Asset scope"
            value={`${asset.name} · ${asset.asset_id}`}
          />
          <DetailField
            label="Organization"
            value={`${asset.org} · ${asset.tenant_id}`}
          />
          <DetailField
            label="Data class"
            value="Customer media essence (protected)"
          />
          <DetailField
            label="Required profile"
            value="Platform Operations, Trust & Safety, or authorized Support"
          />
          <DetailField
            label="Elevation"
            value="Just-in-time · no standing production content access exists"
          />
          <DetailField label="Policy validation">
            Organization scope, purpose, media classification, legal
            restrictions and maximum duration are validated by the policy engine
            before any session is issued.
          </DetailField>
        </dl>

        <div className="mt-4 space-y-3">
          <label className="block">
            <span
              className={cx("mb-1 block text-[12px] font-medium", CONSOLE.body)}
            >
              Operational purpose
            </span>
            <select
              value={purpose}
              onChange={(e) => setPurpose(e.target.value)}
              className={selectCls}
            >
              {PURPOSES.map((p) => (
                <option key={p} value={p}>
                  {p}
                </option>
              ))}
            </select>
          </label>

          <div className="grid gap-3 sm:grid-cols-2">
            <label className="block">
              <span
                className={cx(
                  "mb-1 block text-[12px] font-medium",
                  CONSOLE.body,
                )}
              >
                Case / incident / support reference
              </span>
              <input
                value={caseRef}
                onChange={(e) => setCaseRef(e.target.value)}
                placeholder="SUP-2026-… / TS-2026-… / INC-…"
                className={inputCls}
              />
            </label>
            <label className="block">
              <span
                className={cx(
                  "mb-1 block text-[12px] font-medium",
                  CONSOLE.body,
                )}
              >
                Session duration
              </span>
              <select
                value={minutes}
                onChange={(e) => setMinutes(Number(e.target.value))}
                className={selectCls}
              >
                {DURATIONS.map(([v, l]) => (
                  <option key={v} value={v}>
                    {l}
                  </option>
                ))}
              </select>
            </label>
          </div>

          <label className="block">
            <span
              className={cx("mb-1 block text-[12px] font-medium", CONSOLE.body)}
            >
              Justification{" "}
              <span className={CONSOLE.faint}>
                · minimum {MIN_JUSTIFICATION} characters, stored with the access
                log
              </span>
            </span>
            <textarea
              rows={3}
              value={justification}
              onChange={(e) => setJustification(e.target.value)}
              placeholder="What are you looking for, and why does answering it require watching the customer's media rather than reading its technical state?"
              className={textareaCls}
              aria-describedby="ca-justification-count"
            />
            <span
              id="ca-justification-count"
              className={cx(
                "mt-1 block text-[11px]",
                type.mono,
                short ? "text-amber-600 dark:text-amber-400" : CONSOLE.faint,
              )}
            >
              {justification.trim().length} / {MIN_JUSTIFICATION}
            </span>
          </label>

          <label
            className={cx(
              "flex cursor-pointer items-start gap-2 text-[12px] leading-snug",
              CONSOLE.body,
            )}
          >
            <input
              type="checkbox"
              checked={acknowledged}
              onChange={(e) => setAcknowledged(e.target.checked)}
              className={cx(
                "mt-0.5 h-4 w-4 shrink-0 rounded border-slate-300 accent-violet-600 dark:border-slate-600",
                focusRing,
              )}
            />
            I acknowledge that this session views a customer&apos;s content,
            that every access, seek and track selection is logged, and that the
            session expires automatically.
          </label>
        </div>

        <div className={cx("mt-4 rounded-lg border p-4", CONSOLE.inset)}>
          <p
            className={cx(
              "mb-2 text-[10px] font-semibold uppercase tracking-[0.14em]",
              CONSOLE.faint,
            )}
          >
            Access rules that apply to this session
          </p>
          <dl>
            {RULES.map(([k, v]) => (
              <DetailField key={k} label={k} value={v} />
            ))}
          </dl>
        </div>
      </div>

      <div
        className={cx(
          "mt-4 flex flex-wrap items-center justify-between gap-3 border-t pt-4",
          CONSOLE.divider,
        )}
      >
        <Badge tone="brand">
          Wireframe · no session is issued and no media plays
        </Badge>
        <div className="flex gap-2">
          <Button variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button
            variant="primary"
            leftIcon={FiEye}
            disabled={!ready}
            onClick={submit}
          >
            Request access
          </Button>
        </div>
      </div>
    </Modal>
  );
}
