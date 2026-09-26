// A fresh visitor to a public event meets the registration form, not the video.
//
// The bug was entirely server-side (the gate read a column that defaults False and can no
// longer be set), but the page is where it was observed, so these drive the real EventWatch
// against the real /watch payloads and assert what the viewer actually gets.
//
// Storage is cleared between cases, which is what "fresh Incognito" means here: no
// remembered credential for this or any event.
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../api", () => ({
  default: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
  errMsg: (e, fallback) => fallback ?? "Something went wrong.",
  API_BASE: "http://localhost:8001",
  AUTH_EXPIRED_EVENT: "zoiko:auth-expired",
}));

vi.mock("../../ui/Toast", () => ({
  notify: { error: vi.fn(), success: vi.fn(), alert: vi.fn() },
}));

vi.mock("../../auth/AuthContext", () => ({
  useAuth: () => ({ user: null, loading: false, logout: vi.fn() }),
}));

// The live socket and the LiveKit room are separate concerns; stubs keep these about the
// gate. Without the LiveKit stub the `registered` payloads below carry a real-looking
// wss:// URL and the hook genuinely tries to dial it, which throws after the test has
// already finished — noise that would eventually destabilise the suite.
vi.mock("../../hooks/useEventStream", () => ({
  default: () => ({ status: "closed", send: vi.fn(), disconnect: vi.fn(), closeReason: null }),
}));

vi.mock("../../hooks/useLiveKitViewer", () => ({
  default: () => ({
    mediaRef: { current: null },
    connected: false,
    reconnecting: false,
    hasVideo: false,
    hasAudio: false,
    error: null,
    micOn: false,
    micError: null,
    micLive: false,
    // Quality controls: the player reads these, so the mock has to carry them or the
    // settings menu would crash on videoLayers.map.
    videoLayers: [],
    quality: "auto",
    selectQuality: vi.fn(),
    toggleMic: vi.fn(),
    // Async in the real hook, and a caller chains off it — a bare vi.fn() returns undefined
    // and the chain throws.
    enableMic: vi.fn(() => Promise.resolve(true)),
  }),
}));

import api from "../../api";
import { ThemeProvider } from "../../theme/ThemeContext";
import EventWatch from "./EventWatch";

const EVENT_A = "7ac5caa2-2a78-42d3-b3ed-49e0f61d7d13";
const EVENT_B = "11111111-2222-3333-4444-555555555555";

// What GET /events/{id}/watch returns for a public event the caller has NOT registered for.
const unregistered = (id) => ({
  id,
  title: "TESTING",
  status: "live",
  visibility: "public",
  chat_enabled: true,
  qa_enabled: true,
  polls_enabled: true,
  registration_required: true,   // the effective policy, now derived server-side
  registered: false,             // this caller's verdict
  livekit_token: null,           // withheld until registered
  livekit_url: null,
  room: null,
});

const registered = (id) => ({
  ...unregistered(id),
  registered: true,
  livekit_token: "viewer.subscribe.only.token",
  livekit_url: "wss://livekit.example",
  room: `event-${id}`,
});

const renderWatch = (id) =>
  render(
    <ThemeProvider>
      <MemoryRouter initialEntries={[`/events/${id}/watch`]}>
        <Routes>
          <Route path="/events/:eventId/watch" element={<EventWatch />} />
        </Routes>
      </MemoryRouter>
    </ThemeProvider>
  );

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  sessionStorage.clear();
});

afterEach(() => {
  localStorage.clear();
  sessionStorage.clear();
});

describe("a fresh visitor to a public event", () => {
  beforeEach(() => {
    vi.mocked(api.get).mockResolvedValue({ data: unregistered(EVENT_A) });
  });

  it("is asked to register", async () => {
    renderWatch(EVENT_A);
    expect(await screen.findByText("Enter your name to watch the live event.")).toBeInTheDocument();
  });

  it("is offered the Remember me choice, unticked", async () => {
    renderWatch(EVENT_A);
    await screen.findByText("Enter your name to watch the live event.");
    const box = screen.getByRole("checkbox", { name: /remember me for this event/i });
    expect(box).not.toBeChecked();
  });

  it("presents no saved credential, because there is none", async () => {
    renderWatch(EVENT_A);
    await screen.findByText("Enter your name to watch the live event.");
    // Fresh browser: the watch call carries no reg/link params at all.
    const [, config] = vi.mocked(api.get).mock.calls[0];
    expect(config?.params).toBeUndefined();
  });
});

describe("a caller the server accepts", () => {
  it("goes straight in, with no form", async () => {
    vi.mocked(api.get).mockResolvedValue({ data: registered(EVENT_A) });
    renderWatch(EVENT_A);

    await waitFor(() =>
      expect(screen.queryByText("Enter your name to watch the live event.")).not.toBeInTheDocument()
    );
    expect(screen.queryByRole("checkbox", { name: /remember me/i })).not.toBeInTheDocument();
  });
});

describe("a remembered credential", () => {
  it("is sent to the server for this event, and the form is skipped when accepted", async () => {
    localStorage.setItem(`zk_reg_${EVENT_A}`, "saved.credential.for.a");
    vi.mocked(api.get).mockResolvedValue({ data: registered(EVENT_A) });

    renderWatch(EVENT_A);
    await waitFor(() => expect(api.get).toHaveBeenCalled());

    const [url, config] = vi.mocked(api.get).mock.calls[0];
    expect(url).toBe(`/events/${EVENT_A}/watch`);
    expect(config.params).toMatchObject({ reg: "saved.credential.for.a" });
    expect(screen.queryByText("Enter your name to watch the live event.")).not.toBeInTheDocument();
  });

  it("does nothing for a DIFFERENT event", async () => {
    // Remembered for A only. Opening B must ask, and must not present A's credential.
    localStorage.setItem(`zk_reg_${EVENT_A}`, "saved.credential.for.a");
    vi.mocked(api.get).mockResolvedValue({ data: unregistered(EVENT_B) });

    renderWatch(EVENT_B);
    expect(await screen.findByText("Enter your name to watch the live event.")).toBeInTheDocument();
    const [, config] = vi.mocked(api.get).mock.calls[0];
    expect(config?.params).toBeUndefined();
  });

  it("is discarded and the form shown when the server rejects it", async () => {
    localStorage.setItem(`zk_reg_${EVENT_A}`, "tampered.or.expired");
    // The server verified it and said no — which is the only opinion that counts.
    vi.mocked(api.get).mockResolvedValue({ data: unregistered(EVENT_A) });

    renderWatch(EVENT_A);
    expect(await screen.findByText("Enter your name to watch the live event.")).toBeInTheDocument();
    await waitFor(() => expect(localStorage.getItem(`zk_reg_${EVENT_A}`)).toBeNull());
    expect(sessionStorage.getItem(`zk_reg_${EVENT_A}`)).toBeNull();
  });

  it("a session-only credential is presented too, while the tab lives", async () => {
    // The unticked path: usable now, gone when the tab closes.
    sessionStorage.setItem(`zk_reg_${EVENT_A}`, "session.only.credential");
    vi.mocked(api.get).mockResolvedValue({ data: registered(EVENT_A) });

    renderWatch(EVENT_A);
    await waitFor(() => expect(api.get).toHaveBeenCalled());
    expect(vi.mocked(api.get).mock.calls[0][1].params).toMatchObject({
      reg: "session.only.credential",
    });
  });
});
