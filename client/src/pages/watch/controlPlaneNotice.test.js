import { describe, expect, it } from "vitest";

import { controlPlaneNotice } from "./controlPlaneNotice";

// THE BUG this covers, captured from a real production watch page: the control WebSocket was
// failing repeatedly and the viewer was told nothing at all. useEventStream already exposed
// both `status` and `closeReason`, but EventWatch consumed `status` in only two places (a
// disabled control and the chat panel's connected dot) and never read `closeReason` — so a
// socket the SERVER had explicitly refused with close 1008 "Invalid or expired session"
// (reproduced directly against production) left the page looking entirely normal, with no
// explanation and nothing the viewer could act on.
describe("controlPlaneNotice", () => {
  it("says nothing while the socket is connecting or open", () => {
    // A banner on every first-connect would train viewers to ignore it.
    expect(controlPlaneNotice("connecting", null)).toBeNull();
    expect(controlPlaneNotice("open", null)).toBeNull();
  });

  it("surfaces the server's own refusal reason, and what to do about it", () => {
    const n = controlPlaneNotice("unauthorized", "Invalid or expired session");
    expect(n.tone).toBe("error");
    expect(n.text).toContain("Invalid or expired session");
    expect(n.text).toMatch(/sign in again/i);
  });

  it("still explains an unauthorized socket when the server gave no reason", () => {
    const n = controlPlaneNotice("unauthorized", null);
    expect(n.tone).toBe("error");
    expect(n.text).toMatch(/unavailable/i);
    expect(n.text).toMatch(/sign in again/i);
  });

  it("distinguishes a retrying socket from one that has given up", () => {
    expect(controlPlaneNotice("reconnecting", null).tone).toBe("warn");
    expect(controlPlaneNotice("offline", null).tone).toBe("error");
  });

  // The whole point of the separation: chat/polls/Q&A being down says NOTHING about whether
  // audio and video are arriving. Conflating them is what let a dead control plane read as
  // "this event is in preview".
  it("never implies the media stream is affected", () => {
    for (const status of ["reconnecting", "offline"]) {
      expect(controlPlaneNotice(status, null).text).toMatch(/video and audio are unaffected/i);
    }
  });
});
