// The viewer's chat shows the host's reactions live: the real EventWatch, fed the same
// envelopes the server broadcasts (server/app/services/moderation.py _chat_react ->
// ("chat", "message.update") with the confirmed `reactions` summary and `reaction_users`).
//
// Production on `main` broadcast these updates too, but the viewer's chat had no code to
// draw reactions at all, so the host saw them and viewers never did.
import { act, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

const stream = vi.hoisted(() => ({ onEnvelope: null }));

vi.mock("../../api", () => ({
  default: { get: vi.fn(), post: vi.fn() },
  errMsg: (e, fallback) => e?.response?.data?.detail || e?.message || fallback || "Error",
}));
vi.mock("../../auth/AuthContext", () => ({ useAuth: () => ({ user: null }) }));
vi.mock("../../hooks/useEventStream", () => ({
  default: (eventId, onEnvelope) => {
    stream.onEnvelope = onEnvelope;
    return { status: "open", closeReason: null, latency: 12, attempt: 0, send: vi.fn(() => true), disconnect: vi.fn() };
  },
}));
vi.mock("../../hooks/useKeepAwake", () => ({ default: () => {} }));
vi.mock("../../hooks/useLiveKitViewer", async (importOriginal) => {
  const { resolveQuality } = await importOriginal();
  const mediaRef = { current: null };
  return {
    resolveQuality,
    default: () => ({
      mediaRef, connected: true, reconnecting: false, hasVideo: true, hasAudio: true, error: null,
      videoLayers: [], hasVideoPublication: true, quality: "auto", selectQuality: vi.fn(),
      micOn: false, micError: null, toggleMic: vi.fn(), enableMic: vi.fn(), micLive: false,
    }),
  };
});
vi.mock("../../utils/sound", () => ({ playAlertChime: vi.fn(), unlockAudio: vi.fn() }));
vi.mock("../../ui/Toast", () => ({ notify: { error: vi.fn(), success: vi.fn(), alert: vi.fn(), info: vi.fn() } }));

import api from "../../api";
import { ThemeProvider } from "../../theme/ThemeContext";
import EventWatch from "./EventWatch";

const EVENT_ID = "5e1f2a3b-4c5d-4e6f-8a9b-0c1d2e3f4a5b";
const OTHER_EVENT = "9a8b7c6d-5e4f-4a3b-8c2d-1e0f9a8b7c6d";
const HEART = "❤️";
const MSG = {
  id: "m1", message_id: "m1", event_id: EVENT_ID, name: "Simran", text: "hi radha",
  status: "approved", pinned: false, flags: [], reactions: {}, reaction_users: {},
  created_at: "2026-10-05T10:27:00Z", actor_role: "viewer",
};
const LIVE = {
  id: EVENT_ID, title: "Service", status: "live", visibility: "public", registered: true,
  registration_required: true, chat_enabled: true, qa_enabled: true, polls_enabled: true,
  reactions_enabled: true, raise_hand_enabled: false, not_started: false, expired: false,
  media_status: "live", livekit_token: "lk", livekit_url: "wss://example", room: "event_1", admission: "admitted",
};

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

const snapshot = (messages, you = { identity: "guest-viewer-1" }) =>
  act(() => stream.onEnvelope({ channel: "moderator", type: "snapshot", data: { messages, you, participants: [] } }));
const update = (data) =>
  act(() => stream.onEnvelope({ channel: "chat", type: "message.update", data: { ...MSG, ...data } }));
const message = () => screen.getByText("hi radha").closest("div.rounded-xl");
const chip = (emoji, count) => within(message()).queryByRole("button", { name: `Reaction ${emoji} (${count})` });

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  sessionStorage.clear();
  localStorage.setItem(`zk_reg_${EVENT_ID}`, "reg-token");
  vi.mocked(api.get).mockResolvedValue({ data: LIVE });
});

async function ready() {
  renderWatch();
  await waitFor(() => expect(stream.onEnvelope).toBeTypeOf("function"));
  await screen.findByRole("button", { name: /^Chat/ });
  snapshot([MSG]);
  await screen.findByText("hi radha");
}

describe("the viewer sees chat reactions live", () => {
  it("shows the host's ❤️ the moment it is broadcast, without a refresh", async () => {
    await ready();
    expect(chip(HEART, 1)).toBeNull();
    update({ reactions: { [HEART]: 1 }, reaction_users: { [HEART]: ["host-1"] }, reaction: HEART, count: 1, reaction_count: 1 });
    expect(chip(HEART, 1)).toBeInTheDocument();
    // The message itself is untouched and not duplicated.
    expect(screen.getAllByText("hi radha")).toHaveLength(1);
    expect(within(message()).getByText("Simran")).toBeInTheDocument();
  });

  it("removes it when the host takes it back", async () => {
    await ready();
    update({ reactions: { [HEART]: 1 }, reaction_users: { [HEART]: ["host-1"] } });
    update({ reactions: {}, reaction_users: {}, reaction: HEART, count: 0, reaction_count: 0 });
    expect(chip(HEART, 1)).toBeNull();
  });

  it("counts a second reactor, keeps emojis independent, and a duplicate envelope does not double-count", async () => {
    await ready();
    const two = { reactions: { [HEART]: 2, "🎉": 1 }, reaction_users: { [HEART]: ["host-1", "guest-viewer-2"], "🎉": ["guest-viewer-1"] } };
    update(two);
    update(two);                                            // re-delivered: same absolute state
    expect(chip(HEART, 2)).toBeInTheDocument();
    expect(chip("🎉", 1)).toBeInTheDocument();
    expect(chip(HEART, 4)).toBeNull();
  });

  it("restores persisted reactions from the snapshot after a refresh", async () => {
    renderWatch();
    await waitFor(() => expect(stream.onEnvelope).toBeTypeOf("function"));
    await screen.findByRole("button", { name: /^Chat/ });
    snapshot([{ ...MSG, reactions: { [HEART]: 1 }, reaction_users: { [HEART]: ["host-1"] } }]);
    await screen.findByText("hi radha");
    expect(chip(HEART, 1)).toBeInTheDocument();
  });

  it("never applies an update that belongs to another event", async () => {
    await ready();
    update({ event_id: OTHER_EVENT, reactions: { [HEART]: 1 }, reaction_users: { [HEART]: ["x"] } });
    expect(chip(HEART, 1)).toBeNull();
  });
});
