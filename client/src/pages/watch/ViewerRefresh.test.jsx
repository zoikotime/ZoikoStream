// Refreshing the watch page must put the viewer back where they were.
//
// ── THE BUG ────────────────────────────────────────────────────────────────────────────
// After a refresh: Total viewers 0, chat unusable, reactions greyed out. All four symptoms
// are one failure — the live WebSocket never opens — because everything the page shows
// about "who is here" comes off that socket's opening snapshot:
//
//   socket open → moderator/snapshot → panel.participants → Total viewers
//                                   → liveStatus "open"   → chat composer + reaction bar
//
// So these tests assert the socket, not the pixels: that a restored credential reaches the
// handshake URL, that presence comes back, and that nothing re-registers on the way.
//
// useEventStream is deliberately NOT mocked here — it is the thing under test. WebSocket is
// stubbed instead, so the real hook runs and we can read exactly what it tried to connect
// with.
import { act, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../api", () => ({
  default: { get: vi.fn(), post: vi.fn() },
  // useEventStream builds the socket URL from this, so the mock has to carry it.
  API_BASE: "http://api.test",
  errMsg: (e, fallback) => e?.response?.data?.detail || e?.message || fallback || "Error",
}));
vi.mock("../../auth/AuthContext", () => ({ useAuth: () => ({ user: null }) }));
vi.mock("../../hooks/useKeepAwake", () => ({ default: () => {} }));
vi.mock("../../hooks/useLiveKitViewer", () => {
  const mediaRef = { current: null };
  return {
    default: () => ({
      mediaRef, connected: true, reconnecting: false, hasVideo: true, hasAudio: true,
      error: null, videoLayers: [], hasVideoPublication: true, quality: "auto",
      selectQuality: vi.fn(), micOn: false, micError: null, toggleMic: vi.fn(),
      enableMic: vi.fn(), micLive: false,
    }),
  };
});
vi.mock("../../utils/sound", () => ({ playAlertChime: vi.fn(), unlockAudio: vi.fn() }));
vi.mock("../../ui/Toast", () => ({
  notify: { error: vi.fn(), success: vi.fn(), alert: vi.fn() },
}));

import api from "../../api";
import { ThemeProvider } from "../../theme/ThemeContext";
import EventWatch from "./EventWatch";

const EVENT_ID = "2c1b878a-1903-4a91-8895-81334298e09e";
const OTHER_EVENT = "9f000000-0000-4000-8000-00000000ffff";
const TOKEN = "reg-token-for-this-event";
const OTHER_TOKEN = "reg-token-for-a-DIFFERENT-event";

// ── a WebSocket that records rather than connects ──────────────────────────────────────
let sockets = [];

class FakeSocket {
  static OPEN = 1;
  constructor(url) {
    this.url = url;
    this.readyState = FakeSocket.OPEN;
    this.sent = [];
    sockets.push(this);
  }
  send(data) { this.sent.push(data); }
  close() { this.readyState = 3; this.onclose?.({ code: 1000, reason: "" }); }
  /** Drive the handshake the way the server does: open, then the opening snapshot. */
  open() { act(() => this.onopen?.()); }
  deliver(env) { act(() => this.onmessage?.({ data: JSON.stringify(env) })); }
}

const socketUrl = () => sockets.at(-1)?.url ?? "";
const regParam = () => new URL(socketUrl(), "http://x").searchParams.get("reg");

// What the server sends on connect. `participants` is where Total viewers comes from.
const snapshot = (participants) => ({
  channel: "moderator",
  type: "snapshot",
  data: {
    messages: [], questions: [], polls: [], announcements: [], activity: [],
    participants,
    can_moderate: false,
    you: { identity: "viewer-self", can_moderate: false, can_host: false },
    event: { id: EVENT_ID, name: "TEST", status: "live" },
  },
});

const viewer = (identity, over = {}) => ({
  identity, name: "Someone", role: "viewer", waiting: false, joined_at: 1, ...over,
});

const WATCH = {
  id: EVENT_ID, title: "TEST", status: "live", visibility: "public",
  host_name: "Vihari", organization_name: "Northwind",
  chat_enabled: true, qa_enabled: true, polls_enabled: true,
  reactions_enabled: true, raise_hand_enabled: true,
  registration_required: true, registered: true,
  room: "event_1", livekit_token: "lk", livekit_url: "wss://example",
};

function renderWatch(eventId = EVENT_ID) {
  return render(
    <ThemeProvider>
      <MemoryRouter initialEntries={[`/events/${eventId}/watch`]}>
        <Routes>
          <Route path="/events/:eventId/watch" element={<EventWatch />} />
        </Routes>
      </MemoryRouter>
    </ThemeProvider>
  );
}

/** The page as it exists after F5: storage survives, React state does not. */
const afterRefresh = () => renderWatch();

// The restored count, read where it is rendered as a PLAIN NUMBER — WatchHeader and the
// player badge both print `viewers` directly.
//
// Deliberately NOT the "Total viewers" StatsCard: that one goes through ui/Counter, which
// tweens from 0 and only starts once ui/motion's useInView fires. src/test/setup.js stubs
// IntersectionObserver as an inert no-op on purpose ("components treat a never-firing
// observer as not yet revealed"), so that card reads 0 in jsdom no matter what the data
// says. Asserting on it would test the stub, not the rejoin.
const watchingCount = () => {
  const m = document.body.textContent.match(/(\d+)\s+watching/);
  return m ? Number(m[1]) : null;
};
const reactionButton = (name) => screen.getByRole("button", { name });
const chatBox = () => screen.queryByPlaceholderText(/say something/i);

beforeEach(() => {
  sockets = [];
  vi.clearAllMocks();
  localStorage.clear();
  sessionStorage.clear();
  globalThis.WebSocket = FakeSocket;
  vi.mocked(api.get).mockImplementation((url, config) => {
    if (!url.includes("/watch")) return Promise.resolve({ data: {} });
    const id = url.split("/")[2];
    // The server only says "registered" to a request that actually carried this event's
    // credential — modelling that is the only way a test can tell a restored session from
    // an anonymous one.
    const ok = config?.params?.reg === (id === EVENT_ID ? TOKEN : null);
    return Promise.resolve({ data: { ...WATCH, id, registered: ok } });
  });
});

afterEach(() => { delete globalThis.WebSocket; });

// ── 1. the reported bug ────────────────────────────────────────────────────────────────

describe("a registered viewer refreshes", () => {
  beforeEach(() => localStorage.setItem(`zk_reg_${EVENT_ID}`, TOKEN));

  it("opens the live socket, rather than sitting unauthorized", async () => {
    afterRefresh();
    await waitFor(() => expect(sockets.length).toBe(1));
  });

  it("presents the restored credential on the socket handshake", async () => {
    afterRefresh();
    await waitFor(() => expect(sockets.length).toBe(1));

    // Without this the server closes with 1008 "Invalid or expired session" and every
    // symptom below follows.
    expect(regParam()).toBe(TOKEN);
  });

  it("does NOT register again", async () => {
    afterRefresh();
    await waitFor(() => expect(sockets.length).toBe(1));

    expect(vi.mocked(api.post)).not.toHaveBeenCalled();
    expect(screen.queryByRole("heading", { name: /registration required/i })).not.toBeInTheDocument();
  });

  it("sends the credential on the /watch request too", async () => {
    afterRefresh();
    await waitFor(() => expect(api.get).toHaveBeenCalled());

    const watchCalls = vi.mocked(api.get).mock.calls.filter(([u]) => u.includes("/watch"));
    expect(watchCalls.some(([, c]) => c?.params?.reg === TOKEN)).toBe(true);
  });
});

// ── 7. the viewer count comes back ─────────────────────────────────────────────────────

describe("Total viewers after a refresh", () => {
  beforeEach(() => localStorage.setItem(`zk_reg_${EVENT_ID}`, TOKEN));

  it("is restored from the opening snapshot, not left at 0", async () => {
    afterRefresh();
    await waitFor(() => expect(sockets.length).toBe(1));
    const ws = sockets[0];
    ws.open();
    ws.deliver(snapshot([viewer("a"), viewer("b"), viewer("c")]));

    await waitFor(() => expect(watchingCount()).toBe(3));
  });

  it("counts only real viewers — staff and the waiting room never inflate it", async () => {
    afterRefresh();
    await waitFor(() => expect(sockets.length).toBe(1));
    const ws = sockets[0];
    ws.open();
    ws.deliver(snapshot([
      viewer("a"),
      viewer("b", { role: "host" }),
      viewer("c", { waiting: true }),
      viewer("d", { role: "speaker" }),
    ]));

    await waitFor(() => expect(watchingCount()).toBe(1));
  });

  it("does not drop to 0 when somebody ELSE leaves", async () => {
    // 6: several viewers, one of them refreshes. The others' count must not collapse.
    afterRefresh();
    await waitFor(() => expect(sockets.length).toBe(1));
    const ws = sockets[0];
    ws.open();
    ws.deliver(snapshot([viewer("a"), viewer("b")]));
    await waitFor(() => expect(watchingCount()).toBe(2));

    ws.deliver({ channel: "participants", type: "participant.leave", data: { identity: "b" } });

    await waitFor(() => expect(watchingCount()).toBe(1));
  });

  it("does not double-count the same viewer rejoining", async () => {
    // 8: presence is keyed on identity server-side (bus.presence_upsert), and the reducer
    // keys on it too — a rejoin is an upsert, never a second row.
    afterRefresh();
    await waitFor(() => expect(sockets.length).toBe(1));
    const ws = sockets[0];
    ws.open();
    ws.deliver(snapshot([viewer("a")]));
    ws.deliver({ channel: "participants", type: "participant.join", data: viewer("a") });

    await waitFor(() => expect(watchingCount()).toBe(1));
  });
});

// ── 9/10. chat and reactions work immediately ──────────────────────────────────────────

describe("chat and reactions after a refresh", () => {
  beforeEach(() => localStorage.setItem(`zk_reg_${EVENT_ID}`, TOKEN));

  const connected = async () => {
    afterRefresh();
    await waitFor(() => expect(sockets.length).toBe(1));
    const ws = sockets[0];
    ws.open();
    ws.deliver(snapshot([viewer("self")]));
    return ws;
  };

  it("shows the chat composer, not the identify form", async () => {
    await connected();

    // `identified` is derived from the restored credential; without it the panel replaces
    // chat with "enter your name and email", which is the "chat unavailable" symptom.
    await waitFor(() => expect(chatBox()).toBeInTheDocument());
    expect(screen.queryByText(/enter your name and email/i)).not.toBeInTheDocument();
  });

  it("leaves the reaction buttons enabled", async () => {
    await connected();
    await waitFor(() => expect(reactionButton("Hype")).toBeEnabled());
  });

  it("actually sends a reaction over the restored socket", async () => {
    const ws = await connected();
    await waitFor(() => expect(reactionButton("Hype")).toBeEnabled());

    act(() => reactionButton("Hype").click());

    const frames = ws.sent.map((s) => JSON.parse(s));
    expect(frames.some((f) => f.action === "reaction.add" && f.payload.key === "fire")).toBe(true);
  });

  it("keeps Q&A, Polls and Raise Hand at whatever the watch payload says", async () => {
    await connected();

    await waitFor(() => expect(screen.getByRole("button", { name: /raise your hand/i })).toBeInTheDocument());
    // Rendered from the watch payload's per-event flags, so a refresh must not silently
    // drop a tab the organiser enabled.
    expect(screen.getByText("Q&A")).toBeInTheDocument();
    expect(screen.getByText("Polls")).toBeInTheDocument();
  });
});

// ── 2/3. both Remember me choices survive a refresh ────────────────────────────────────

describe("the credential is found wherever Remember me put it", () => {
  it("localStorage, when Remember me was ticked", async () => {
    localStorage.setItem(`zk_reg_${EVENT_ID}`, TOKEN);
    afterRefresh();

    await waitFor(() => expect(regParam()).toBe(TOKEN));
  });

  it("sessionStorage, when it was not — same tab still rejoins", async () => {
    sessionStorage.setItem(`zk_reg_${EVENT_ID}`, TOKEN);
    afterRefresh();

    await waitFor(() => expect(regParam()).toBe(TOKEN));
  });

  it("prefers the remembered one when both somehow exist", async () => {
    localStorage.setItem(`zk_reg_${EVENT_ID}`, TOKEN);
    sessionStorage.setItem(`zk_reg_${EVENT_ID}`, "stale-session-copy");
    afterRefresh();

    await waitFor(() => expect(regParam()).toBe(TOKEN));
  });
});

// ── the credential stays event-specific ────────────────────────────────────────────────

describe("one event's credential never opens another", () => {
  it("does not carry event A's token to event B's socket", async () => {
    localStorage.setItem(`zk_reg_${EVENT_ID}`, TOKEN);
    localStorage.setItem(`zk_reg_${OTHER_EVENT}`, OTHER_TOKEN);

    renderWatch(OTHER_EVENT);

    await waitFor(() => expect(sockets.length).toBe(1));
    expect(regParam()).toBe(OTHER_TOKEN);
    expect(regParam()).not.toBe(TOKEN);
  });

  it("holds no credential at all for an event it was never registered for", async () => {
    localStorage.setItem(`zk_reg_${EVENT_ID}`, TOKEN);

    renderWatch(OTHER_EVENT);

    // Nothing to present, so the page asks rather than borrowing.
    await waitFor(() => expect(api.get).toHaveBeenCalled());
    expect(sockets.map((s) => new URL(s.url, "http://x").searchParams.get("reg")))
      .not.toContain(TOKEN);
  });

  it("does not mistake the remembered PROFILE for an access credential", async () => {
    // The device profile is a name and an email for pre-filling a form. It grants nothing,
    // and must never be read as a session.
    localStorage.setItem("zk_viewer_profile", JSON.stringify({ name: "Naveen", email: "n@x.com" }));

    afterRefresh();

    await waitFor(() => expect(api.get).toHaveBeenCalled());
    expect(sockets.length === 0 || regParam() === null).toBe(true);
  });
});

// ── 5. a dropped connection comes back on its own ──────────────────────────────────────

describe("after a temporary network loss", () => {
  it("reconnects with the same credential", async () => {
    vi.useFakeTimers();
    try {
      localStorage.setItem(`zk_reg_${EVENT_ID}`, TOKEN);
      afterRefresh();
      await vi.waitFor(() => expect(sockets.length).toBe(1));
      sockets[0].open();

      // The socket drops with a code that is NOT fatal, so the hook retries.
      act(() => sockets[0].onclose?.({ code: 1006, reason: "" }));
      await act(async () => { await vi.advanceTimersByTimeAsync(5000); });

      expect(sockets.length).toBeGreaterThan(1);
      expect(regParam()).toBe(TOKEN);
    } finally {
      vi.useRealTimers();
    }
  });

  it("restores the roster from the fresh snapshot rather than replaying the old one", async () => {
    localStorage.setItem(`zk_reg_${EVENT_ID}`, TOKEN);
    afterRefresh();
    await waitFor(() => expect(sockets.length).toBe(1));

    const first = sockets[0];
    first.open();
    first.deliver(snapshot([viewer("a"), viewer("b")]));
    await waitFor(() => expect(watchingCount()).toBe(2));

    // A reconnect re-sends the WHOLE snapshot; the reducer replaces rather than merges, so
    // a viewer who left while we were offline is not resurrected.
    first.deliver(snapshot([viewer("a")]));
    await waitFor(() => expect(watchingCount()).toBe(1));
  });
});
