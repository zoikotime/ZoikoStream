import { useState } from "react";
import {
  FiAlertTriangle, FiArrowUpRight, FiCheckCircle, FiEye, FiInfo, FiLock, FiRefreshCw,
  FiRotateCcw, FiSlash, FiTool,
} from "react-icons/fi";
import {
  Badge, Button, DetailField, Panel, TabStrip, CONSOLE, cx, focusRing, type,
} from "../../components/admin";
import Drawer from "../../ui/Drawer";
import {
  AVAILABILITY_TONE, JOBS, LIFECYCLE_TONE, POLICY_TONE, PRESERVATION_TONE, PROCESSING_TONE,
  PROVENANCE_TONE, REGIONS, REPLAY_TONE, TRACK_TONE, label,
} from "./mediaData";

// S06-V02 Asset 360 — the per-asset control surface, opened from a Media Portfolio row.
//
// The rule that shapes every header and tab in here is STD-04: lifecycle, processing,
// availability, policy, preservation, legal hold, provenance and mode are separate axes and
// never collapse into one status. An asset can be READY, PARTIAL, DEGRADED, RESTRICTED,
// RETAINED and legally held all at once, and an operator has to be able to read each one.
//
// The second rule is the content boundary: opening this drawer shows technical METADATA. It
// does not authorize playback of the customer's media. Every route to essence goes through the
// protected access session, which the page owns so its banner survives this drawer closing.
//
// Nothing here mutates: §36 makes the Processing Orchestrator, Preserve Service, Identity &
// Access and Audit build blockers. Recovery buttons state their preconditions and are inert.

const TABS = [
  { key: "overview", label: "Overview" },
  { key: "essence", label: "Essence" },
  { key: "processing", label: "Processing" },
  { key: "tracks", label: "Tracks" },
  { key: "playback", label: "Playback" },
  { key: "recording", label: "Recording & Replay" },
  { key: "access", label: "Access" },
  { key: "preservation", label: "Preservation" },
  { key: "policy", label: "Policy" },
  { key: "provenance", label: "Provenance" },
  { key: "history", label: "History" },
];

const BLOCKER_SKIN = {
  danger: "border-rose-200 bg-rose-50 text-rose-700 dark:border-rose-500/25 dark:bg-rose-500/10 dark:text-rose-300",
  warning: "border-amber-200 bg-amber-50 text-amber-800 dark:border-amber-500/25 dark:bg-amber-500/10 dark:text-amber-200",
  info: "border-blue-200 bg-blue-50 text-blue-800 dark:border-blue-500/25 dark:bg-blue-500/10 dark:text-blue-200",
};
const BLOCKER_ICON = { danger: FiAlertTriangle, warning: FiAlertTriangle, info: FiInfo };

// §13.2 safe recovery actions. Every entry names its precondition, because an action whose
// precondition is unmet must read as unavailable rather than as untried.
const RECOVERY = [
  { label: "Retry failed job", icon: FiRotateCcw, pre: "Failure is retryable and the source and config are still valid", effect: "New attempt under the same logical job lineage" },
  { label: "Reprocess asset", icon: FiRefreshCw, pre: "Source essence is valid and policy permits", effect: "New processing job; healthy outputs stay until a replacement is verified" },
  { label: "Rebuild missing rendition", icon: FiTool, pre: "The derivative is absent or invalid and the source is available", effect: "Targets only the required derivative" },
  { label: "Revalidate output", icon: FiCheckCircle, pre: "No transformation is required", effect: "Runs technical validation; availability may be reevaluated" },
  { label: "Cancel queued or running job", icon: FiSlash, pre: "The job is in a cancelable state and an impact preview was shown", effect: "Stops the job; existing outputs are not deleted" },
  { label: "Escalate to Infrastructure", icon: FiArrowUpRight, pre: "The failure indicates regional or service impairment", effect: "Creates or links an operational incident" },
];

const KIND_DOT = {
  system: "bg-slate-400",
  processing: "bg-blue-500",
  rendition: "bg-amber-500",
  track: "bg-amber-500",
  recording: "bg-violet-500",
  preservation: "bg-green-500",
  policy: "bg-rose-500",
  governance: "bg-rose-500",
  provenance: "bg-blue-500",
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

// The eight state axes as one row of pills. Same component in the header and, on the Overview
// tab, as the technical summary — one definition, so the two can never drift apart.
function AxisRow({ asset }) {
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <Badge tone={asset.mode === "TEST" ? "warning" : "neutral"}>{asset.mode === "TEST" ? "Test mode" : "Live mode"}</Badge>
      <Badge tone={LIFECYCLE_TONE[asset.lifecycle]}>Lifecycle · {label(asset.lifecycle)}</Badge>
      <Badge tone={PROCESSING_TONE[asset.processing]}>Processing · {label(asset.processing)}</Badge>
      <Badge tone={AVAILABILITY_TONE[asset.availability]}>Availability · {label(asset.availability)}</Badge>
      <Badge tone={POLICY_TONE[asset.policy]}>Policy · {label(asset.policy)}</Badge>
      <Badge tone={PRESERVATION_TONE[asset.preservation]}>Preservation · {label(asset.preservation)}</Badge>
      <Badge tone={asset.legal_hold.active ? "danger" : "neutral"}>
        Legal hold · {asset.legal_hold.active ? `Yes (${asset.legal_hold.count})` : "No"}
      </Badge>
      <Badge tone={PROVENANCE_TONE[asset.provenance]}>Provenance · {label(asset.provenance)}</Badge>
    </div>
  );
}

function ContentGate({ onRequest, session }) {
  const active = session?.asset_id;
  return (
    <div
      className={cx(
        "rounded-lg border px-4 py-3",
        active
          ? "border-green-200 bg-green-50 dark:border-green-500/25 dark:bg-green-500/10"
          : "border-amber-200 bg-amber-50 dark:border-amber-500/25 dark:bg-amber-500/10"
      )}
    >
      <p className={cx("flex items-center gap-2 text-[13px] font-semibold", CONSOLE.heading)}>
        {active ? <FiEye aria-hidden="true" /> : <FiLock aria-hidden="true" />}
        {active ? "Customer content access active" : "Customer content access required"}
      </p>
      <p className={cx("mt-1 text-[12px] leading-snug", CONSOLE.body)}>
        {active
          ? `Scope ${session.asset_id} · purpose recorded · ${session.remaining} remaining. Playback starts muted and every access is logged.`
          : "Media playback is protected. Elevate access and record a valid operational purpose to continue. Metadata inspection does not imply permission to view customer content."}
      </p>
      {!active && (
        <Button variant="secondary" size="sm" leftIcon={FiLock} className="mt-3" onClick={onRequest}>
          Request content access
        </Button>
      )}
    </div>
  );
}

export default function Asset360Drawer({ asset, open, onClose, session, onRequestAccess }) {
  const [tab, setTab] = useState("overview");
  if (!asset) return null;

  const jobs = JOBS.filter((j) => j.asset_id === asset.asset_id);
  const assetSession = session?.asset_id === asset.asset_id ? session : null;

  return (
    <Drawer open={open} onClose={onClose} width="w-[920px] max-w-[96vw]" title={`Asset 360 · ${asset.name}`}>
      {/* §11.1 Header — every axis is its own field. */}
      <div className={cx("rounded-lg border p-4", CONSOLE.divider)}>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <h3 className={cx("text-[17px] font-semibold tracking-tight", CONSOLE.heading)}>{asset.name}</h3>
            <p className={cx("mt-0.5 text-[11px]", type.mono, CONSOLE.faint)}>
              {asset.asset_id} · {asset.kind} · {asset.tenant_id}
            </p>
          </div>
          <Badge tone="neutral">{regionLabel(asset.region)}</Badge>
        </div>

        <div className="mt-3">
          <AxisRow asset={asset} />
        </div>

        <div className="mt-3 grid gap-x-6 sm:grid-cols-2">
          <dl>
            <DetailField label="Organization" value={asset.org} />
            <DetailField label="Created / recorded" value={`${asset.created_local} · ${asset.created_utc}`} />
            <DetailField label="Source event / session" value={asset.source_event} />
          </dl>
          <dl>
            <DetailField label="Source origin" value={asset.source_origin} />
            <DetailField label="Size / duration" value={`${asset.size} · ${asset.duration}`} />
            <DetailField label="Last changed" value={`${asset.last_activity_local} · ${asset.last_activity_utc}`} />
          </dl>
        </div>
      </div>

      <div className="mt-4">
        <TabStrip
          tabs={TABS.map((t) => (t.key === "processing" ? { ...t, count: jobs.filter((j) => ["FAILED", "PARTIAL"].includes(j.state)).length } : t))}
          active={tab}
          onChange={setTab}
          label="Asset 360 sections"
          idPrefix="a360"
        />
      </div>

      <div role="tabpanel" id={`a360panel-${tab}`} aria-labelledby={`a360-${tab}`} tabIndex={-1} className="mt-4 space-y-4">
        {tab === "overview" && (
          <>
            {asset.blockers.length > 0 && (
              <div className="space-y-2">
                {asset.blockers.map((b) => {
                  const Icon = BLOCKER_ICON[b.tone] || FiInfo;
                  return (
                    <p
                      key={b.text}
                      className={cx("flex items-start gap-2 rounded-lg border px-4 py-3 text-[12px] leading-snug", BLOCKER_SKIN[b.tone])}
                    >
                      <Icon className="mt-0.5 shrink-0" aria-hidden="true" />
                      {b.text}
                    </p>
                  );
                })}
              </div>
            )}

            <Panel title="Technical summary">
              <div className="mb-3">
                <AxisRow asset={asset} />
              </div>
              <dl>
                <DetailField label="Renditions" value={`${asset.renditions.filter((r) => r.availability === "AVAILABLE").length} available of ${asset.renditions.length}`} />
                <DetailField label="Tracks" value={`${asset.tracks.filter((t) => t.state === "validated").length} validated of ${asset.tracks.filter((t) => t.state !== "absent").length} present`} />
                <DetailField label="Active jobs" value={jobs.filter((j) => ["QUEUED", "RUNNING"].includes(j.state)).length || "None"} />
                <DetailField label="Replay" value={label(asset.replay.state)} />
                <DetailField label="Deletion eligibility" value={asset.preservation_detail.deletion_eligibility} />
                <DetailField label="Retention" value={`${asset.preservation_detail.duration} · ${asset.preservation_detail.policy_source}`} />
              </dl>
            </Panel>

            <Panel title="Key actions" description="Requests into authoritative services. None of them writes state here.">
              <div className="flex flex-wrap gap-2">
                <Button variant="secondary" size="sm" leftIcon={FiLock} onClick={onRequestAccess}>
                  Request content access
                </Button>
                <Button variant="secondary" size="sm" leftIcon={FiRotateCcw} onClick={() => setTab("processing")}>
                  Open processing detail
                </Button>
                {asset.policy !== "NONE" && (
                  <Button variant="secondary" size="sm" leftIcon={FiAlertTriangle} onClick={() => setTab("policy")}>
                    Open policy case
                  </Button>
                )}
                {asset.legal_hold.active && (
                  <Button variant="secondary" size="sm" leftIcon={FiArrowUpRight} onClick={() => setTab("preservation")}>
                    Open Governance record
                  </Button>
                )}
              </div>
            </Panel>
          </>
        )}

        {tab === "essence" && (
          <>
            <ContentGate onRequest={onRequestAccess} session={assetSession} />
            <Panel title="Source media metadata" description="Technical metadata only. No storage-object URL and no key material.">
              <Pairs rows={asset.essence} />
            </Panel>
          </>
        )}

        {tab === "processing" && (
          <>
            <Panel title="Renditions and packaging" count={asset.renditions.filter((r) => r.availability !== "AVAILABLE").length} flush>
              {asset.renditions.length === 0 ? (
                <p className={cx("px-5 py-8 text-center text-[13px]", CONSOLE.faint)}>
                  No derivative has been produced for this asset yet.
                </p>
              ) : (
                <ul className={cx("divide-y", CONSOLE.divideY)}>
                  {asset.renditions.map((r) => (
                    <li key={r.id} className="transition-colors duration-150 hover:bg-slate-50 motion-reduce:transition-none dark:hover:bg-white/[0.03] px-5 py-3">
                      <div className="flex flex-wrap items-start justify-between gap-2">
                        <p className={cx("text-[13px] font-semibold", CONSOLE.heading)}>{r.profile}</p>
                        <Badge tone={AVAILABILITY_TONE[r.availability]}>{label(r.availability)}</Badge>
                      </div>
                      <dl className="mt-1">
                        <DetailField label="Rendition ID" value={r.id} />
                        <DetailField label="Audio" value={r.audio} />
                        <DetailField label="Packaging" value={r.packaging} />
                        <DetailField label="Validation" value={r.validation} />
                        <DetailField label="Lineage" value={r.lineage} />
                      </dl>
                    </li>
                  ))}
                </ul>
              )}
            </Panel>

            <Panel title="Processing jobs for this asset" flush>
              {jobs.length === 0 ? (
                <p className={cx("px-5 py-8 text-center text-[13px]", CONSOLE.faint)}>
                  No processing job applies to this asset.
                </p>
              ) : (
                <ul className={cx("divide-y", CONSOLE.divideY)}>
                  {jobs.map((j) => (
                    <li key={j.job_id} className="transition-colors duration-150 hover:bg-slate-50 motion-reduce:transition-none dark:hover:bg-white/[0.03] px-5 py-3">
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <p className={cx("text-[13px] font-semibold", type.mono, CONSOLE.heading)}>{j.job_id}</p>
                        <Badge tone={PROCESSING_TONE[j.state]}>{label(j.state)}</Badge>
                      </div>
                      <dl className="mt-1">
                        <DetailField label="Job type" value={j.type} />
                        <DetailField label="Region" value={regionLabel(j.region)} />
                        <DetailField label="Attempt" value={`${j.attempt} of ${j.attempts_total} recorded`} />
                        <DetailField label="Failure code" value={j.failure_code} />
                        <DetailField label="Failure summary" value={j.failure_summary} />
                        <DetailField label="Outputs" value={j.outputs} />
                        <DetailField label="Timing" value={`created ${j.created} · started ${j.started} · completed ${j.completed}`} />
                      </dl>
                    </li>
                  ))}
                </ul>
              )}
            </Panel>

            <Panel title="Safe recovery actions" description="Each action names the precondition it requires.">
              <ul className="space-y-2">
                {RECOVERY.map((a) => (
                  <li key={a.label} className="flex flex-wrap items-start gap-3">
                    <Button variant="secondary" size="sm" leftIcon={a.icon} className="w-[240px] justify-start">
                      {a.label}
                    </Button>
                    <div className="min-w-[220px] flex-1">
                      <p className={cx("text-[12px] leading-snug", CONSOLE.body)}>{a.pre}</p>
                      <p className={cx("text-[11px] leading-snug", CONSOLE.faint)}>{a.effect}</p>
                    </div>
                  </li>
                ))}
              </ul>
              <p className={cx("mt-3 border-t pt-3 text-[11px] leading-snug", CONSOLE.divider, CONSOLE.faint)}>
                An accepted retry is not a repair. Success appears only once the authoritative
                processing service verifies the required output and availability is reevaluated.
              </p>
            </Panel>
          </>
        )}

        {tab === "tracks" && (
          <>
            <div className="grid gap-3 sm:grid-cols-2">
              {asset.tracks.map((t) => (
                <div key={t.cls} className={cx(CONSOLE.panel, CONSOLE.panelHover, "transition-colors duration-150 motion-reduce:transition-none", "p-4")}>
                  <div className="flex flex-wrap items-start justify-between gap-2">
                    <p className={cx("text-[13px] font-semibold", CONSOLE.heading)}>{t.cls}</p>
                    <Badge tone={TRACK_TONE[t.state]}>
                      {{ validated: "Validated", provisioned: "Provisioned", failed: "Failed", pending: "In progress", absent: "Not provided" }[t.state]}
                    </Badge>
                  </div>
                  <dl className="mt-1">
                    <DetailField label="Language" value={t.language} />
                    <DetailField label="Detail" value={t.detail} />
                    <DetailField label="Processing" value={label(t.processing)} />
                    <DetailField label="Generation" value={t.automated ? "Automated — no human review implied" : "Not automated"} />
                  </dl>
                </div>
              ))}
            </div>
            <Panel title="Accessibility rules that apply here">
              <ul className={cx("space-y-1.5 text-[13px]", CONSOLE.muted)}>
                <li>The presence of a caption file does not mean &ldquo;accessible&rdquo;. Provisioned, validated and quality-reviewed are distinct states.</li>
                <li>Language labels use controlled BCP 47-compatible values where the backend contract supports them.</li>
                <li>Caption and transcript content is customer content and follows the same protected-access rules.</li>
                <li>Automated generation is identified as automated. Human review is claimed only when an evidence artifact confirms it.</li>
              </ul>
            </Panel>
          </>
        )}

        {tab === "playback" && (
          <>
            <ContentGate onRequest={onRequestAccess} session={assetSession} />
            <Panel title="Authorized playback configuration">
              <Pairs rows={asset.playback} />
            </Panel>
            <Panel title="Player diagnostics">
              <p className={cx("text-[13px] leading-[20px]", CONSOLE.body)}>
                The diagnostic player is keyboard-operable, exposes caption and audio-description
                track selection where present, shows its playback state as text, and never autoplays
                with sound. It loads only inside an active content-access session.
              </p>
              <p className={cx("mt-3 border-t pt-3 text-[12px] leading-snug", CONSOLE.divider, CONSOLE.faint)}>
                No player is mounted in this build. Direct storage-object URLs are never exposed;
                content access uses scoped, expiring, service-mediated URLs.
              </p>
            </Panel>
          </>
        )}

        {tab === "recording" && (
          <>
            <Panel title="Recording continuity">
              <Pairs
                rows={[
                  ["Capture source", asset.recording.capture_source],
                  ["Recording state", asset.recording.state],
                  ["Start", `${asset.recording.start_local} · ${asset.recording.start_utc}`],
                  ["End", `${asset.recording.end_local} · ${asset.recording.end_utc}`],
                  ["Continuity", asset.recording.continuity],
                  ["Finalization", asset.recording.finalization],
                  ["Resulting asset", asset.recording.resulting_asset],
                  ["Storage target", asset.recording.storage_target],
                  ["Retention", asset.recording.retention],
                  ["Failures", asset.recording.failures],
                ]}
              />
            </Panel>
            <Panel
              title="Replay readiness"
              action={<Badge tone={REPLAY_TONE[asset.replay.state]}>{label(asset.replay.state)}</Badge>}
            >
              <Pairs
                rows={[
                  ["Replay state", label(asset.replay.state)],
                  ["Availability window", asset.replay.window],
                  ["Access policy", asset.replay.access_policy],
                ]}
              />
              <p className={cx("mt-3 border-t pt-3 text-[12px] leading-snug", CONSOLE.divider, CONSOLE.faint)}>
                {asset.replay.note} Recording completion, replay preparation, replay availability,
                access policy and retention are independent states.
              </p>
            </Panel>
          </>
        )}

        {tab === "access" && (
          <Panel title="Effective access policy" description="Summary and security references — not tenant configuration editing.">
            <Pairs rows={asset.access} />
            <p className={cx("mt-3 border-t pt-3 text-[12px] leading-snug", CONSOLE.divider, CONSOLE.faint)}>
              Cross-tenant authorization is enforced server-side on every request. No super admin can
              silently impersonate an Organization user to view this media.
            </p>
          </Panel>
        )}

        {tab === "preservation" && (
          <>
            <Panel title="Retention, archive and deletion">
              <Pairs
                rows={[
                  ["Effective retention policy", asset.preservation_detail.effective_policy],
                  ["Policy source", asset.preservation_detail.policy_source],
                  ["Duration / condition", asset.preservation_detail.duration],
                  ["Next evaluation", asset.preservation_detail.next_evaluation],
                  ["Preservation state", label(asset.preservation_detail.state)],
                  ["Storage locations", asset.preservation_detail.storage_locations],
                  ["Archive state", asset.preservation_detail.archive_state],
                  ["Deletion eligibility", asset.preservation_detail.deletion_eligibility],
                  ["Deletion schedule", asset.preservation_detail.deletion_schedule],
                  ["Legal hold", asset.preservation_detail.legal_hold],
                  ["Dependencies", asset.preservation_detail.dependencies],
                ]}
              />
              <div className="mt-3 flex flex-wrap gap-2">
                <Button variant="secondary" size="sm">
                  Request archive restore
                </Button>
                <Button variant="secondary" size="sm">
                  Open Governance record
                </Button>
              </div>
            </Panel>
            <Panel title="Retention precedence" description="The UI always shows which policy won, and why.">
              <ol className={cx("list-inside list-decimal space-y-1 text-[13px]", CONSOLE.muted)}>
                <li>Legal hold or binding preservation order</li>
                <li>Regulatory or contractual requirement</li>
                <li>Organization-specific governed policy</li>
                <li>Plan or service entitlement</li>
                <li>Platform default</li>
              </ol>
              <p className={cx("mt-3 border-t pt-3 text-[12px] leading-snug", CONSOLE.divider, CONSOLE.faint)}>
                Media cannot remove or weaken a legal hold, change retention to avoid deletion, or
                execute deletion while a hold or conflict exists. Those routes go to Governance.
              </p>
            </Panel>
          </>
        )}

        {tab === "policy" && (
          <>
            <Panel
              title="Policy enforcement"
              action={<Badge tone={POLICY_TONE[asset.policy_detail.state]}>{label(asset.policy_detail.state)}</Badge>}
            >
              <Pairs
                rows={[
                  ["Enforcement state", label(asset.policy_detail.state)],
                  ["Decision / case reference", asset.policy_detail.decision_ref],
                  ["Authority", asset.policy_detail.authority],
                  ["Effective from", asset.policy_detail.effective_from],
                  ["Expiry", asset.policy_detail.expiry],
                  ["Technical execution", asset.policy_detail.execution],
                ]}
              />
              {asset.policy_detail.state !== "NONE" && (
                <div className="mt-3 flex flex-wrap gap-2">
                  <Button variant="secondary" size="sm">
                    Open Trust &amp; Safety case
                  </Button>
                  <Button variant="secondary" size="sm">
                    Re-execute enforcement
                  </Button>
                </div>
              )}
            </Panel>
            <Panel title="What Media may and may not do here">
              <div className="grid gap-4 sm:grid-cols-2">
                <div>
                  <p className={cx("mb-1.5 text-[10px] font-semibold uppercase tracking-[0.14em]", CONSOLE.faint)}>Media may</p>
                  <ul className={cx("space-y-1 text-[12px]", CONSOLE.muted)}>
                    <li>Display the enforcement state and linked decision reference</li>
                    <li>Confirm delivery reflects the authoritative decision</li>
                    <li>Retry a failed technical execution of an authorized restriction</li>
                    <li>Open the case and record enforcement failures</li>
                  </ul>
                </div>
                <div>
                  <p className={cx("mb-1.5 text-[10px] font-semibold uppercase tracking-[0.14em]", CONSOLE.faint)}>Media must not</p>
                  <ul className={cx("space-y-1 text-[12px]", CONSOLE.muted)}>
                    <li>Create a moderation finding or violation classification</li>
                    <li>Downgrade, remove or expire a Trust &amp; Safety decision</li>
                    <li>Restore content because an operator believes the restriction is wrong</li>
                    <li>Use a processing failure as a reason to bypass enforcement</li>
                  </ul>
                </div>
              </div>
            </Panel>
          </>
        )}

        {tab === "provenance" && (
          <Panel
            title="Provenance and integrity"
            action={<Badge tone={PROVENANCE_TONE[asset.provenance]}>{label(asset.provenance)}</Badge>}
          >
            <Pairs
              rows={[
                ["Presence", asset.provenance_detail.presence],
                ["Verification", label(asset.provenance_detail.verification)],
                ["Specification / profile", asset.provenance_detail.spec],
                ["Signer / claim generator", asset.provenance_detail.signer],
                ["Content binding", asset.provenance_detail.binding],
                ["Actions / ingredients", asset.provenance_detail.ingredients],
                ["Redactions", asset.provenance_detail.redactions],
                ["Validation time", asset.provenance_detail.validated_at],
                ["Warnings", asset.provenance_detail.warnings],
              ]}
            />
            <p className={cx("mt-3 border-t pt-3 text-[12px] leading-snug", CONSOLE.divider, CONSOLE.faint)}>
              &ldquo;Verified&rdquo; means the governed verifier validated the credential, its binding
              and the relevant trust rules. It does not mean every assertion about the content is
              factually true, safe, lawful, or approved by ZoikoStream.
            </p>
          </Panel>
        )}

        {tab === "history" && (
          <Panel title="Media history" description="System events, interventions, evidence and audit correlation.">
            <ol className="relative space-y-4 pl-6">
              <span
                className="absolute left-[5px] top-1.5 h-[calc(100%-12px)] w-px bg-slate-200 dark:bg-white/10"
                aria-hidden="true"
              />
              {asset.history.map((h) => (
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
            <p className={cx("mt-4 border-t pt-3 text-[11px] leading-snug", CONSOLE.divider, CONSOLE.faint)}>
              Privileged mutations and protected reads are immutably correlated in the audit service.
              This view references those records; it cannot edit them.
            </p>
          </Panel>
        )}
      </div>

      <div className={cx("mt-5 flex flex-wrap items-center gap-2 border-t pt-4", CONSOLE.divider)}>
        <span className={cx("text-[11px]", CONSOLE.faint)}>
          Metadata inspection does not imply permission to view customer content.
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
