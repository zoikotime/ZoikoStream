// A speaker presenting from Backstage must not be signed out mid-broadcast, and an abandoned
// Backstage must not stay signed in just because the event is live.
//
// The page runs inside the REAL AuthProvider and session keeper (auth/useSessionKeeper); only
// the media and socket hooks are stubbed, so each test drives exactly what the page knows: the
// contributor state from the live socket, and what LiveKit has acknowledged as published.
// Time is fake (shouldAdvanceTime), never waited for.
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../api", () => ({
  default: { get: vi.fn(), post: vi.fn() },
  API_BASE: "http://api.test",
  AUTH_EXPIRED_EVENT: "zoiko:auth-expired",
  errMsg: (e) => e?.message ?? "Something went wrong.",
}));
vi.mock("../../ui/Toast", () => ({ notify: { error: vi.fn(), success: vi.fn() } }));

let pushEnvelope = () => {};
vi.mock("../../hooks/useEventStream", () => ({
  default: (eventId, onEnvelope) => {
    pushEnvelope = onEnvelope;
    return { status: "open", closeReason: null, send: vi.fn() };
  },
}));
const MEDIA = {
  videoRef: { current: null }, streamRef: { current: null }, videoTrack: { kind: "video" },
  devices: { cameras: [], mics: [] }, picked: {}, active: true, error: null,
  selectCamera: () => {}, selectMic: () => {},
};
vi.mock("../../hooks/useMediaPreview", () => ({ default: () => MEDIA }));
vi.mock("../../hooks/useLiveKitViewer", () => ({
  default: () => ({ mediaRef: { current: null }, hasVideo: false, hasAudio: false }),
}));
// What LiveKit has acknowledged. Tests change it, then re-render through a socket envelope.
let published = { publishedAudio: true, publishedVideo: true };
vi.mock("../../hooks/useLiveKitPublish", () => ({
  default: () => ({ connected: true, reconnecting: false, publishError: null, ...published }),
}));
vi.mock("../../hooks/useKeepAwake", () => ({ default: () => {} }));
vi.mock("../../native/useBroadcastKeepAlive", () => ({ default: () => {} }));

import api from "../../api";
import { AuthProvider } from "../../auth/AuthContext";
import ProtectedRoute from "../../components/ProtectedRoute";
import { __resetSessionChannelForTests, peekSessionEndReason } from "../../auth/sessionStore";
import { HOLD_INTERVAL_MS } from "../../auth/useSessionKeeper";
import Backstage from "./Backstage";
import { isActivelyPresenting } from "./activeSpeaker";

const MIN = 60_000;
const SPEAKER = { id: "u-speaker", full_name: "Sam Speaker", email: "sam@example.com", role: "speaker" };

const statusIn = (idleIn) => {
  const now = Date.now();
  return {
    idle_timeout_seconds: 1800,
    last_activity_at: new Date(now).toISOString(),
    idle_expires_at: new Date(now + idleIn).toISOString(),
    absolute_expires_at: new Date(now + 12 * 60 * MIN).toISOString(),
    server_now: new Date(now).toISOString(),
    remember: true,
  };
};
const ended = (reason) => {
  const error = new Error("session ended");
  error.response = { status: 401, data: { detail: { code: "SESSION_EXPIRED", reason, message: "x" } } };
  return error;
};

let sessionStatus;
const activityCalls = () => api.post.mock.calls.filter(([url]) => url === "/auth/session/activity");

function setVisibility(state) {
  Object.defineProperty(document, "visibilityState", { configurable: true, get: () => state });
}

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  localStorage.clear();
  sessionStorage.clear();
  vi.clearAllMocks();
  setVisibility("visible");
  published = { publishedAudio: true, publishedVideo: true };
  localStorage.setItem("token", "speaker.jwt.value");
  sessionStatus = () => statusIn(30 * MIN);
  api.get.mockImplementation((url) => {
    if (url === "/auth/me") return Promise.resolve({ data: SPEAKER });
    if (url === "/auth/session") {
      try {
        return Promise.resolve({ data: sessionStatus() });
      } catch (error) {
        return Promise.reject(error);
      }
    }
    return Promise.resolve({ data: { status: "live" } });     // the return feed's /watch
  });
  api.post.mockImplementation((url) => (url === "/auth/session/activity"
    ? Promise.resolve({ data: statusIn(30 * MIN) })
    : Promise.resolve({ data: {} })));
});

afterEach(() => {
  vi.useRealTimers();
  __resetSessionChannelForTests();
  localStorage.clear();
  sessionStorage.clear();
});

function renderBackstage() {
  return render(
    <MemoryRouter initialEntries={["/speaker/backstage?event=ev-1"]}>
      <AuthProvider>
        <Routes>
          <Route path="/login" element={<h1>Sign in</h1>} />
          <Route element={<ProtectedRoute />}>
            <Route path="/speaker/backstage" element={<Backstage />} />
          </Route>
        </Routes>
      </AuthProvider>
    </MemoryRouter>,
  );
}

const contributor = (state) => ({ user_id: SPEAKER.id, state });

/** The live socket's snapshot: the speaker is in `state`. */
async function joinAs(state) {
  renderBackstage();
  await waitFor(() => expect(api.get).toHaveBeenCalledWith("/auth/me"));
  await act(async () => {
    pushEnvelope({
      channel: "moderator", type: "snapshot",
      data: { event: { name: "Town hall" }, my_contributor_state: contributor(state),
              my_publish_token: "publish.jwt", livekit_url: "wss://livekit.test" },
    });
  });
  await act(async () => {});
}

/** The host moves the speaker (or anything else re-renders the page). */
const moveTo = (state) => act(async () => {
  pushEnvelope({ channel: "contributor", type: "session.update", data: contributor(state) });
});

const advance = (ms) => act(async () => { await vi.advanceTimersByTimeAsync(ms); });

describe("the active-speaker rule", () => {
  const on = { contributorState: "live", publishedAudio: true, publishedVideo: true, micOn: true, cameraOn: true };
  it.each([
    ["on stage, camera and microphone published", on, true],
    ["publishing audio only", { ...on, publishedVideo: false }, true],
    ["publishing video only", { ...on, publishedAudio: false }, true],
    ["muted by the host but still on camera", { ...on, contributorState: "muted" }, true],
    ["muted by the host with the camera off", { ...on, contributorState: "muted", cameraOn: false }, false],
    ["both devices turned off", { ...on, micOn: false, cameraOn: false }, false],
    ["nothing acknowledged by LiveKit (connection lost)", { ...on, publishedAudio: false, publishedVideo: false }, false],
    ["waiting to be admitted", { ...on, contributorState: "connected" }, false],
    ["ready, off stage", { ...on, contributorState: "ready" }, false],
    ["on standby", { ...on, contributorState: "on_standby" }, false],
    ["removed", { ...on, contributorState: "removed" }, false],
  ])("%s", (_label, input, expected) => {
    expect(isActivelyPresenting(input)).toBe(expected);
  });
});

describe("a speaker presenting from Backstage", () => {
  it("stays signed in beyond the idle window without touching anything, and is never warned", async () => {
    await joinAs("live");
    expect(screen.getByText(/You.re on air/)).toBeInTheDocument();
    await advance(75 * MIN);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.getByText(/You.re on air/)).toBeInTheDocument();
    // Bounded: one report at once, then one per HOLD_INTERVAL_MS.
    expect(activityCalls().length).toBeGreaterThanOrEqual(15);
    expect(activityCalls().length).toBeLessThanOrEqual(1 + (75 * MIN) / HOLD_INTERVAL_MS);
  });

  it("counts audio alone, and video alone", async () => {
    published = { publishedAudio: true, publishedVideo: false };
    await joinAs("live");
    await advance(2 * HOLD_INTERVAL_MS);
    const withAudio = activityCalls().length;
    expect(withAudio).toBeGreaterThanOrEqual(2);
    fireEvent.click(screen.getByRole("button", { name: "Mute microphone" }));   // audio off...
    published = { publishedAudio: true, publishedVideo: true };                  // ...camera on
    await moveTo("live");
    await advance(2 * HOLD_INTERVAL_MS);
    expect(activityCalls().length).toBeGreaterThan(withAudio);
  });

  it("is not held while waiting off stage, even with the socket and return feed running", async () => {
    await joinAs("ready");
    await advance(26 * MIN);
    expect(activityCalls()).toHaveLength(0);
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });
});

describe("a speaker who stops presenting", () => {
  it("leaving the stage ends the hold: the normal warning appears, then the session ends", async () => {
    await joinAs("live");
    await advance(20 * MIN);
    await moveTo("ready");
    const reported = activityCalls().length;
    await advance(26 * MIN);
    expect(activityCalls()).toHaveLength(reported);
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    sessionStatus = () => { throw ended("idle"); };
    await advance(5 * MIN);
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    expect(peekSessionEndReason()).toBe("idle");
  });

  it("turning both devices off ends the hold", async () => {
    await joinAs("live");
    await advance(1 * MIN);
    fireEvent.click(screen.getByRole("button", { name: "Turn camera off" }));
    fireEvent.click(screen.getByRole("button", { name: "Mute microphone" }));
    const reported = activityCalls().length;
    await advance(26 * MIN);
    // The two clicks were genuine interaction (at most one report); the hold adds nothing.
    expect(activityCalls().length).toBeLessThanOrEqual(reported + 1);
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("losing the publish connection ends the hold", async () => {
    await joinAs("live");
    published = { publishedAudio: false, publishedVideo: false };
    await moveTo("live");
    const reported = activityCalls().length;
    await advance(26 * MIN);
    expect(activityCalls()).toHaveLength(reported);
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("an abandoned, hidden Backstage tab expires even while on stage", async () => {
    await joinAs("live");
    await act(async () => {
      setVisibility("hidden");
      document.dispatchEvent(new Event("visibilitychange"));
    });
    const reported = activityCalls().length;
    await advance(26 * MIN);
    expect(activityCalls()).toHaveLength(reported);
    sessionStatus = () => { throw ended("idle"); };
    await advance(5 * MIN);
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
  });

  it("the maximum session length signs out even a presenting speaker", async () => {
    await joinAs("live");
    api.post.mockImplementation((url) => (url === "/auth/session/activity"
      ? Promise.reject(ended("absolute"))
      : Promise.resolve({ data: {} })));
    await advance(HOLD_INTERVAL_MS + 1_000);
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    expect(peekSessionEndReason()).toBe("absolute");
  });
});
