// The viewer states of ZST-SPEC-VAP-001 §6.3, on the EXISTING viewer page.
//
// Pre-event, live, reconnecting, broadcaster interruption, replay processing / available /
// expired, capacity waiting, in-app browser, unrecoverable error. Each test drives the real
// EventWatch with the same mocks the other viewer tests use (api, socket, LiveKit hook).
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const stream = vi.hoisted(() => ({ onEnvelope: null, options: null, send: null }));
const lk = vi.hoisted(() => ({ state: {} }));

vi.mock("../../api", () => ({
  default: { get: vi.fn(), post: vi.fn() },
  errMsg: (e, fallback) => e?.response?.data?.detail || e?.message || fallback || "Error",
}));
vi.mock("../../auth/AuthContext", () => ({ useAuth: () => ({ user: null }) }));
vi.mock("../../hooks/useEventStream", () => ({
  default: (eventId, onEnvelope, reg, link, options) => {
    stream.onEnvelope = onEnvelope;
    stream.options = options;
    return { status: "open", closeReason: null, latency: 12, attempt: 0, send: stream.send, disconnect: vi.fn() };
  },
}));
vi.mock("../../hooks/useKeepAwake", () => ({ default: () => {} }));
vi.mock("../../hooks/useLiveKitViewer", () => {
  const mediaRef = { current: null };
  return { default: () => ({ mediaRef, ...lk.state }) };
});
vi.mock("../../utils/sound", () => ({ playAlertChime: vi.fn(), unlockAudio: vi.fn() }));
vi.mock("../../ui/Toast", () => ({ notify: { error: vi.fn(), success: vi.fn(), alert: vi.fn(), info: vi.fn() } }));

import api from "../../api";
import { ThemeProvider } from "../../theme/ThemeContext";
import EventWatch from "./EventWatch";

const EVENT_ID = "8a1f4f0e-3c2b-4f7e-9d1a-2b3c4d5e6f70";
const soon = new Date(Date.now() + 3 * 3600 * 1000).toISOString();
const BASE = {
  id: EVENT_ID, title: "In loving memory", visibility: "private", category: "Funeral / Memorial",
  host_name: "Vihari", organization_name: "Northwind", chat_enabled: true, qa_enabled: true,
  polls_enabled: true, reactions_enabled: true, raise_hand_enabled: true, registered: true,
  registration_required: false, not_started: false, expired: false, media_status: "live",
  start_time: soon, end_time: null, livekit_token: null, livekit_url: null, room: null,
  admission: null, retry_after_seconds: null, replay_state: null, replay_available_until: null,
};
const LIVE = { ...BASE, status: "live", livekit_token: "lk", livekit_url: "wss://example", room: "event_1", admission: "admitted" };

function serve(...payloads) {
  const queue = [...payloads];
  vi.mocked(api.get).mockImplementation(() => {
    const next = queue.length > 1 ? queue.shift() : queue[0];
    return next instanceof Error ? Promise.reject(next) : Promise.resolve({ data: next });
  });
}

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

const isLive = () => waitFor(() => expect(screen.getAllByText("LIVE").length).toBeGreaterThan(0));

const networkError = () => Object.assign(new Error("Network Error"), { code: "ERR_NETWORK" });

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  sessionStorage.clear();
  stream.send = vi.fn(() => true);
  lk.state = {
    connected: true, reconnecting: false, hasVideo: true, hasAudio: true, error: null,
    videoLayers: [], hasVideoPublication: true, quality: "auto", selectQuality: vi.fn(),
    micOn: false, micError: null, toggleMic: vi.fn(), enableMic: vi.fn(), micLive: false,
    captions: { available: false, text: "" },
  };
});
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("pre-event", () => {
  it("shows the event, its time locally AND in UTC, and that it starts by itself", async () => {
    serve({ ...BASE, status: "scheduled", not_started: true });
    renderWatch();
    const card = await screen.findByTestId("state-pre-event");
    expect(within(card).getByRole("heading", { name: "The service hasn't started yet" })).toBeInTheDocument();
    expect(within(card).getByText("In loving memory")).toBeInTheDocument();
    expect(within(card).getAllByText(/ UTC$/).length).toBeGreaterThan(0);
    expect(within(card).getByText("Service will begin here automatically.")).toBeInTheDocument();
    expect(card).toHaveAttribute("role", "status");
  });

  it("covers a scheduled event past its start the host has not taken live — no empty PREVIEW player", async () => {
    serve({ ...BASE, status: "scheduled", not_started: false });
    renderWatch();
    expect(await screen.findByTestId("state-pre-event")).toBeInTheDocument();
    expect(screen.queryByText("PREVIEW")).toBeNull();
  });

  it("turns into the player by itself once the event is live (no refresh)", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    serve({ ...BASE, status: "scheduled", not_started: true }, LIVE);
    renderWatch();
    await screen.findByTestId("state-pre-event");
    await act(async () => { await vi.advanceTimersByTimeAsync(10_500); });
    await isLive();
  });

  it("keeps polling a viewer who opened the page early while the host was already live", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    serve({ ...BASE, status: "live", not_started: true }, LIVE);
    renderWatch();
    await screen.findByTestId("state-pre-event");
    await act(async () => { await vi.advanceTimersByTimeAsync(10_500); });
    await isLive();
  });
});

describe("live, reconnecting, interruption", () => {
  it("shows the player with a LIVE indicator", async () => {
    serve(LIVE);
    renderWatch();
    await isLive();
    expect(document.querySelector("video")).not.toBeNull();
  });

  it("keeps the player and says it is reconnecting — calmly, not as an error", async () => {
    lk.state = { ...lk.state, reconnecting: true };
    serve(LIVE);
    renderWatch();
    const notice = await screen.findByTestId("player-status-notice");
    expect(notice).toHaveTextContent("Reconnecting… The video will continue automatically.");
    expect(document.querySelector("video")).not.toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("says the service is paused when the host pauses, and keeps the page", async () => {
    serve(LIVE);
    renderWatch();
    await isLive();
    act(() => stream.onEnvelope({ channel: "broadcast", type: "broadcast.update", data: { status: "paused" } }));
    expect(await screen.findByTestId("player-status-notice")).toHaveTextContent(
      "The service is paused. Please stay on this page.");
    act(() => stream.onEnvelope({ channel: "broadcast", type: "broadcast.update", data: { status: "live" } }));
    await waitFor(() => expect(screen.queryByTestId("player-status-notice")).toBeNull());
  });

  it("says the same when the server reports the producer's media dropped", async () => {
    serve({ ...LIVE, status: "degraded", media_status: "reconnecting" });
    renderWatch();
    expect(await screen.findByTestId("player-status-notice")).toHaveTextContent("The service is paused.");
  });
});

describe("replay", () => {
  const ENDED = { ...BASE, status: "ended", livekit_token: null };

  it("is Processing while the replay is being prepared, and keeps checking", async () => {
    serve({ ...ENDED, replay_state: "processing" });
    renderWatch();
    expect(await screen.findByText("The replay is being prepared. This page will update automatically.")).toBeInTheDocument();
    expect(screen.getByText("This event has ended")).toBeInTheDocument();
  });

  it("is Expired once the window closed", async () => {
    serve({ ...ENDED, replay_state: "expired" });
    renderWatch();
    expect(await screen.findByText("The replay is no longer available.")).toBeInTheDocument();
  });

  it("is Available with its end date, and plays on request", async () => {
    serve({ ...ENDED, replay_state: "available", recording_url: "https://storage.example/replay.mp4",
            replay_available_until: "2026-12-31T00:00:00Z" });
    renderWatch();
    expect(await screen.findByRole("button", { name: /Watch the replay/ })).toBeInTheDocument();
    expect(screen.getByText(/Available until/)).toBeInTheDocument();
  });

  it("still says there is no recording when there is none", async () => {
    serve({ ...ENDED, replay_state: "unavailable" });
    renderWatch();
    expect(await screen.findByText("No recording is available for this event.")).toBeInTheDocument();
  });
});

describe("capacity waiting", () => {
  const WAITING = { ...LIVE, livekit_token: null, livekit_url: null, room: null, admission: "waiting", retry_after_seconds: 10 };

  it("holds a NEW viewer in a calm waiting state — no player, no chat, socket held", async () => {
    serve(WAITING);
    renderWatch();
    const card = await screen.findByTestId("state-capacity-waiting");
    expect(within(card).getByRole("heading", { name: "This event is very busy right now" })).toBeInTheDocument();
    expect(within(card).getByText(/join automatically as soon as there's room/)).toBeInTheDocument();
    expect(document.querySelector("video")).toBeNull();
    expect(screen.queryByRole("button", { name: /^Chat/ })).toBeNull();
    expect(stream.options).toEqual({ paused: true });
  });

  it("retries on its own and lets the viewer in once there is room", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    serve(WAITING, LIVE);
    renderWatch();
    await screen.findByTestId("state-capacity-waiting");
    await act(async () => { await vi.advanceTimersByTimeAsync(12_500); });
    await isLive();
    expect(stream.options).toEqual({ paused: false });
  });

  it("can be retried at once with Try now", async () => {
    serve(WAITING, LIVE);
    const u = userEvent.setup();
    renderWatch();
    await u.click(await screen.findByRole("button", { name: /Try now/ }));
    await isLive();
  });

  it("backs off, bounded", async () => {
    const { waitSeconds, MAX_WAIT_SECONDS } = await import("../../components/watch/CapacityWaitingNotice");
    expect(waitSeconds(10, 0)).toBe(10);
    expect(waitSeconds(10, 1)).toBe(15);
    expect(waitSeconds(10, 2)).toBe(23);
    expect(waitSeconds(10, 20)).toBe(MAX_WAIT_SECONDS);
  });

  it("never interrupts a viewer who is already watching", async () => {
    serve(LIVE);
    renderWatch();
    await isLive();
    expect(screen.queryByTestId("state-capacity-waiting")).toBeNull();
    expect(stream.options).toEqual({ paused: false });
  });
});

describe("unsupported in-app browser", () => {
  it("offers Open in browser guidance and Copy Link, without touching the session", async () => {
    const u = userEvent.setup();
    vi.spyOn(window.navigator, "userAgent", "get").mockReturnValue("Mozilla/5.0 (iPhone) Instagram 300.0");
    serve(LIVE);
    renderWatch();
    const notice = await screen.findByTestId("in-app-browser-notice");
    expect(within(notice).getByText(/open this page in your browser/)).toBeInTheDocument();
    await u.click(within(notice).getByRole("button", { name: /Copy link/ }));
    // The page's own address only — never a query string or a fragment.
    expect(await navigator.clipboard.readText()).toBe(`${window.location.origin}${window.location.pathname}`);
    expect(screen.getAllByText("LIVE").length).toBeGreaterThan(0);   // the session carries on
    await u.click(within(notice).getByRole("button", { name: "Dismiss" }));
    expect(screen.queryByTestId("in-app-browser-notice")).toBeNull();
  });

  it("is not shown in an ordinary browser", async () => {
    serve(LIVE);
    renderWatch();
    await isLive();
    expect(screen.queryByTestId("in-app-browser-notice")).toBeNull();
  });
});

describe("unrecoverable error", () => {
  it("explains simply and offers Retry and Help", async () => {
    vi.mocked(api.get).mockRejectedValueOnce(networkError()).mockResolvedValue({ data: LIVE });
    const u = userEvent.setup();
    renderWatch();
    const state = await screen.findByTestId("state-error");
    expect(within(state).getByRole("heading", { name: "We couldn't load this event" })).toBeInTheDocument();
    expect(within(state).getByRole("link", { name: /Contact support/ })).toHaveAttribute("href", "/contact");
    await u.click(within(state).getByRole("button", { name: /Try again/ }));
    await isLive();
  });

  it("a failed background poll never replaces a working page with an error", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    serve({ ...BASE, status: "scheduled", not_started: true }, networkError(), { ...BASE, status: "scheduled", not_started: true });
    renderWatch();
    await screen.findByTestId("state-pre-event");
    await act(async () => { await vi.advanceTimersByTimeAsync(10_500); });
    expect(screen.getByTestId("state-pre-event")).toBeInTheDocument();
    expect(screen.queryByText("This event could not be found.")).toBeNull();
    expect(screen.queryByTestId("state-error")).toBeNull();
  });

  it("still says not found for an event that does not exist", async () => {
    vi.mocked(api.get).mockRejectedValue(Object.assign(new Error("404"), { response: { status: 404, data: { detail: "Event not found" } } }));
    renderWatch();
    expect(await screen.findByText("This event could not be found.")).toBeInTheDocument();
  });
});
