// Live Broadcast Stats: the host's counter tiles follow the room between connects.
//
// The Stats tab paints from the socket's opening snapshot and is then kept current ONLY by the
// server's 15s `analytics/analytics.tick`, which this reducer merges over the previous block.
// These pin the client half of that contract with the backend's real wire names
// (services/broadcast.py::_interaction_totals): a tick that carries poll_votes moves the tile,
// and a tick that omits a field leaves the last real value standing.
import { describe, expect, it } from "vitest";
import { INITIAL_LIVE_STATE, reducer } from "./useLiveEvent";

const env = (channel, type, data) => ({ channel, type, data, ts: 0 });

function connected(analytics) {
  return reducer(INITIAL_LIVE_STATE, env("moderator", "snapshot", { event: { id: "e1" }, analytics }));
}

const AT_CONNECT = {
  viewers: 1, peak_viewers: 1, engagement: 0,
  questions_asked: 0, reactions: 0, poll_votes: 0, poll_participation: 0,
};

describe("analytics.tick", () => {
  it("updates Poll votes and turnout when a vote lands after the console connected", () => {
    const s = reducer(connected(AT_CONNECT), env("analytics", "analytics.tick", {
      viewers: 1, peak_viewers: 1, engagement: 60,
      questions_asked: 0, reactions: 0, poll_votes: 1, poll_participation: 100,
    }));
    expect(s.analytics.poll_votes).toBe(1);
    expect(s.analytics.poll_participation).toBe(100);
    expect(s.analytics.engagement).toBe(60);
  });

  it("updates Questions and message Reactions the same way", () => {
    const s = reducer(connected(AT_CONNECT), env("analytics", "analytics.tick", {
      questions_asked: 2, reactions: 5,
    }));
    expect(s.analytics.questions_asked).toBe(2);
    expect(s.analytics.reactions).toBe(5);
  });

  it("keeps the last real value for a field the tick omits (the outage tick)", () => {
    const voted = { ...AT_CONNECT, poll_votes: 3 };
    const s = reducer(connected(voted), env("analytics", "analytics.tick", {
      presence_available: false, t: "2026-09-29T00:00:00Z",
    }));
    expect(s.analytics.poll_votes).toBe(3);
  });

  it("does not reset other state when a tick arrives", () => {
    const s = reducer(connected(AT_CONNECT), env("analytics", "analytics.tick", { poll_votes: 1 }));
    expect(s.ready).toBe(true);
    expect(s.event).toEqual({ id: "e1" });
  });
});
