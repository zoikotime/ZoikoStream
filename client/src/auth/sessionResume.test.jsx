// Coming back to the app after being idle.
//
// Reported from the Android app: "after leaving it unused for a while, it asks me to sign in
// again". Two paths produce that, and only one of them was a defect:
//
//   * the SERVER ended the session for inactivity (30 minutes without genuine interaction by
//     default, services/auth_sessions.py). That is the security policy, unchanged: the user is
//     sent to sign-in WITH the reason.
//   * a cold start (Android had killed the backgrounded app) whose /auth/me could not reach the
//     server — network still waking up, a timeout, a 5xx. The credential was kept, but the
//     state resolved to "unauthenticated" and the guards showed the sign-in form, in an app
//     with no reload to recover with. That is the defect: it is now UNREACHABLE, the guards
//     show a reconnecting screen, and the check is re-asked until the server answers.
//
// There are no refresh tokens in this design (one bearer token per server-side session), so
// "refresh and retry" is not part of it; nothing here can extend a session the server ended.
// The REAL provider and the REAL guard; only the api module is mocked.
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../api", () => ({
  default: { get: vi.fn(), post: vi.fn() },
  AUTH_EXPIRED_EVENT: "zoiko:auth-expired",
  errMsg: (e) => e?.message ?? "Something went wrong.",
  errCode: () => null,
}));

import api from "../api";
import { AuthProvider, useAuth, UNREACHABLE_RETRY_MS } from "./AuthContext";
import ProtectedRoute from "../components/ProtectedRoute";
import { SESSION_END_MESSAGES, __resetSessionChannelForTests, peekSessionEndReason } from "./sessionStore";

const TOKEN = "stored.session.token.never-log-me";
const USER = { id: "u1", full_name: "Ada Admin", email: "ada@example.com", role: "org_admin" };

const sessionStatus = () => ({
  server_now: new Date().toISOString(),
  last_activity_at: new Date().toISOString(),
  idle_expires_at: new Date(Date.now() + 25 * 60_000).toISOString(),
  absolute_expires_at: new Date(Date.now() + 10 * 3_600_000).toISOString(),
});
const networkError = () => Object.assign(new Error("Network Error"), { code: "ERR_NETWORK" });
const sessionEnded = (reason) =>
  Object.assign(new Error("Session expired"), {
    response: { status: 401, data: { detail: { code: "SESSION_EXPIRED", reason, message: "x" } } },
  });
const revoked = () =>
  Object.assign(new Error("Revoked"), {
    response: { status: 401, data: { detail: { code: "SESSION_REVOKED", reason: "revoked", message: "x" } } },
  });

// What /auth/me answers next; the session status endpoints always answer "alive".
let me;
const meCalls = () => vi.mocked(api.get).mock.calls.filter(([url]) => url === "/auth/me").length;

beforeEach(() => {
  localStorage.clear();
  sessionStorage.clear();
  vi.clearAllMocks();
  me = () => Promise.resolve({ data: USER });
  vi.mocked(api.get).mockImplementation((url) => {
    if (url === "/auth/me") return me();
    if (url === "/auth/session") return Promise.resolve({ data: sessionStatus() });
    return Promise.resolve({ data: {} });
  });
  vi.mocked(api.post).mockImplementation((url) =>
    url === "/auth/session/activity" ? Promise.resolve({ data: sessionStatus() }) : Promise.resolve({ data: {} }));
});

afterEach(() => {
  __resetSessionChannelForTests();
  localStorage.clear();
  sessionStorage.clear();
  vi.useRealTimers();
  setVisibility("visible");
});

function Console() {
  const { logout } = useAuth();
  return (
    <>
      <h1>Console</h1>
      <button type="button" onClick={logout}>Log out</button>
    </>
  );
}
// The real sign-in page shows the ended reason the same way (peekSessionEndReason).
function SignIn() {
  const reason = peekSessionEndReason();
  return (
    <>
      <h1>Sign in</h1>
      {reason && <p>{SESSION_END_MESSAGES[reason]}</p>}
    </>
  );
}

function openApp() {
  return render(
    <MemoryRouter initialEntries={["/console"]}>
      <AuthProvider>
        <Routes>
          <Route path="/login" element={<SignIn />} />
          <Route element={<ProtectedRoute />}>
            <Route path="/console" element={<Console />} />
          </Route>
        </Routes>
      </AuthProvider>
    </MemoryRouter>
  );
}

let visibility = "visible";
Object.defineProperty(document, "visibilityState", { configurable: true, get: () => visibility });
function setVisibility(state) {
  visibility = state;
  document.dispatchEvent(new Event("visibilitychange"));
}
const goOnline = () => window.dispatchEvent(new Event("online"));
// The reconnect screen is in the DOM before React has run the effect that listens for the
// connection coming back. Let it run, or an event fired at once has nobody to hear it.
const reconnecting = async () => {
  await screen.findByRole("heading", { name: /can.t reach zoikostream/i });
  await act(async () => {});
};

describe("returning to the app with a live session", () => {
  it("opens signed in from the stored session, without a password", async () => {
    localStorage.setItem("token", TOKEN);
    openApp();
    expect(await screen.findByRole("heading", { name: "Console" })).toBeInTheDocument();
  });

  it("stays signed in after being backgrounded: it asks the server, which says the session is alive", async () => {
    localStorage.setItem("token", TOKEN);
    openApp();
    await screen.findByRole("heading", { name: "Console" });
    const before = vi.mocked(api.get).mock.calls.filter(([u]) => u === "/auth/session").length;
    act(() => setVisibility("hidden"));
    act(() => setVisibility("visible"));
    await waitFor(() =>
      expect(vi.mocked(api.get).mock.calls.filter(([u]) => u === "/auth/session").length).toBeGreaterThan(before));
    expect(screen.getByRole("heading", { name: "Console" })).toBeInTheDocument();
    expect(localStorage.getItem("token")).toBe(TOKEN);
  });
});

describe("a session the server has ended", () => {
  it("went idle in the background: sign-in with the reason, credential cleared", async () => {
    localStorage.setItem("token", TOKEN);
    openApp();
    await screen.findByRole("heading", { name: "Console" });
    vi.mocked(api.get).mockImplementation((url) =>
      url === "/auth/session" ? Promise.reject(sessionEnded("idle")) : Promise.resolve({ data: {} }));
    act(() => setVisibility("hidden"));
    act(() => setVisibility("visible"));
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    expect(screen.getByText(SESSION_END_MESSAGES.idle)).toBeInTheDocument();
    expect(localStorage.getItem("token")).toBeNull();
  });

  it("revoked or invalid credentials never grant access", async () => {
    localStorage.setItem("token", TOKEN);
    me = () => Promise.reject(revoked());
    openApp();
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Console" })).not.toBeInTheDocument();
    expect(localStorage.getItem("token")).toBeNull();
  });
});

describe("a cold start that cannot reach the server", () => {
  beforeEach(() => {
    localStorage.setItem("token", TOKEN);
    me = () => Promise.reject(networkError());
  });

  it("shows reconnecting, not sign-in, keeps the session, and grants nothing meanwhile", async () => {
    openApp();
    expect(await screen.findByRole("heading", { name: /can.t reach zoikostream/i })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Sign in" })).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Console" })).not.toBeInTheDocument();
    expect(localStorage.getItem("token")).toBe(TOKEN);
  });

  it("signs straight back in when the connection returns", async () => {
    openApp();
    await reconnecting();
    me = () => Promise.resolve({ data: USER });
    act(() => goOnline());
    expect(await screen.findByRole("heading", { name: "Console" })).toBeInTheDocument();
  });

  it("re-checks when the app returns to the foreground", async () => {
    openApp();
    await reconnecting();
    me = () => Promise.resolve({ data: USER });
    act(() => setVisibility("hidden"));
    act(() => setVisibility("visible"));
    expect(await screen.findByRole("heading", { name: "Console" })).toBeInTheDocument();
  });

  it("re-checks from Try again", async () => {
    const user = userEvent.setup();
    openApp();
    await reconnecting();
    me = () => Promise.resolve({ data: USER });
    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("heading", { name: "Console" })).toBeInTheDocument();
  });

  it("re-checks on its own timer while nothing else prompts it", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    openApp();
    await reconnecting();
    await act(async () => {}); // let the retry effect install its interval
    me = () => Promise.resolve({ data: USER });
    const before = meCalls();
    await act(async () => { await vi.advanceTimersByTimeAsync(UNREACHABLE_RETRY_MS - 1_000); });
    expect(meCalls()).toBe(before);
    await act(async () => { await vi.advanceTimersByTimeAsync(1_000); });
    expect(meCalls()).toBe(before + 1);
    expect(await screen.findByRole("heading", { name: "Console" })).toBeInTheDocument();
  });

  it("goes to sign-in with the reason if the server, once reached, says the session ended", async () => {
    openApp();
    await reconnecting();
    me = () => Promise.reject(sessionEnded("idle"));
    act(() => goOnline());
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    expect(screen.getByText(SESSION_END_MESSAGES.idle)).toBeInTheDocument();
    expect(localStorage.getItem("token")).toBeNull();
  });

  it("runs one check at a time, however many prompts arrive together", async () => {
    const user = userEvent.setup();
    openApp();
    await reconnecting();
    let answer;
    me = () => new Promise((resolve) => { answer = resolve; });
    const before = meCalls();
    act(() => goOnline());
    act(() => setVisibility("visible"));
    await user.click(screen.getByRole("button", { name: /try again|checking/i }));
    act(() => goOnline());
    expect(meCalls() - before).toBe(1);
    await act(async () => answer({ data: USER }));
    expect(await screen.findByRole("heading", { name: "Console" })).toBeInTheDocument();
  });
});

describe("signing out", () => {
  it("is not undone by a later reconnect or a return to the foreground", async () => {
    const user = userEvent.setup();
    localStorage.setItem("token", TOKEN);
    openApp();
    await screen.findByRole("heading", { name: "Console" });
    await user.click(screen.getByRole("button", { name: "Log out" }));
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    const after = meCalls();
    act(() => goOnline());
    act(() => setVisibility("hidden"));
    act(() => setVisibility("visible"));
    await new Promise((r) => setTimeout(r, 50));
    expect(meCalls()).toBe(after);
    expect(localStorage.getItem("token")).toBeNull();
    expect(screen.getByRole("heading", { name: "Sign in" })).toBeInTheDocument();
  });
});

describe("credentials", () => {
  it("are never written to the console through a disconnect, reconnect and server-ended session", async () => {
    const spies = ["log", "info", "warn", "error", "debug"].map((m) => vi.spyOn(console, m).mockImplementation(() => {}));
    localStorage.setItem("token", TOKEN);
    me = () => Promise.reject(networkError());
    openApp();
    await reconnecting();
    me = () => Promise.resolve({ data: USER });
    act(() => goOnline());
    await screen.findByRole("heading", { name: "Console" });
    vi.mocked(api.get).mockImplementation((url) =>
      url === "/auth/session" ? Promise.reject(sessionEnded("idle")) : Promise.resolve({ data: {} }));
    act(() => setVisibility("visible"));
    await screen.findByRole("heading", { name: "Sign in" });
    const logged = spies.flatMap((s) => s.mock.calls).map((args) => args.map(String).join(" ")).join("\n");
    expect(logged).not.toContain(TOKEN);
    spies.forEach((s) => s.mockRestore());
  });
});
