// What a viewer sees for event statuses outside the normal broadcast: a cancelled event says
// so (it used to fall through to an empty player with no explanation), and an unpublished
// draft is "not found" (the server now answers 404 to anyone outside the organization).
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../api", () => ({
  default: { get: vi.fn(), post: vi.fn() },
  errMsg: (e, fallback) => e?.response?.data?.detail || e?.message || fallback || "Error",
}));
vi.mock("../../auth/AuthContext", () => ({ useAuth: () => ({ user: null }) }));
vi.mock("../../hooks/useEventStream", () => ({
  default: () => ({ status: "idle", closeReason: null, latency: null, attempt: 0, send: vi.fn(() => false), disconnect: vi.fn() }),
}));
vi.mock("../../hooks/useKeepAwake", () => ({ default: () => {} }));
vi.mock("../../hooks/useLiveKitViewer", async (importOriginal) => {
  const { resolveQuality } = await importOriginal();
  return {
    resolveQuality,
    default: () => ({
      mediaRef: { current: null }, connected: false, reconnecting: false, hasVideo: false, hasAudio: false,
      error: null, videoLayers: [], hasVideoPublication: false, quality: "auto", selectQuality: vi.fn(),
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
const WATCH = {
  id: EVENT_ID, title: "Spring Gala", status: "scheduled", visibility: "public", registered: true,
  registration_required: false, chat_enabled: true, qa_enabled: true, polls_enabled: true,
  reactions_enabled: true, raise_hand_enabled: false, not_started: true, expired: false,
  media_status: "waiting_for_host", livekit_token: null,
};

function open() {
  return render(
    <ThemeProvider>
      <MemoryRouter initialEntries={[`/events/${EVENT_ID}/watch`]}>
        <Routes><Route path="/events/:eventId/watch" element={<EventWatch />} /></Routes>
      </MemoryRouter>
    </ThemeProvider>
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  sessionStorage.clear();
});

describe("the viewer page and event status", () => {
  it("a cancelled event says it was cancelled, with nothing to play", async () => {
    vi.mocked(api.get).mockResolvedValue({ data: { ...WATCH, status: "cancelled", not_started: false } });
    open();
    expect(await screen.findByText("This event was cancelled")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Spring Gala won't take place.");
    expect(document.querySelector("video")).toBeNull();
  });

  it("an unpublished draft is simply not found", async () => {
    vi.mocked(api.get).mockRejectedValue({ response: { status: 404, data: { detail: "Event not found" } } });
    open();
    expect(await screen.findByText("This event could not be found.")).toBeInTheDocument();
  });

  it("a scheduled event still shows the pre-event state", async () => {
    vi.mocked(api.get).mockResolvedValue({ data: WATCH });
    open();
    await waitFor(() => expect(screen.queryByText("This event was cancelled")).toBeNull());
    expect((await screen.findAllByText("Spring Gala")).length).toBeGreaterThan(0);
  });
});
