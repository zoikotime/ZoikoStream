// The original viewer dashboard, pinned after the ZST-SPEC-VAP-001 implementation was reverted.
//
// A Funeral / Memorial event is watched exactly like any other: the shareable /e/:id link is
// the registration landing, the watch page reads the event's own chat / Q&A / polls flags, a
// /watch refusal never redirects anywhere, and nothing in the client calls an /access API.
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, resolve } from "node:path";

import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../api", () => ({
  default: { get: vi.fn(), post: vi.fn() },
  errMsg: (e, fallback) => e?.response?.data?.detail || e?.message || fallback || "Error",
}));
vi.mock("../../auth/AuthContext", () => ({ useAuth: () => ({ user: null }) }));
vi.mock("../../hooks/useEventStream", () => ({
  default: () => ({ status: "open", closeReason: null, latency: 12, attempt: 0,
                    send: vi.fn(() => true), disconnect: vi.fn() }),
}));
vi.mock("../../hooks/useKeepAwake", () => ({ default: () => {} }));
vi.mock("../../hooks/useLiveKitViewer", () => {
  const mediaRef = { current: null };
  return {
    default: () => ({
      mediaRef, connected: true, reconnecting: false,
      hasVideo: true, hasAudio: true, error: null,
      videoLayers: [], hasVideoPublication: true, quality: "auto", selectQuality: vi.fn(),
      micOn: false, micError: null, toggleMic: vi.fn(), enableMic: vi.fn(), micLive: false,
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

const EVENT_ID = "6b0f7f8e-2a51-4a7e-9a39-1f1f3c7d2b10";
const MEMORIAL = {
  id: EVENT_ID, title: "In loving memory", status: "live", visibility: "public",
  category: "Funeral / Memorial", host_name: "Vihari", organization_name: "Northwind",
  chat_enabled: true, qa_enabled: true, polls_enabled: true,
  reactions_enabled: true, raise_hand_enabled: true, registered: true,
  room: "event_1", livekit_token: "lk", livekit_url: "wss://example",
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

const requestedUrls = () => [...vi.mocked(api.get).mock.calls, ...vi.mocked(api.post).mock.calls].map(([url]) => url);

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  sessionStorage.clear();
});
afterEach(() => vi.unstubAllGlobals());

describe("a memorial is watched on the original dashboard", () => {
  it("shows Chat, Q&A and Polls from the event's own flags", async () => {
    vi.mocked(api.get).mockImplementation((url) =>
      Promise.resolve({ data: url.includes("/watch") ? MEMORIAL : {} }));
    renderWatch();
    for (const name of ["Chat", "Q&A", "Polls"]) {
      expect(await screen.findByRole("button", { name: new RegExp(`^${name}`) })).toBeInTheDocument();
    }
    expect(requestedUrls().filter((u) => String(u).startsWith("/access"))).toEqual([]);
  });

  it("hides exactly the tabs the organiser turned off", async () => {
    vi.mocked(api.get).mockImplementation((url) =>
      Promise.resolve({ data: url.includes("/watch") ? { ...MEMORIAL, chat_enabled: false, polls_enabled: false } : {} }));
    renderWatch();
    expect(await screen.findByRole("button", { name: /^Q&A/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Chat/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /^Polls/ })).toBeNull();
  });

  it("never leaves the page when /watch refuses, whatever the body names", async () => {
    const replace = vi.fn();
    vi.stubGlobal("location", { ...window.location, replace });
    vi.mocked(api.get).mockRejectedValue(Object.assign(new Error("Request failed with status code 409"), {
      response: { status: 409, data: { detail: "Conflict", access_path: "/e/Ab3dEf5hIj7kLm" } },
    }));
    renderWatch();
    await waitFor(() => expect(api.get).toHaveBeenCalled());
    await new Promise((r) => setTimeout(r, 0));
    expect(replace).not.toHaveBeenCalled();
  });
});

// ── static guards: the reverted subsystem has no foothold left in the client ──────────────

const SRC = resolve(process.cwd(), "src");
function sources(dir) {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) return sources(path);
    return /\.(jsx?|tsx?)$/.test(name) && !/\.test\./.test(name) ? [path] : [];
  });
}

describe("no viewer-access code remains", () => {
  it("routes the shareable /e/:id link to the registration landing", () => {
    const app = readFileSync(join(SRC, "App.jsx"), "utf8");
    expect(app).toMatch(/<Route path="\/e\/:id" element={<EventRegistration \/>} \/>/);
    expect(app).not.toMatch(/pages\/access\//);
  });

  it("imports none of the removed modules and calls no /access API", () => {
    const offenders = sources(SRC).filter((file) => {
      const text = readFileSync(file, "utf8");
      return /pages\/access\/|audienceTerms|launchAccess|ViewerAccessPanel|["'`]\/access\//.test(text);
    });
    expect(offenders).toEqual([]);
  });
});
