// What the user is told when a request gets no answer.
//
// ── THE BUG ─────────────────────────────────────────────────────────────────────────────
// errMsg's last resort was `e.message`, and for any request that never received a response
// axios sets that to the bare string "Network Error". The browser uses that one string for a
// dropped connection, a DNS failure, AND a response it refused to deliver because it carried
// no CORS headers — which is what every unhandled 500 looked like until app/main.py started
// answering those inside the CORS layer.
//
// Printed verbatim it names the wrong layer and reads as a verdict on the reader's own
// connection. On the Billing page it was interpolated into a sentence about their account:
// "Current subscription detected. Network Error Plan changes are paused" — so one discarded
// response told an operator their subscription was in trouble.
import { describe, expect, it } from "vitest";

import { errMsg, transportMessage } from "./api";

/** axios's shape for a request that was sent but never answered. */
const noResponse = (over = {}) => ({
  message: "Network Error", request: {}, response: undefined, ...over,
});

describe("a request that got no response", () => {
  it("is not reported with axios's bare wording", () => {
    const msg = errMsg(noResponse());
    expect(msg).not.toBe("Network Error");
    expect(msg).toMatch(/couldn't reach/i);
  });

  it("says nothing was submitted, because nothing was", () => {
    expect(errMsg(noResponse())).toMatch(/nothing was submitted/i);
  });

  it("names a timeout as a timeout rather than as unreachable", () => {
    const msg = errMsg(noResponse({ code: "ECONNABORTED", message: "timeout of 30000ms exceeded" }));
    expect(msg).toMatch(/took too long/i);
    expect(msg).toMatch(/nothing was submitted/i);
  });

  it("leaves a deliberate cancellation to the caller", () => {
    // We aborted it on purpose (unmount, superseded request). There is nothing to apologise
    // for and no state to report, so the generic fallback applies.
    expect(transportMessage(noResponse({ code: "ERR_CANCELED" }))).toBeNull();
  });
});

describe("a request the server DID answer", () => {
  const answered = (status, data) => ({ message: "Request failed", response: { status, data } });

  it("is never described as a transport failure", () => {
    expect(transportMessage(answered(500, { detail: "boom" }))).toBeNull();
    expect(transportMessage(answered(403, { detail: "nope" }))).toBeNull();
  });

  it("still surfaces the server's own detail", () => {
    expect(errMsg(answered(409, { detail: "This organization already has an active subscription" })))
      .toBe("This organization already has an active subscription");
  });

  it("surfaces the readable 500 the API now returns instead of a dropped response", () => {
    // The other half of the fix: app/main.py answers an unhandled fault inside CORS, so this
    // reaches the page at all.
    const msg = errMsg(answered(500, { detail: "The server hit an unexpected error handling this request." }));
    expect(msg).toMatch(/unexpected error/i);
    expect(msg).not.toMatch(/couldn't reach/i);
  });

  it("keeps reporting a 422's field errors", () => {
    expect(errMsg(answered(422, { detail: [{ msg: "value is not a valid email address" }] })))
      .toMatch(/valid email address/);
  });

  it("keeps the Cloudflare edge wording, which is more specific than either", () => {
    expect(errMsg(answered(525, { detail: "handshake failed" }))).toMatch(/edge error 525/);
  });
});
