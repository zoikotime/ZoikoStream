// The studio connection's own state machine.
//
// ── THE BUG ────────────────────────────────────────────────────────────────────────────
// Opening the Producer Console on a freshly created event showed "Reconnecting to the
// studio…" and a "Reconnecting ·1" chip, then settled to Connected a moment later. Nothing
// had disconnected — the FIRST handshake simply did not land, and the retry was labelled a
// reconnect:
//
//     setStatus(retries > 4 ? "offline" : "reconnecting");   // on ANY close
//
// "Reconnecting" is a claim about history: it tells an operator that a working connection
// broke. On a brand-new event that is false, and it is exactly the kind of false alarm that
// teaches people to ignore the status chip.
//
// The socket is stubbed so each transition can be driven deliberately — these are about
// WHICH WORD the hook reports, and nothing else.
import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../api", () => ({ API_BASE: "http://api.test" }));

import useEventStream from "./useEventStream";

const EVENT_A = "aaaaaaaa-0000-4000-8000-00000000000a";
const EVENT_B = "bbbbbbbb-0000-4000-8000-00000000000b";

let sockets = [];
class FakeSocket {
  static OPEN = 1;
  constructor(url) {
    this.url = url;
    this.readyState = FakeSocket.OPEN;
    sockets.push(this);
  }
  send() {}
  close() { this.readyState = 3; }
  /** The server accepted the handshake. */
  open() { act(() => this.onopen?.()); }
  /** The socket went away. 1006 is an abnormal close — not in FATAL_CODES, so it retries. */
  drop(code = 1006) { act(() => this.onclose?.({ code, reason: "" })); }
}

const latest = () => sockets.at(-1);

beforeEach(() => {
  sockets = [];
  localStorage.setItem("token", "host-jwt");   // authKey, so the hook actually connects
  globalThis.WebSocket = FakeSocket;
  vi.useFakeTimers();
});
afterEach(() => {
  vi.useRealTimers();
  localStorage.clear();
  delete globalThis.WebSocket;
});

const mount = (eventId = EVENT_A) =>
  renderHook(({ id }) => useEventStream(id, () => {}), { initialProps: { id: eventId } });

/** Let the backoff timer fire so the next attempt is made. */
const advance = async () => { await act(async () => { await vi.advanceTimersByTimeAsync(20000); }); };

// ── 1. a new event's first connection ──────────────────────────────────────────────────

describe("the first connection", () => {
  it("starts as connecting", () => {
    const { result } = mount();
    expect(result.current.status).toBe("connecting");
  });

  it("stays connecting when the first handshake fails — never 'reconnecting'", async () => {
    // THE REPORTED BUG, stated as an assertion.
    const { result } = mount();
    latest().drop();

    expect(result.current.status).toBe("connecting");
    expect(result.current.status).not.toBe("reconnecting");
  });

  it("is still connecting across several failed first attempts", async () => {
    const { result } = mount();

    for (let i = 0; i < 3; i += 1) {
      latest().drop();
      expect(result.current.status).toBe("connecting");
      await advance();
    }
  });

  it("escalates to offline once it clearly cannot reach the studio", async () => {
    // Retrying forever under a "Connecting" label would be its own lie.
    const { result } = mount();

    for (let i = 0; i < 5; i += 1) {
      latest().drop();
      await advance();
    }

    expect(result.current.status).toBe("offline");
  });
});

// ── 2. success ─────────────────────────────────────────────────────────────────────────

describe("once connected", () => {
  it("reports open, with no attempt counter left over", () => {
    const { result } = mount();
    latest().drop();          // a failed first try…
    latest().open();          // …then it lands

    expect(result.current.status).toBe("open");
    expect(result.current.attempt).toBe(0);
  });
});

// ── 3 & 4. a real disconnect, and recovery ─────────────────────────────────────────────

describe("after a connection has existed", () => {
  it("reports reconnecting when it is genuinely lost", () => {
    const { result } = mount();
    latest().open();

    latest().drop();

    // This is the case the word is FOR.
    expect(result.current.status).toBe("reconnecting");
    expect(result.current.attempt).toBe(1);
  });

  it("clears back to open when the reconnect succeeds", async () => {
    const { result } = mount();
    latest().open();
    latest().drop();
    expect(result.current.status).toBe("reconnecting");

    await advance();
    latest().open();

    expect(result.current.status).toBe("open");
    expect(result.current.attempt).toBe(0);
  });

  it("keeps saying reconnecting across repeated drops of a session that worked", async () => {
    const { result } = mount();
    latest().open();

    latest().drop();
    await advance();
    latest().drop();

    expect(result.current.status).toBe("reconnecting");
  });
});

// ── 5. a different event must not inherit the old one's verdict ────────────────────────

describe("switching events", () => {
  it("resets an offline verdict when a new event is opened", async () => {
    const { result, rerender } = mount(EVENT_A);
    for (let i = 0; i < 5; i += 1) { latest().drop(); await advance(); }
    expect(result.current.status).toBe("offline");

    rerender({ id: EVENT_B });

    // Event B has its own socket and has not failed at anything.
    expect(result.current.status).toBe("connecting");
    expect(result.current.attempt).toBe(0);
  });

  it("does not carry a reconnect from event A into event B", () => {
    const { result, rerender } = mount(EVENT_A);
    latest().open();
    latest().drop();
    expect(result.current.status).toBe("reconnecting");

    rerender({ id: EVENT_B });

    expect(result.current.status).toBe("connecting");
  });

  it("treats B's own first failure as connecting, not reconnecting", async () => {
    // The `everOpen` history belongs to A's socket and must not leak into B's.
    const { result, rerender } = mount(EVENT_A);
    latest().open();
    rerender({ id: EVENT_B });

    latest().drop();

    expect(result.current.status).toBe("connecting");
  });

  it("opens a socket for the new event", () => {
    const { rerender } = mount(EVENT_A);
    rerender({ id: EVENT_B });

    expect(latest().url).toContain(EVENT_B);
  });
});

// ── the refusal path is unchanged ──────────────────────────────────────────────────────

describe("a refused connection", () => {
  it("is unauthorized, not connecting — 1008 must not be retried", () => {
    const { result } = mount();

    act(() => latest().onclose?.({ code: 1008, reason: "Invalid or expired session" }));

    expect(result.current.status).toBe("unauthorized");
    expect(result.current.closeReason).toBe("Invalid or expired session");
  });
});
