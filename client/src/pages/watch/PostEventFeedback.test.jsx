// Viewers are asked for feedback when the event ends — and told truthfully whether it was saved.
//
// Production report: no feedback request appeared when an event ended. The watch page offered
// the existing FeedbackModal only from "Leave Event". It now also opens it once, at the
// live -> ended transition, for a viewer the page saw watching live. That prompt waits for the
// server: "saved" only on session/feedback.saved for this identity (the row is committed —
// server/test_live_feedback.py), a failure on a refusal, a closed socket or no answer.
//
// The real page and the real useEventStream; WebSocket is a recorder, as in
// ViewerRefresh.test.jsx.
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../api", () => ({
  default: { get: vi.fn(), post: vi.fn() },
  API_BASE: "http://api.test",
  errMsg: (e, fallback) => e?.response?.data?.detail || e?.message || fallback || "Error",
}));
vi.mock("../../auth/AuthContext", () => ({ useAuth: () => ({ user: null }) }));
vi.mock("../../hooks/useKeepAwake", () => ({ default: () => {} }));
vi.mock("../../hooks/useLiveKitViewer", async (importOriginal) => {
  const mediaRef = { current: null };
  const { resolveQuality } = await importOriginal();
  return {
    resolveQuality,
    default: () => ({
      mediaRef, connected: true, reconnecting: false, hasVideo: true, hasAudio: true,
      error: null, videoLayers: [], hasVideoPublication: true, quality: "auto",
      selectQuality: vi.fn(), micOn: false, micError: null, toggleMic: vi.fn(),
      enableMic: vi.fn(), micLive: false,
    }),
  };
});
vi.mock("../../utils/sound", () => ({ playAlertChime: vi.fn(), unlockAudio: vi.fn() }));
vi.mock("../../ui/Toast", () => ({ notify: { error: vi.fn(), success: vi.fn(), alert: vi.fn() } }));

import api from "../../api";
import { notify } from "../../ui/Toast";
import { ThemeProvider } from "../../theme/ThemeContext";
import EventWatch from "./EventWatch";

const EVENT_ID = "2c1b878a-1903-4a91-8895-81334298e09e";
const TOKEN = "reg-token-for-this-event";
const ME = "viewer-self";

let sockets = [];
class FakeSocket {
  static OPEN = 1;
  constructor(url) {
    this.url = url;
    this.readyState = FakeSocket.OPEN;
    this.sent = [];
    sockets.push(this);
  }
  send(data) { this.sent.push(JSON.parse(data)); }
  close() { this.readyState = 3; this.onclose?.({ code: 1000, reason: "" }); }
  open() { act(() => this.onopen?.()); }
  deliver(env) { act(() => this.onmessage?.({ data: JSON.stringify(env) })); }
}

const snapshot = (status = "live") => ({
  channel: "moderator", type: "snapshot",
  data: {
    messages: [], questions: [], polls: [], announcements: [], activity: [], participants: [],
    can_moderate: false, you: { identity: ME, can_moderate: false, can_host: false },
    event: { id: EVENT_ID, name: "TEST", status }, broadcast: { status },
  },
});
const ENDED = { channel: "broadcast", type: "broadcast.update", data: { status: "ended" } };

let watchStatus;
const WATCH = () => ({
  id: EVENT_ID, title: "TEST", status: watchStatus, visibility: "public",
  host_name: "Vihari", organization_name: "Northwind",
  chat_enabled: true, qa_enabled: true, polls_enabled: true, reactions_enabled: true,
  raise_hand_enabled: true, registration_required: true, registered: true,
  room: "event_1", livekit_token: "lk", livekit_url: "wss://example",
});

function renderWatch() {
  return render(
    <ThemeProvider>
      <MemoryRouter initialEntries={[`/events/${EVENT_ID}/watch`]}>
        <Routes>
          <Route path="/events/:eventId/watch" element={<EventWatch />} />
        </Routes>
      </MemoryRouter>
    </ThemeProvider>
  );
}

const prompt = () => screen.queryByRole("dialog", { name: "Thanks for watching!" });
const feedbackFrames = (ws) => ws.sent.filter((f) => f.action === "feedback.submit");

async function watchUntilItEnds() {
  renderWatch();
  await waitFor(() => expect(sockets.length).toBe(1));
  const ws = sockets[0];
  ws.open();
  ws.deliver(snapshot("live"));
  await screen.findAllByText(/live/i);
  watchStatus = "ended";                 // what GET /watch now says
  ws.deliver(ENDED);
  await waitFor(() => expect(prompt()).toBeInTheDocument());
  return ws;
}

async function rateAndSubmit(stars = 4) {
  fireEvent.click(screen.getByRole("button", { name: `${stars} stars` }));
  fireEvent.click(screen.getByRole("button", { name: "Submit" }));
}

beforeEach(() => {
  sockets = [];
  vi.clearAllMocks();
  localStorage.clear();
  sessionStorage.clear();
  localStorage.setItem(`zk_reg_${EVENT_ID}`, TOKEN);
  globalThis.WebSocket = FakeSocket;
  watchStatus = "live";
  vi.mocked(api.get).mockImplementation((url) =>
    Promise.resolve({ data: url.includes("/watch") ? WATCH() : {} }));
});
afterEach(() => { delete globalThis.WebSocket; });

describe("when the event ends", () => {
  it("asks a viewer who was watching for feedback", async () => {
    await watchUntilItEnds();
    expect(prompt()).toBeInTheDocument();
  });

  it("says saved only once the server acknowledges THIS viewer's submission", async () => {
    const ws = await watchUntilItEnds();
    await rateAndSubmit(4);
    expect(feedbackFrames(ws)).toEqual([{ action: "feedback.submit", payload: { rating: 4, comment: null } }]);
    expect(screen.getByRole("button", { name: "Saving…" })).toBeDisabled();

    // Someone else's acknowledgement is not ours.
    ws.deliver({ channel: "session", type: "feedback.saved", data: { identity: "someone-else" } });
    await act(async () => {});                                // let any resolution land
    expect(screen.queryByText(/was saved/)).toBeNull();
    expect(screen.getByRole("button", { name: "Saving…" })).toBeDisabled();

    ws.deliver({ channel: "session", type: "feedback.saved", data: { identity: ME } });
    await waitFor(() =>
      expect(within(prompt()).getByRole("status")).toHaveTextContent("your feedback was saved"));
    fireEvent.click(screen.getByRole("button", { name: "Done" }));
    await waitFor(() => expect(prompt()).toBeNull());
  });

  it("says it could not be sent when the server refuses it, and keeps the rating to retry", async () => {
    const ws = await watchUntilItEnds();
    await rateAndSubmit(5);
    ws.deliver({ channel: "moderator", type: "error",
      data: { action: "feedback.submit", message: "Your feedback couldn't be saved. Please try again." } });
    expect(await screen.findByRole("alert")).toHaveTextContent(/couldn.t be sent/);
    expect(notify.error).not.toHaveBeenCalled();             // shown in the dialog, not a toast
    expect(screen.queryByText(/was saved/)).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(feedbackFrames(ws)).toHaveLength(2);
    expect(feedbackFrames(ws)[1].payload.rating).toBe(5);    // the rating survived the failure
  });

  it("says it could not be sent when the connection is gone", async () => {
    const ws = await watchUntilItEnds();
    ws.readyState = 3;                                        // closed under the dialog
    await rateAndSubmit(3);
    expect(await screen.findByRole("alert")).toHaveTextContent(/couldn.t be sent/);
    expect(feedbackFrames(ws)).toHaveLength(0);
  });

  it("says it could not be sent when the server never answers", async () => {
    const ws = await watchUntilItEnds();
    vi.useFakeTimers();
    try {
      await rateAndSubmit(2);
      expect(feedbackFrames(ws)).toHaveLength(1);
      expect(screen.queryByRole("alert")).toBeNull();          // still waiting, not failed yet
      await act(async () => { vi.advanceTimersByTime(10_000); });
      expect(within(prompt()).getByRole("alert")).toHaveTextContent(/couldn.t be sent/);
      // A late acknowledgement for a submission already reported as failed changes nothing.
      ws.deliver({ channel: "session", type: "feedback.saved", data: { identity: ME } });
      await act(async () => {});
      expect(screen.queryByText(/was saved/)).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });

  it("asks only once, even when the end is signalled again (a reconnect's snapshot)", async () => {
    const ws = await watchUntilItEnds();
    fireEvent.click(screen.getByRole("button", { name: "Skip" }));
    await waitFor(() => expect(prompt()).toBeNull());
    ws.deliver(snapshot("ended"));
    ws.deliver(ENDED);
    await act(async () => {});
    expect(prompt()).toBeNull();
  });
});

describe("a refresh in the same tab", () => {
  it("does not ask again once this tab has been asked", async () => {
    sessionStorage.setItem(`zk_fb_prompted_${EVENT_ID}`, "1");   // asked before the refresh
    renderWatch();
    await waitFor(() => expect(sockets.length).toBe(1));
    const ws = sockets[0];
    ws.open();
    ws.deliver(snapshot("live"));
    await screen.findAllByText(/live/i);
    watchStatus = "ended";
    ws.deliver(ENDED);
    const watchGets = () => vi.mocked(api.get).mock.calls.filter(([u]) => u.includes("/watch")).length;
    await waitFor(() => expect(watchGets()).toBeGreaterThan(1));   // the end was fetched
    await act(async () => {});
    expect(prompt()).toBeNull();
  });
});

describe("arriving after it ended", () => {
  it("does not ask someone who never watched it live", async () => {
    watchStatus = "ended";
    renderWatch();
    await waitFor(() => expect(sockets.length).toBe(1));
    sockets[0].open();
    sockets[0].deliver(snapshot("ended"));
    await act(async () => {});
    expect(prompt()).toBeNull();
  });
});
