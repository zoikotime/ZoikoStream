// Run with:  node --test client/src/pages/admin/consoleData.test.js
//
// One check per module, for the failure mode that actually matters on all four pages: a state
// value with no entry in its display map. Badge falls back to the neutral tone, so a QUARANTINED
// asset, a Blocked gate, a CRITICAL case or a residency CONFLICT would render as quiet grey and
// read as "nothing to see here". That is the one bug on an operations console that gets acted on.
//
// It also guards the spec rules that are structural rather than visual, and therefore easy to
// erode without noticing:
//   · STD-04 — no combined `status` field on a media asset or a Trust & Safety case.
//   · NO UNIVERSAL COMPLIANCE SCORE — no score/percentage field in the Governance payload.
//   · NO AUTOMATED GUILT — confidence is never coloured as severity; a confirmed finding always
//     names a clause and the policy version in force at content time.
//   · Verified-not-optimistic — an ACTIVE enforcement always carries a verification timestamp,
//     and a PROPOSED one never does.
//   · FAIL-CLOSED DISPLAY — residency UNKNOWN, assurance NOT_TESTED and NOT_APPLICABLE are never
//     rendered in the success tone.
//   · The referential joins the pages rely on (jobs → assets, controls → evidence, regions).
//
// No test framework: node:test and node:assert ship with the runtime.
import { test } from "node:test";
import assert from "node:assert/strict";

import {
  ASSETS, AVAILABILITY_TONE, JOBS, LIFECYCLE_TONE, POLICY_TONE, PRESERVATION_TONE,
  PROCESSING_TONE, PROVENANCE_TONE, REGIONS, REPLAY_TONE, TRACK_TONE,
} from "./mediaData.js";
import { EVENTS, EVIDENCE_LABEL, EVIDENCE_TONE, GATE_LABEL, GATE_TONE, RISK_LABEL, RISK_TONE } from "./readinessData.js";
import {
  APPEAL_TONE, CASES, CASE_STATE_TONE, CONFIDENCE_TONE, CONTENT_TYPES,
  AVAILABILITY_TONE as TS_AVAILABILITY_TONE, ENFORCEMENT_TONE, EVIDENCE_INTEGRITY_TONE,
  FINDING_TONE, LIVE_TONE, OUTCOMES, POLICY_FAMILIES, PRIORITY_TONE as TS_PRIORITY_TONE,
  SIGNAL_LABEL, SIGNAL_TREATMENT, SLA_TONE,
} from "./trustSafetyData.js";
import {
  ACCESS_REVIEWS, ACTIONS, ASSURANCE_TONE, BOARD_ITEMS, CONTROLS, DPIAS, DPIA_TONE, EVIDENCE,
  EVIDENCE_TONE as GOV_EVIDENCE_TONE, EXCEPTIONS, EXCEPTION_TONE, HOLD_TONE, LEGAL_HOLDS,
  OBLIGATIONS, OBLIGATION_TONE, POLICIES, POLICY_TONE as GOV_POLICY_TONE, PRIVACY_REQUESTS,
  PRIVACY_TONE, PROCESSING_ACTIVITIES, RESIDENCY, RESIDENCY_TONE, SUBPROCESSORS,
  SUBPROCESSOR_TONE, TIMING_TONE,
} from "./governanceData.js";

const mediaRegions = new Set(REGIONS.map((r) => r.id));

// ─────────────────────────────────────────────────────────────────────────────
// Media
// ─────────────────────────────────────────────────────────────────────────────
test("every media state value has a display tone", () => {
  const axes = [
    ["lifecycle", LIFECYCLE_TONE],
    ["processing", PROCESSING_TONE],
    ["availability", AVAILABILITY_TONE],
    ["policy", POLICY_TONE],
    ["preservation", PRESERVATION_TONE],
    ["provenance", PROVENANCE_TONE],
  ];
  for (const a of ASSETS) {
    for (const [field, map] of axes) {
      assert.ok(map[a[field]], `${a.asset_id}: ${field}="${a[field]}" has no tone`);
    }
    assert.ok(REPLAY_TONE[a.replay.state], `${a.asset_id}: replay "${a.replay.state}" has no tone`);
    assert.ok(["LIVE", "TEST"].includes(a.mode), `${a.asset_id}: mode "${a.mode}" is not LIVE or TEST`);
    for (const t of a.tracks) {
      assert.ok(TRACK_TONE[t.state], `${a.asset_id}/${t.cls}: track state "${t.state}" has no tone`);
      assert.ok(PROCESSING_TONE[t.processing], `${a.asset_id}/${t.cls}: processing "${t.processing}" has no tone`);
    }
    for (const r of a.renditions) {
      assert.ok(AVAILABILITY_TONE[r.availability], `${a.asset_id}/${r.id}: availability has no tone`);
    }
  }
});

test("no media asset carries a combined status field (STD-04)", () => {
  for (const a of ASSETS) {
    assert.equal(a.status, undefined, `${a.asset_id} has a collapsed status field`);
  }
});

test("media assets and jobs resolve their references", () => {
  const ids = ASSETS.map((a) => a.asset_id);
  assert.equal(new Set(ids).size, ids.length, "duplicate asset_id");
  for (const a of ASSETS) {
    assert.ok(mediaRegions.has(a.region), `${a.asset_id}: unknown region ${a.region}`);
    assert.equal(typeof a.created_days_ago, "number", `${a.asset_id}: created_days_ago must be numeric`);
  }
  for (const j of JOBS) {
    assert.ok(ids.includes(j.asset_id), `${j.job_id}: points at unknown asset ${j.asset_id}`);
    assert.ok(PROCESSING_TONE[j.state], `${j.job_id}: state "${j.state}" has no tone`);
    assert.ok(mediaRegions.has(j.region), `${j.job_id}: unknown region ${j.region}`);
  }
});

// ─────────────────────────────────────────────────────────────────────────────
// Event Readiness
// ─────────────────────────────────────────────────────────────────────────────
test("every readiness gate, risk and evidence state has a label and a tone", () => {
  for (const e of EVENTS) {
    assert.ok(GATE_TONE[e.gate] && GATE_LABEL[e.gate], `${e.event_id}: gate "${e.gate}" is unmapped`);
    assert.ok(RISK_TONE[e.risk] && RISK_LABEL[e.risk], `${e.event_id}: risk "${e.risk}" is unmapped`);
    for (const field of ["primary_path", "backup_path", "access", "captions", "preservation"]) {
      const v = e[field];
      // "configured" is rendered by its own pill; everything else must be evidence vocabulary.
      assert.ok(v === "configured" || EVIDENCE_TONE[v], `${e.event_id}: ${field}="${v}" is unmapped`);
    }
    for (const r of e.requirements) {
      assert.ok(EVIDENCE_TONE[r.state] && EVIDENCE_LABEL[r.state], `${e.event_id}/${r.id}: state "${r.state}" is unmapped`);
    }
  }
});

test("every readiness event carries the full requirement catalogue", () => {
  const expected = EVENTS[0].requirements.map((r) => r.id);
  assert.equal(expected.length, 15, "the catalogue should hold 15 rules");
  for (const e of EVENTS) {
    assert.deepEqual(
      e.requirements.map((r) => r.id),
      expected,
      `${e.event_id} does not render the whole catalogue`
    );
    assert.equal(typeof e.days_out, "number", `${e.event_id}: days_out must be numeric for horizon filtering`);
    assert.equal(typeof e.completed, "boolean", `${e.event_id}: completed must be boolean`);
  }
  const ids = EVENTS.map((e) => e.id);
  assert.equal(new Set(ids).size, ids.length, "duplicate event id");
});

// Only what the spec actually guarantees, so the check stays true rather than convenient.
//
// Expiring is deliberately NOT unsatisfied: §11.1 makes it "valid now, expires before start" —
// a warning, not a failure. Treating it as a blocker would turn every safety-window warning
// into a false Blocked, which is the mirror image of greenwashing and just as wrong.
const UNSATISFIED = ["missing", "failed", "expired", "conflicting", "indeterminate"];

test("blocker counts, gate states and requirements agree", () => {
  for (const e of EVENTS) {
    const openMandatory = e.requirements.filter((r) => r.mandatory && UNSATISFIED.includes(r.state));

    // The count in the pipeline column and the list in the record drawer are read side by side.
    assert.equal(
      e.blockers,
      e.blocker_details.length,
      `${e.event_id}: blockers=${e.blockers} but ${e.blocker_details.length} blockers are detailed`
    );

    // §31.9 — expired or superseded evidence cannot satisfy a mandatory rule, so a Passed event
    // must have nothing mandatory outstanding. This is the greenwashing guard.
    if (e.gate === "PASSED") {
      assert.equal(e.blockers, 0, `${e.event_id}: Passed with ${e.blockers} blockers`);
      assert.deepEqual(
        openMandatory.map((r) => r.id),
        [],
        `${e.event_id}: Passed with unsatisfied mandatory requirements`
      );
    }

    // §3.1 — Conditional means every mandatory item is satisfied and only conditions remain.
    if (e.gate === "CONDITIONAL") {
      assert.equal(e.blockers, 0, `${e.event_id}: Conditional with ${e.blockers} mandatory blockers`);
    }

    // §3.1 — Blocked means at least one mandatory requirement is unsatisfied.
    if (e.gate === "BLOCKED") {
      assert.ok(e.blockers > 0, `${e.event_id}: Blocked with no blocker`);
      assert.ok(openMandatory.length > 0, `${e.event_id}: Blocked but every mandatory rule is satisfied`);
    }
  }
});

// ─────────────────────────────────────────────────────────────────────────────
// Trust & Safety
// ─────────────────────────────────────────────────────────────────────────────
test("every Trust & Safety state value has a display tone", () => {
  const axes = [
    ["priority", TS_PRIORITY_TONE],
    ["case_state", CASE_STATE_TONE],
    ["confidence", CONFIDENCE_TONE],
    ["finding", FINDING_TONE],
    ["enforcement_state", ENFORCEMENT_TONE],
    ["appeal_state", APPEAL_TONE],
    ["content_availability", TS_AVAILABILITY_TONE],
    ["live_context", LIVE_TONE],
    ["sla_state", SLA_TONE],
  ];
  for (const c of CASES) {
    for (const [field, map] of axes) {
      assert.ok(map[c[field]], `${c.case_id}: ${field}="${c[field]}" has no tone`);
    }
    assert.ok(["LIVE", "TEST"].includes(c.mode), `${c.case_id}: mode "${c.mode}" is not LIVE or TEST`);
    assert.ok(POLICY_FAMILIES.includes(c.reason_family), `${c.case_id}: reason_family "${c.reason_family}" is not in the taxonomy`);
    assert.ok(CONTENT_TYPES.includes(c.content_type), `${c.case_id}: content_type "${c.content_type}" is not controlled`);
    for (const s of c.signal_sources) {
      assert.ok(SIGNAL_LABEL[s], `${c.case_id}: signal source "${s}" has no label`);
      assert.ok(SIGNAL_TREATMENT[s], `${c.case_id}: signal source "${s}" has no treatment text — it could read as a verdict`);
    }
    for (const e of c.evidence) {
      assert.ok(EVIDENCE_INTEGRITY_TONE[e.integrity], `${c.case_id}/${e.evidence_id}: integrity "${e.integrity}" has no tone`);
    }
  }
});

test("no Trust & Safety case carries a combined status field (STD-04)", () => {
  for (const c of CASES) {
    assert.equal(c.status, undefined, `${c.case_id} has a collapsed status field`);
  }
});

test("signal confidence is never coloured as severity (NO AUTOMATED GUILT)", () => {
  // Colouring confidence red/green smuggles automated guilt in through the palette: a
  // high-confidence detector hit would read as a decided violation.
  for (const [level, tone] of Object.entries(CONFIDENCE_TONE)) {
    assert.ok(!["danger", "warning", "success"].includes(tone), `confidence ${level} uses the severity tone "${tone}"`);
  }
});

test("a confirmed finding always names a clause and a policy version", () => {
  for (const c of CASES) {
    if (c.finding !== "VIOLATION_CONFIRMED") continue;
    assert.ok(c.assessments.length > 0, `${c.case_id}: confirmed finding with no assessment`);
    for (const a of c.assessments) {
      assert.ok(a.clause?.length, `${c.case_id}: assessment without a clause ID`);
      assert.ok(a.version_at_content?.length, `${c.case_id}: assessment without the version in force at content time`);
      assert.ok(a.rationale?.length > 40, `${c.case_id}: assessment rationale is not substantive`);
    }
  }
});

test("enforcement is never shown as done before it is verified", () => {
  for (const c of CASES) {
    // ACTIVE means the action is in force, which is only claimable from an observed post-action
    // state. NONE and PROPOSED must not carry a verification at all.
    if (c.enforcement.state === "ACTIVE") {
      assert.notEqual(c.enforcement.verified, "—", `${c.case_id}: ACTIVE enforcement with no verification`);
    }
    if (["NONE", "PROPOSED"].includes(c.enforcement.state)) {
      assert.equal(c.enforcement.verified, "—", `${c.case_id}: ${c.enforcement.state} enforcement claims a verification`);
    }
    if (c.decision.outcome !== "—") {
      assert.ok(
        OUTCOMES.some((o) => o.id === c.decision.outcome),
        `${c.case_id}: decision outcome "${c.decision.outcome}" is not a controlled outcome code`
      );
    }
  }
});

test("repeat-violation counting excludes unresolved and overturned outcomes", () => {
  const ids = CASES.map((x) => x.case_id);
  assert.equal(new Set(ids).size, ids.length, "duplicate case_id");
  for (const c of CASES) {
    const counted = c.repeat.items.filter((r) => r.counts);
    assert.equal(c.repeat.final_adverse, counted.length, `${c.case_id}: final_adverse disagrees with the counted items`);
    for (const r of c.repeat.items) {
      if (!r.final) assert.equal(r.counts, false, `${c.case_id}/${r.case_id}: unresolved allegation counted as final`);
      if (r.note.includes("OVERTURNED")) assert.equal(r.counts, false, `${c.case_id}/${r.case_id}: overturned outcome counted`);
    }
  }
});

// ─────────────────────────────────────────────────────────────────────────────
// Governance
// ─────────────────────────────────────────────────────────────────────────────
test("every Governance state value has a display tone", () => {
  const axisMap = {
    residency: RESIDENCY_TONE,
    hold: HOLD_TONE,
    privacy: PRIVACY_TONE,
    dpia: DPIA_TONE,
    assurance: ASSURANCE_TONE,
    exception: EXCEPTION_TONE,
    obligation: OBLIGATION_TONE,
  };
  for (const a of ACTIONS) {
    const map = axisMap[a.state_axis];
    assert.ok(map, `${a.id}: unknown state axis "${a.state_axis}"`);
    assert.ok(map[a.state], `${a.id}: state "${a.state}" has no tone on the ${a.state_axis} axis`);
    assert.ok(GOV_EVIDENCE_TONE[a.evidence_state], `${a.id}: evidence state "${a.evidence_state}" has no tone`);
    assert.ok(TIMING_TONE[a.timing], `${a.id}: timing "${a.timing}" has no tone`);
  }
  for (const o of OBLIGATIONS) assert.ok(OBLIGATION_TONE[o.lifecycle], `${o.obligation_id}: lifecycle has no tone`);
  for (const p of POLICIES) {
    assert.ok(GOV_POLICY_TONE[p.status], `${p.policy_id}: status has no tone`);
    for (const v of p.versions) assert.ok(GOV_POLICY_TONE[v.status], `${p.policy_id}/${v.version}: version status has no tone`);
  }
  for (const r of RESIDENCY) assert.ok(RESIDENCY_TONE[r.posture], `${r.id}: posture "${r.posture}" has no tone`);
  for (const h of LEGAL_HOLDS) assert.ok(HOLD_TONE[h.state], `${h.hold_id}: state has no tone`);
  for (const p of PRIVACY_REQUESTS) {
    assert.ok(PRIVACY_TONE[p.state], `${p.id}: request state has no tone`);
    assert.ok(TIMING_TONE[p.timing], `${p.id}: deadline state has no tone`);
  }
  for (const d of DPIAS) assert.ok(DPIA_TONE[d.state], `${d.id}: DPIA state has no tone`);
  for (const s of SUBPROCESSORS) {
    assert.ok(SUBPROCESSOR_TONE[s.state], `${s.id}: state has no tone`);
    assert.ok(GOV_EVIDENCE_TONE[s.evidence_state], `${s.id}: evidence state has no tone`);
  }
  for (const c of CONTROLS) {
    assert.ok(ASSURANCE_TONE[c.assurance_state], `${c.control_id}: assurance state has no tone`);
    assert.ok(GOV_EVIDENCE_TONE[c.evidence_state], `${c.control_id}: evidence state has no tone`);
  }
  for (const e of EXCEPTIONS) assert.ok(EXCEPTION_TONE[e.state], `${e.id}: state has no tone`);
  for (const e of EVIDENCE) assert.ok(GOV_EVIDENCE_TONE[e.status], `${e.id}: status has no tone`);
  for (const b of BOARD_ITEMS) assert.ok(TIMING_TONE[b.timing], `${b.id}: timing has no tone`);
  for (const a of ACCESS_REVIEWS) assert.ok(TIMING_TONE[a.timing], `${a.id}: timing has no tone`);
});

test("Governance exposes no universal compliance score", () => {
  // §33.1 and the NO UNIVERSAL COMPLIANCE SCORE rule. A single percentage hides expired evidence,
  // high-severity exceptions, jurisdictional gaps and untested controls behind one figure.
  const banned = /^(score|compliance_score|percent|percentage|compliance|health|grade|rating)$/i;
  const collections = {
    ACTIONS, OBLIGATIONS, POLICIES, PROCESSING_ACTIVITIES, RESIDENCY, LEGAL_HOLDS,
    PRIVACY_REQUESTS, DPIAS, SUBPROCESSORS, CONTROLS, EXCEPTIONS, BOARD_ITEMS, EVIDENCE,
    ACCESS_REVIEWS,
  };
  for (const [name, rows] of Object.entries(collections)) {
    for (const row of rows) {
      for (const key of Object.keys(row)) {
        assert.ok(!banned.test(key), `${name} carries a "${key}" field — that is a compliance score`);
      }
    }
  }
});

test("fail-closed states are never rendered in the success tone", () => {
  // Unknown placement is not permitted placement; not tested is not effective; not applicable is
  // not effective. Each of these being green is a specific lie the spec names.
  assert.notEqual(RESIDENCY_TONE.UNKNOWN, "success", "residency UNKNOWN renders as compliant");
  assert.notEqual(RESIDENCY_TONE.AT_RISK, "success", "residency AT_RISK renders as compliant");
  assert.notEqual(ASSURANCE_TONE.NOT_TESTED, "success", "assurance NOT_TESTED renders as effective");
  assert.notEqual(ASSURANCE_TONE.NOT_APPLICABLE, "success", "assurance NOT_APPLICABLE renders as effective");
  assert.notEqual(GOV_EVIDENCE_TONE.EXPIRED, "success", "expired evidence renders as current");
  assert.notEqual(GOV_EVIDENCE_TONE.MISSING, "success", "missing evidence renders as current");
});

test("Governance records resolve their references and time boundaries", () => {
  const evidenceIds = new Set(EVIDENCE.map((e) => e.id));
  const controlIds = new Set(CONTROLS.map((c) => c.control_id));
  const obligationIds = new Set(OBLIGATIONS.map((o) => o.obligation_id));

  for (const c of CONTROLS) {
    for (const id of c.evidence) assert.ok(evidenceIds.has(id), `${c.control_id}: references unknown evidence ${id}`);
    for (const id of c.obligations) assert.ok(obligationIds.has(id), `${c.control_id}: references unknown obligation ${id}`);
    // §33.16 — a control cannot stay Effective on expired or missing evidence without reevaluation.
    if (["EXPIRED", "MISSING"].includes(c.evidence_state)) {
      assert.notEqual(c.assurance_state, "EFFECTIVE", `${c.control_id}: EFFECTIVE on ${c.evidence_state} evidence`);
    }
  }
  for (const o of OBLIGATIONS) {
    for (const id of o.controls) assert.ok(controlIds.has(id), `${o.obligation_id}: references unknown control ${id}`);
    // §33.3 — automated regulatory intelligence cannot make an obligation active on its own.
    if (o.applicability_status === "PROPOSED") {
      assert.notEqual(o.lifecycle, "ACTIVE", `${o.obligation_id}: suggested applicability but ACTIVE lifecycle`);
    }
  }
  // §33.17 / §33.18 — every exception has a mandatory time boundary and none auto-renews silently.
  for (const e of EXCEPTIONS) {
    assert.ok(e.expires_at?.length && !/indefinite|never/i.test(e.expires_at), `${e.id}: no mandatory expiry`);
    assert.ok(!/auto-?renew/i.test(e.renewal) || /never|not|no /i.test(e.renewal), `${e.id}: renewal permits silent auto-renewal`);
    assert.ok(e.baseline_note?.length, `${e.id}: no baseline note — the requirement must stay visible as unsatisfied`);
  }
  // §33.19 — a Board approval control is only offered against a complete structured package.
  for (const b of BOARD_ITEMS) {
    assert.ok(b.package.length >= 4, `${b.id}: review package is too thin to approve against`);
  }
  const ids = ACTIONS.map((a) => a.id);
  assert.equal(new Set(ids).size, ids.length, "duplicate governance action id");
});
