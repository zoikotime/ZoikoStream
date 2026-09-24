// The Producer Console's ACTIVE recording slot.
//
// ── THE BUG ────────────────────────────────────────────────────────────────────────────
// `state.recording` drives the console's recording indicator and timer. The reducer filled
// it with `data.status === "stopped" ? null : data` — a blacklist of exactly one terminal
// status. Every other non-running status therefore landed in the slot and started a timer,
// and the one that matters is "failed": a LiveKit egress that never started (the production
// 503, `twirp error unknown: no response from servers`) put a running timer on screen for a
// capture that was not happening.
//
// Whitelisting the two live states says the same thing in the form that stays correct: any
// status that is not "recording" or "paused" is not an active recording, including ones
// nobody has invented yet.
import { describe, expect, it } from "vitest";

import { reducer, INITIAL_LIVE_STATE } from "./useLiveEvent";

const update = (data) => ({ channel: "recording", type: "recording.update", data });
const apply = (...envelopes) => envelopes.reduce(reducer, INITIAL_LIVE_STATE);

const RUNNING = { id: "r1", status: "recording", enforced: true, egress_id: "EG_1" };
// What the backend now writes when LiveKit refuses the job.
const FAILED = {
  id: "r2", status: "failed", enforced: false, egress_id: null,
  error: "ServerError(code=unavailable, message=twirp error unknown: no response from servers, status=503)",
};

describe("a recording that is genuinely running", () => {
  it("fills the active slot, so the indicator and timer appear", () => {
    const state = apply(update(RUNNING));

    expect(state.recording).toEqual(RUNNING);
  });

  it("appears on the FIRST update, with no second envelope needed", () => {
    // The reported symptom was a timer that only showed up after clicking Record twice.
    // One envelope is all the console ever needs.
    const state = apply(update(RUNNING));

    expect(state.recording?.id).toBe("r1");
    expect(state.recordings).toHaveLength(1);
  });

  it("stays active while paused", () => {
    const state = apply(update(RUNNING), update({ ...RUNNING, status: "paused" }));

    expect(state.recording?.status).toBe("paused");
  });
});

describe("a recording that failed to start", () => {
  it("does NOT fill the active slot — no timer for a capture that is not happening", () => {
    const state = apply(update(FAILED));

    expect(state.recording).toBeNull();
  });

  it("is still kept in the log, with the provider's real error", () => {
    // Not active, but not discarded either: the diagnostics are the whole point.
    const state = apply(update(FAILED));

    expect(state.recordings).toHaveLength(1);
    expect(state.recordings[0].error).toMatch(/503/);
    expect(state.recordings[0].enforced).toBe(false);
  });

  it("clears an active recording that later fails mid-capture", () => {
    const state = apply(update(RUNNING), update({ ...RUNNING, status: "failed", enforced: false }));

    expect(state.recording).toBeNull();
  });
});

describe("terminal statuses in general", () => {
  it.each(["stopped", "failed"])("%s leaves the active slot", (status) => {
    const state = apply(update(RUNNING), update({ ...RUNNING, status }));

    expect(state.recording).toBeNull();
    // …and remains in the log.
    expect(state.recordings.at(-1).status).toBe(status);
  });

  it("keeps the log to one row per recording id", () => {
    const state = apply(update(RUNNING), update({ ...RUNNING, status: "stopped" }));

    expect(state.recordings).toHaveLength(1);
  });
});

describe("dual recording", () => {
  it("stays active while one path is still running", () => {
    // R2/R3 events run two independent egress jobs. One failing must not take the console's
    // timer down while the other is still capturing.
    const primary = { id: "p", role: "primary", status: "recording", enforced: true };
    const secondary = { id: "s", role: "secondary", status: "failed", enforced: false };
    const state = apply(update(secondary), update(primary));

    expect(state.recording?.id).toBe("p");
    expect(state.recordings).toHaveLength(2);
  });
});
