// The client half of session timeout (the server enforces; see server/test_session_timeout.py).
//
// Pinned here: genuine interaction reports activity (throttled) and nothing else does; the
// inactivity warning appears from the SERVER's deadline and "Stay signed in" only works if the
// server agrees; at a deadline the server decides; an ended session lands on /login with the
// reason; tabs stay in step; a live broadcast keeps its producer signed in; "Remember me"
// decides persistence only. Time is driven by fake timers, never waited for.
import { useState } from "react";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../api", () => ({
  default: { get: vi.fn(), post: vi.fn() },
  API_BASE: "http://api.test",
  AUTH_EXPIRED_EVENT: "zoiko:auth-expired",
  errMsg: (e) => e?.message ?? "Something went wrong.",
  errCode: () => null,
}));

import api from "../api";
import { AuthProvider, useAuth } from "./AuthContext";
import ProtectedRoute from "../components/ProtectedRoute";
import {
  __resetSessionChannelForTests,
  peekSessionEndReason,
  storeToken,
} from "./sessionStore";
import { HOLD_INTERVAL_MS, TOUCH_INTERVAL_MS, useSessionHold } from "./useSessionKeeper";
import useEventStream from "../hooks/useEventStream";

const USER = { id: "u1", full_name: "Ada Admin", email: "ada@example.com", role: "org_admin" };
const MIN = 60_000;

/** A session status as GET /auth/session returns it, `idleIn` ms from now. */
const statusIn = (idleIn, absoluteIn = 12 * 60 * MIN) => {
  const now = Date.now();
  return {
    idle_timeout_seconds: 1800,
    last_activity_at: new Date(now).toISOString(),
    idle_expires_at: new Date(now + idleIn).toISOString(),
    absolute_expires_at: new Date(now + absoluteIn).toISOString(),
    server_now: new Date(now).toISOString(),
    remember: true,
  };
};

/** The structured 401 the server sends for an ended session. */
const ended = (reason = "idle", code = "SESSION_EXPIRED") => {
  const error = new Error("session ended");
  error.response = { status: 401, data: { detail: { code, reason, message: "Please sign in again." } } };
  return error;
};

let sessionStatus;     // what GET /auth/session answers; a function so tests can change it
const activityCalls = () => api.post.mock.calls.filter(([url]) => url === "/auth/session/activity");

function Console() {
  const { logout } = useAuth();
  return (
    <div>
      <h1>Organization dashboard</h1>
      <button type="button" onClick={logout}>Sign out</button>
    </div>
  );
}

function LiveConsole({ live, whileHidden }) {
  useSessionHold(live, { whileHidden });
  return <h1>Producer console</h1>;
}

// A presenter whose hold can end (leaving the stage) or flap (a publish connection bouncing).
function Stage() {
  const [on, setOn] = useState(true);
  useSessionHold(on);
  return (
    <div>
      <h1>Producer console</h1>
      <button type="button" onClick={() => setOn(false)}>Leave stage</button>
      <button type="button" onClick={() => setOn((v) => !v)}>Flap</button>
    </div>
  );
}

// A console holding a live socket (the Backstage / host studio pattern).
function SocketConsole() {
  const { status } = useEventStream("event-1", () => {});
  return <h1>Producer console {status}</h1>;
}

function renderApp({ path = "/organization/dashboard", live, whileHidden } = {}) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <AuthProvider>
        <Routes>
          <Route path="/login" element={<h1>Sign in</h1>} />
          <Route element={<ProtectedRoute />}>
            <Route path="/organization/dashboard" element={<Console />} />
            <Route path="/producer" element={<LiveConsole live={live} whileHidden={whileHidden} />} />
            <Route path="/stage" element={<Stage />} />
            <Route path="/studio" element={<SocketConsole />} />
          </Route>
        </Routes>
      </AuthProvider>
    </MemoryRouter>,
  );
}

const hide = () => act(() => {
  setVisibility("hidden");
  document.dispatchEvent(new Event("visibilitychange"));
});

// findBy resolves as soon as the DOM shows the console, which can be before React has run the
// passive effects that attach the activity listeners; flush them so an event fired next is
// heard (a real user cannot click inside that gap).
const signedIn = async () => {
  const el = await screen.findByText(/Organization dashboard|Producer console/);
  await act(async () => {});
  return el;
};
const signInScreen = () => screen.findByRole("heading", { name: "Sign in" });

function setVisibility(state) {
  Object.defineProperty(document, "visibilityState", { configurable: true, get: () => state });
}

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  localStorage.clear();
  sessionStorage.clear();
  vi.clearAllMocks();
  setVisibility("visible");
  localStorage.setItem("token", "good.jwt.value");
  sessionStatus = () => statusIn(30 * MIN);
  api.get.mockImplementation((url) => {
    if (url === "/auth/me") return Promise.resolve({ data: USER });
    if (url === "/auth/session") {
      try {
        return Promise.resolve({ data: sessionStatus() });
      } catch (error) {
        return Promise.reject(error);
      }
    }
    return Promise.resolve({ data: {} });
  });
  api.post.mockImplementation((url) => {
    if (url === "/auth/session/activity") return Promise.resolve({ data: statusIn(30 * MIN) });
    return Promise.resolve({ data: {} });
  });
});

afterEach(() => {
  vi.useRealTimers();
  __resetSessionChannelForTests();
  localStorage.clear();
  sessionStorage.clear();
});

// ── what counts as activity ──────────────────────────────────────────────────────────────

describe("activity", () => {
  it("reports genuine interaction to the server, at most once per interval", async () => {
    renderApp();
    await signedIn();
    fireEvent.pointerDown(window);
    await waitFor(() => expect(activityCalls()).toHaveLength(1));
    for (let i = 0; i < 5; i += 1) fireEvent.keyDown(window, { key: "a" });
    expect(activityCalls()).toHaveLength(1);
    // The interaction inside the interval is reported when the interval ends.
    await act(async () => { await vi.advanceTimersByTimeAsync(TOUCH_INTERVAL_MS); });
    expect(activityCalls()).toHaveLength(2);
  });

  it("does not report anything when nobody is interacting — background polling is not activity", async () => {
    renderApp();
    await signedIn();
    await act(async () => { await vi.advanceTimersByTimeAsync(20 * MIN); });
    expect(activityCalls()).toHaveLength(0);
    // Only status reads, which never extend a session.
    expect(api.get.mock.calls.every(([url]) => url === "/auth/me" || url === "/auth/session")).toBe(true);
  });

  it("ignores events from a hidden tab", async () => {
    renderApp();
    await signedIn();
    setVisibility("hidden");
    fireEvent.keyDown(window, { key: "a" });
    fireEvent.pointerDown(window);
    await act(async () => { await vi.advanceTimersByTimeAsync(2 * MIN); });
    expect(activityCalls()).toHaveLength(0);
  });
});

// ── warning and expiry ───────────────────────────────────────────────────────────────────

describe("the inactivity warning", () => {
  it("appears five minutes before the server's idle deadline", async () => {
    sessionStatus = () => statusIn(6 * MIN);
    renderApp();
    await signedIn();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await act(async () => { await vi.advanceTimersByTimeAsync(MIN + 1000); });
    expect(await screen.findByRole("dialog", { name: "Your session will expire soon due to inactivity." })).toBeInTheDocument();
  });

  it("Stay signed in asks the server, and the warning closes when the server agrees", async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    sessionStatus = () => statusIn(4 * MIN);
    renderApp();
    await screen.findByRole("dialog");
    await user.click(screen.getByRole("button", { name: "Stay signed in" }));
    expect(activityCalls().length).toBeGreaterThanOrEqual(1);
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(screen.getByText("Organization dashboard")).toBeInTheDocument();
  });

  it("Stay signed in cannot revive a session the server has already ended", async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    sessionStatus = () => statusIn(4 * MIN);
    api.post.mockImplementation((url) =>
      url === "/auth/session/activity" ? Promise.reject(ended("idle")) : Promise.resolve({ data: {} }));
    renderApp();
    await screen.findByRole("dialog");
    await user.click(screen.getByRole("button", { name: "Stay signed in" }));
    expect(await signInScreen()).toBeInTheDocument();
    expect(peekSessionEndReason()).toBe("idle");
  });

  it("Sign out in the warning signs out", async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    sessionStatus = () => statusIn(4 * MIN);
    renderApp();
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Sign out" }));
    expect(await signInScreen()).toBeInTheDocument();
  });
});

describe("at the deadline the server decides", () => {
  it("an ended session redirects to /login and records why", async () => {
    sessionStatus = () => statusIn(2 * MIN);
    renderApp();
    await signedIn();
    sessionStatus = () => { throw ended("idle"); };
    await act(async () => { await vi.advanceTimersByTimeAsync(3 * MIN); });
    expect(await signInScreen()).toBeInTheDocument();
    expect(peekSessionEndReason()).toBe("idle");
    expect(localStorage.getItem("token")).toBeNull();
  });

  it("a session another tab kept alive is not ended here", async () => {
    sessionStatus = () => statusIn(2 * MIN);
    renderApp();
    await signedIn();
    sessionStatus = () => statusIn(25 * MIN);      // extended elsewhere in the meantime
    await act(async () => { await vi.advanceTimersByTimeAsync(3 * MIN); });
    expect(screen.getByText("Organization dashboard")).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("the maximum session length ends it too, with its own reason", async () => {
    sessionStatus = () => statusIn(30 * MIN, 2 * MIN);
    renderApp();
    await signedIn();
    sessionStatus = () => { throw ended("absolute"); };
    await act(async () => { await vi.advanceTimersByTimeAsync(3 * MIN); });
    expect(await signInScreen()).toBeInTheDocument();
    expect(peekSessionEndReason()).toBe("absolute");
  });
});

// ── reopening the browser ────────────────────────────────────────────────────────────────

describe("reopening the browser", () => {
  it("after the idle window, a stored token gets the sign-in screen with the reason", async () => {
    api.get.mockImplementation((url) =>
      url === "/auth/me" ? Promise.reject(ended("idle")) : Promise.resolve({ data: {} }));
    localStorage.setItem("user", JSON.stringify(USER));   // stale local state, too
    renderApp();
    expect(await signInScreen()).toBeInTheDocument();
    expect(peekSessionEndReason()).toBe("idle");
    expect(localStorage.getItem("token")).toBeNull();
    expect(localStorage.getItem("user")).toBeNull();
  });

  it("within the window, the server keeps the session and the console opens", async () => {
    renderApp();
    expect(await signedIn()).toBeInTheDocument();
  });
});

// ── sign-out and tabs ────────────────────────────────────────────────────────────────────

describe("tabs", () => {
  it("signing out revokes the session on the server and tells the other tabs", async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    const otherTab = new BroadcastChannel("zs-session");
    const heard = [];
    otherTab.onmessage = (e) => heard.push(e.data);
    renderApp();
    await signedIn();
    await user.click(screen.getByRole("button", { name: "Sign out" }));
    expect(await signInScreen()).toBeInTheDocument();
    const [url, , config] = api.post.mock.calls.find(([u]) => u === "/auth/logout");
    expect(url).toBe("/auth/logout");
    expect(config.headers.Authorization).toBe("Bearer good.jwt.value");
    await waitFor(() => expect(heard).toContainEqual({ type: "ended", reason: "logout" }));
    otherTab.close();
  });

  it("another tab's sign-out or expiry signs this tab out", async () => {
    renderApp();
    await signedIn();
    const otherTab = new BroadcastChannel("zs-session");
    otherTab.postMessage({ type: "ended", reason: "idle" });
    expect(await signInScreen()).toBeInTheDocument();
    expect(peekSessionEndReason()).toBe("idle");
    otherTab.close();
  });

  it("this tab's expiry signs out the other tabs", async () => {
    const otherTab = new BroadcastChannel("zs-session");
    const heard = [];
    otherTab.onmessage = (e) => heard.push(e.data);
    sessionStatus = () => statusIn(2 * MIN);
    renderApp();
    await signedIn();
    sessionStatus = () => { throw ended("idle"); };
    await act(async () => { await vi.advanceTimersByTimeAsync(3 * MIN); });
    await waitFor(() => expect(heard).toContainEqual({ type: "ended", reason: "idle" }));
    otherTab.close();
  });

  it("activity in another tab keeps this tab from warning", async () => {
    sessionStatus = () => statusIn(4 * MIN);
    renderApp();
    await screen.findByRole("dialog");
    const otherTab = new BroadcastChannel("zs-session");
    otherTab.postMessage({ type: "status", status: statusIn(30 * MIN) });
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    otherTab.close();
  });
});

// ── live production ──────────────────────────────────────────────────────────────────────

describe("a producer on air", () => {
  it("is kept signed in without touching the mouse while the broadcast is live", async () => {
    renderApp({ path: "/producer", live: true });
    await signedIn();
    await waitFor(() => expect(activityCalls()).toHaveLength(1));
    await act(async () => { await vi.advanceTimersByTimeAsync(3 * HOLD_INTERVAL_MS); });
    expect(activityCalls()).toHaveLength(4);
  });

  it("an abandoned console that is not live reports nothing, so its session can lapse", async () => {
    renderApp({ path: "/producer", live: false });
    await signedIn();
    await act(async () => { await vi.advanceTimersByTimeAsync(3 * HOLD_INTERVAL_MS); });
    expect(activityCalls()).toHaveLength(0);
  });

  it("the host console's hold also applies with its tab hidden (a producer working in OBS)", async () => {
    renderApp({ path: "/producer", live: true, whileHidden: true });
    await signedIn();
    await waitFor(() => expect(activityCalls()).toHaveLength(1));
    hide();
    await act(async () => { await vi.advanceTimersByTimeAsync(3 * HOLD_INTERVAL_MS); });
    expect(activityCalls()).toHaveLength(4);
  });
});

// ── holds: bounded, visible, never immortal ──────────────────────────────────────────────

describe("a presenter hold", () => {
  it("keeps a presenter signed in past the idle window with no input, and never warns", async () => {
    renderApp({ path: "/stage" });
    await signedIn();
    await act(async () => { await vi.advanceTimersByTimeAsync(60 * MIN); });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.getByText("Producer console")).toBeInTheDocument();
  });

  it("costs a handful of requests an hour, never one per heartbeat", async () => {
    renderApp({ path: "/stage" });
    await signedIn();
    await act(async () => { await vi.advanceTimersByTimeAsync(60 * MIN); });
    // One at once, then one per HOLD_INTERVAL_MS: 16 in an hour.
    expect(activityCalls().length).toBeLessThanOrEqual(1 + (60 * MIN) / HOLD_INTERVAL_MS);
  });

  it("does not multiply requests when it flaps on and off", async () => {
    renderApp({ path: "/stage" });
    await signedIn();
    await waitFor(() => expect(activityCalls()).toHaveLength(1));
    for (let i = 0; i < 10; i += 1) {
      fireEvent.click(screen.getByRole("button", { name: "Flap" }));
      await act(async () => { await vi.advanceTimersByTimeAsync(2_000); });
    }
    // A pointer press is genuine interaction too: that is the one interval report allowed.
    expect(activityCalls().length).toBeLessThanOrEqual(2);
  });

  it("applies only while its tab is visible: a hidden presenter tab lets the session lapse", async () => {
    renderApp({ path: "/stage" });
    await signedIn();
    await waitFor(() => expect(activityCalls()).toHaveLength(1));
    hide();
    await act(async () => { await vi.advanceTimersByTimeAsync(20 * MIN); });
    expect(activityCalls()).toHaveLength(1);
    // The normal idle warning, then the server's verdict at the deadline.
    await act(async () => { await vi.advanceTimersByTimeAsync(6 * MIN); });
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    sessionStatus = () => { throw ended("idle"); };
    await act(async () => { await vi.advanceTimersByTimeAsync(5 * MIN); });
    expect(await signInScreen()).toBeInTheDocument();
    expect(peekSessionEndReason()).toBe("idle");
  });

  it("once it ends (leaving the stage), the normal warning and expiry resume", async () => {
    renderApp({ path: "/stage" });
    await signedIn();
    await act(async () => { await vi.advanceTimersByTimeAsync(40 * MIN); });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Leave stage" }));
    const reported = activityCalls().length;
    await act(async () => { await vi.advanceTimersByTimeAsync(26 * MIN); });
    expect(activityCalls()).toHaveLength(reported);
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    sessionStatus = () => { throw ended("idle"); };
    await act(async () => { await vi.advanceTimersByTimeAsync(5 * MIN); });
    expect(await signInScreen()).toBeInTheDocument();
  });

  it("cannot outlast the maximum session length: the server's verdict still signs out", async () => {
    renderApp({ path: "/stage" });
    await signedIn();
    api.post.mockImplementation((url) => (url === "/auth/session/activity"
      ? Promise.reject(ended("absolute"))
      : Promise.resolve({ data: {} })));
    await act(async () => { await vi.advanceTimersByTimeAsync(HOLD_INTERVAL_MS + 1_000); });
    expect(await signInScreen()).toBeInTheDocument();
    expect(peekSessionEndReason()).toBe("absolute");
  });
});

// ── an open live socket ends with the session ────────────────────────────────────────────

describe("a live socket the server closes because the session ended", () => {
  let sockets;
  class FakeSocket {
    static OPEN = 1;
    constructor(url) { this.url = url; this.readyState = FakeSocket.OPEN; sockets.push(this); }
    send() {}
    close() { this.readyState = 3; }
  }
  beforeEach(() => { sockets = []; globalThis.WebSocket = FakeSocket; });
  afterEach(() => { delete globalThis.WebSocket; });

  it("signs out with the reason in this tab and every other tab, and never reconnects", async () => {
    const otherTab = new BroadcastChannel("zs-session");
    const heard = [];
    otherTab.onmessage = (e) => heard.push(e.data);
    renderApp({ path: "/studio" });
    await signedIn();
    expect(sockets).toHaveLength(1);
    act(() => sockets[0].onopen?.());
    act(() => sockets[0].onclose?.({ code: 4401, reason: "revoked" }));
    expect(await signInScreen()).toBeInTheDocument();
    expect(peekSessionEndReason()).toBe("revoked");
    await waitFor(() => expect(heard).toContainEqual({ type: "ended", reason: "revoked" }));
    await act(async () => { await vi.advanceTimersByTimeAsync(60_000); });
    expect(sockets).toHaveLength(1);
    otherTab.close();
  });

  it("socket heartbeats are not activity", async () => {
    renderApp({ path: "/studio" });
    await signedIn();
    act(() => sockets[0].onopen?.());
    await act(async () => { await vi.advanceTimersByTimeAsync(20 * MIN); });   // pings every 15s
    expect(activityCalls()).toHaveLength(0);
  });
});

// ── "Remember me" ────────────────────────────────────────────────────────────────────────

describe("Remember me", () => {
  it("on: the credential survives a browser restart (localStorage)", () => {
    storeToken("t1", { remember: true });
    expect(localStorage.getItem("token")).toBe("t1");
    expect(sessionStorage.getItem("token")).toBeNull();
  });

  it("off: the credential lives only in this browser session", () => {
    storeToken("t2", { remember: false });
    expect(sessionStorage.getItem("token")).toBe("t2");
    expect(localStorage.getItem("token")).toBeNull();
  });

  it("off, browser reopened with no tab left to ask: sign-in is required", async () => {
    localStorage.clear();
    storeToken("t3", { remember: false });
    sessionStorage.clear();                           // the browser closed every tab
    renderApp();
    expect(await signInScreen()).toBeInTheDocument();
  });

  it("off, a new tab while another is open: the open tab hands the session over", async () => {
    localStorage.clear();
    storeToken("t4", { remember: false });
    sessionStorage.clear();                           // the NEW tab's empty sessionStorage
    const openTab = new BroadcastChannel("zs-session");
    openTab.onmessage = (e) => {
      if (e.data.type === "token-request") openTab.postMessage({ type: "token", id: e.data.id, token: "t4" });
    };
    renderApp();
    expect(await signedIn()).toBeInTheDocument();
    expect(sessionStorage.getItem("token")).toBe("t4");
    openTab.close();
  });
});
