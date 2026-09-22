// The viewer's emoji reactions and Raise Hand.
//
// ── THE BUG ────────────────────────────────────────────────────────────────────────────
// A live event rendered its video, its viewer count and its Chat/Q&A/Polls panel, but no
// emoji buttons and no Raise Hand. Two faults, one on each side:
//
//   BACKEND  routers/events.py's watch payload still clamped BOTH flags on category:
//              reactions_enabled = not is_memorial_category(...)
//              raise_hand_enabled = False if is_memorial_category(...) else ev.raise_hand_enabled
//            The Funeral / Memorial restriction was retired everywhere else — crud.event's
//            create/update, broadcast._seed_settings, _MEMORIAL_LOCKED_SETTINGS — but these
//            two survived, on the one surface that decides whether the controls render at
//            all. It was already inconsistent with the server's own enforcement:
//            _seed_settings seeds reactions_enabled True regardless of category, so
//            moderation._reaction_add would have ACCEPTED a reaction whose button was hidden.
//
//   FRONTEND Raise Hand lives inside ReactionBar, and the whole bar was gated on
//            `reactions_enabled` alone. So ANY event with reactions off lost Raise Hand
//            too, however its own raise_hand_enabled was set — two independent features
//            sharing one switch.
//
// These pin each flag to its own control, and pin the combinations.
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../api", () => ({
  default: { get: vi.fn(), post: vi.fn() },
  API_BASE: "http://api.test",
  errMsg: (e, fallback) => e?.message ?? fallback ?? "Error",
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
  notify: { error: vi.fn(), success: vi.fn(), alert: vi.fn(), info: vi.fn() },
}));

import api from "../../api";
import { ThemeProvider } from "../../theme/ThemeContext";
import EventWatch from "./EventWatch";

const EVENT_ID = "7f92709a-2f58-42e4-88ba-cea55c313b2a";
const TOKEN = "reg-token";

let sockets = [];
class FakeSocket {
  static OPEN = 1;
  constructor(url) { this.url = url; this.readyState = 1; this.sent = []; sockets.push(this); }
  send(d) { this.sent.push(d); }
  close() { this.readyState = 3; }
}

// The event from the report: live, registered viewer, panel rendering.
const WATCH = {
  id: EVENT_ID, title: "Test 9", status: "live", visibility: "public",
  host_name: "Akshay", organization_name: "Zoiko",
  chat_enabled: true, qa_enabled: true, polls_enabled: true,
  registration_required: true, registered: true,
  room: "r1", livekit_token: "lk", livekit_url: "wss://x",
  reactions_enabled: true, raise_hand_enabled: true,
};

const serve = (over = {}) =>
  vi.mocked(api.get).mockResolvedValue({ data: { ...WATCH, ...over } });

function show() {
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

// Ready signal that works in every case, including the ones where no control renders.
const ready = () => screen.findByText(/test 9/i);
const emoji = (name) => screen.queryByRole("button", { name });
const raiseHand = () => screen.queryByRole("button", { name: /raise your hand/i });
const anyEmoji = () => screen.queryAllByRole("button", { name: /^(like|love|celebrate|hype|applause)$/i });

beforeEach(() => {
  sockets = [];
  vi.clearAllMocks();
  localStorage.clear();
  sessionStorage.clear();
  globalThis.WebSocket = FakeSocket;
  localStorage.setItem(`zk_reg_${EVENT_ID}`, TOKEN);
  serve();
});

// ── the reported case ──────────────────────────────────────────────────────────────────

describe("a live event with both features on", () => {
  it("shows all five emoji buttons", async () => {
    show();
    await ready();

    for (const label of ["Like", "Love", "Celebrate", "Hype", "Applause"]) {
      expect(emoji(label)).toBeInTheDocument();
    }
  });

  it("shows Raise Hand", async () => {
    show();
    await ready();
    expect(raiseHand()).toBeInTheDocument();
  });
});

// ── the coupling bug: one flag must not hide the other's control ───────────────────────

describe("the two features are independent", () => {
  it("keeps Raise Hand when reactions are off", async () => {
    // THE REGRESSION. Raise Hand lives inside ReactionBar, and the bar used to be gated on
    // reactions_enabled alone — so this combination rendered nothing at all.
    serve({ reactions_enabled: false, raise_hand_enabled: true });
    show();
    await ready();

    expect(raiseHand()).toBeInTheDocument();
    expect(anyEmoji()).toHaveLength(0);
  });

  it("keeps the emoji when Raise Hand is off", async () => {
    serve({ reactions_enabled: true, raise_hand_enabled: false });
    show();
    await ready();

    expect(anyEmoji()).toHaveLength(5);
    expect(raiseHand()).not.toBeInTheDocument();
  });

  it("hides the whole bar only when BOTH are off", async () => {
    serve({ reactions_enabled: false, raise_hand_enabled: false });
    show();
    await ready();

    expect(anyEmoji()).toHaveLength(0);
    expect(raiseHand()).not.toBeInTheDocument();
  });
});

// ── the controls actually work ─────────────────────────────────────────────────────────

describe("the controls still drive the existing actions", () => {
  const connect = () => {
    const ws = sockets[0];
    ws.onopen?.();
    ws.onmessage?.({ data: JSON.stringify({
      channel: "moderator", type: "snapshot",
      data: { messages: [], questions: [], polls: [], announcements: [], activity: [],
              participants: [], you: { identity: "me", can_moderate: false } },
    }) });
    return ws;
  };

  it("sends the unchanged reaction.add action", async () => {
    const u = userEvent.setup();
    show();
    await ready();
    const ws = connect();

    await u.click(emoji("Hype"));

    const frames = ws.sent.map((s) => JSON.parse(s));
    expect(frames.some((f) => f.action === "reaction.add" && f.payload.key === "fire")).toBe(true);
  });

  it("sends the unchanged participant.hand action", async () => {
    const u = userEvent.setup();
    show();
    await ready();
    const ws = connect();

    await u.click(raiseHand());

    const frames = ws.sent.map((s) => JSON.parse(s));
    expect(frames.some((f) => f.action === "participant.hand")).toBe(true);
  });
});

// ── the flags come from the payload, not from the category ─────────────────────────────

describe("category does not decide visibility", () => {
  it("shows both controls on a Funeral / Memorial event that enabled them", async () => {
    // The retired restriction. The backend no longer clamps on category, so a memorial
    // event reports whatever the organiser configured, like every other category.
    serve({ category: "Funeral / Memorial", reactions_enabled: true, raise_hand_enabled: true });
    show();
    await ready();

    expect(anyEmoji()).toHaveLength(5);
    expect(raiseHand()).toBeInTheDocument();
  });

  it("still hides them when the event genuinely turned them off", async () => {
    // Not faking enabled features: a real `false` stays hidden.
    serve({ category: "Webinar", reactions_enabled: false, raise_hand_enabled: false });
    show();
    await ready();

    expect(anyEmoji()).toHaveLength(0);
    expect(raiseHand()).not.toBeInTheDocument();
  });
});

// ── visibility survives a refresh ──────────────────────────────────────────────────────

describe("after a refresh", () => {
  it("restores both controls for a returning registered viewer", async () => {
    // Storage already holds the credential (beforeEach), which is what a refresh looks like.
    show();
    await ready();

    expect(anyEmoji()).toHaveLength(5);
    expect(raiseHand()).toBeInTheDocument();
  });

  it("restores them for a session-only viewer too", async () => {
    localStorage.clear();
    sessionStorage.setItem(`zk_reg_${EVENT_ID}`, TOKEN);
    show();
    await ready();

    expect(anyEmoji()).toHaveLength(5);
    expect(raiseHand()).toBeInTheDocument();
  });
});
