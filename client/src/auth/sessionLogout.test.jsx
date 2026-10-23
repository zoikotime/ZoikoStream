// Being signed out while using the app.
//
// Every route to the sign-in page was traced (api.js 401 interceptor, the live socket's 4401
// close, the session keeper, AuthContext's /auth/me check, the cross-tab "ended" message,
// explicit logout). The server ends a session only for idle, maximum length, sign-out or a
// deactivated account (services/auth_sessions.py), and those are policy, unchanged here. Two
// client paths ended sessions that were still valid:
//
//   * A 401 was acted on whatever token the refused request had carried. A request still in
//     flight from a session that had ended — answered after the user signed in again — cleared
//     the NEW sign-in and told every tab it had ended. It now counts only when the request
//     carried the token this tab holds; that also makes a burst of 401s end a session once.
//   * /auth/me answering 403 was treated as "signed out" and the token cleared. The server
//     never sends 403 there (every refusal of get_current_user is a 401); a 403 can only come
//     from something in front of it, so the token is now kept and the check retried.
//
// There are no refresh tokens in this design (one bearer token per server-side session), so
// "refresh and retry" is not part of it: nothing here renews a session the server ended.
// This file uses the REAL api module — its interceptors run — with a stand-in transport.
import { act, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useNavigate } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import api, { AUTH_EXPIRED_EVENT } from "../api";
import { AuthProvider, useAuth } from "./AuthContext";
import ProtectedRoute from "../components/ProtectedRoute";
import { SESSION_END_MESSAGES, __resetSessionChannelForTests, peekSessionEndReason, readToken } from "./sessionStore";

const OLD = "old.session.token.never-log-me";
const NEW = "new.session.token.never-log-me";
const USER = { id: "u1", full_name: "Ada Admin", email: "ada@example.com", role: "org_admin" };
const status = () => ({
  server_now: new Date().toISOString(),
  idle_expires_at: new Date(Date.now() + 25 * 60_000).toISOString(),
  absolute_expires_at: new Date(Date.now() + 10 * 3_600_000).toISOString(),
});

// ── the stand-in transport ───────────────────────────────────────────────────────────────
// `route(config)` answers each request: a value -> 200 with that body; ["fail", status, data]
// -> an HTTP error; ["network"] -> no response at all; a Promise -> answered when it settles.
let route;
const sent = [];                          // [method url, Authorization] per request
const realAdapter = api.defaults.adapter;
const respond = (config, outcome) => {
  if (Array.isArray(outcome) && outcome[0] === "network") {
    return Promise.reject(Object.assign(new Error("Network Error"), { code: "ERR_NETWORK", config, request: {} }));
  }
  if (Array.isArray(outcome) && outcome[0] === "fail") {
    const [, code, data = {}] = outcome;
    return Promise.reject(Object.assign(new Error(`Request failed with status code ${code}`), {
      config, request: {}, response: { status: code, data, headers: {}, config },
    }));
  }
  return Promise.resolve({ data: outcome, status: 200, statusText: "OK", headers: {}, config, request: {} });
};
const ended = (reason) => ["fail", 401, { detail: { code: "SESSION_EXPIRED", reason, message: "x" } }];
const revoked = () => ["fail", 401, { detail: { code: "SESSION_REVOKED", reason: "revoked", message: "x" } }];
const defaultRoute = (config) => {
  if (config.url === "/auth/me") return USER;
  if (config.url === "/auth/session" || config.url === "/auth/session/activity") return status();
  return { ok: true };
};

let expired;
const onExpired = () => { expired += 1; };
beforeEach(() => {
  localStorage.clear();
  sessionStorage.clear();
  sent.length = 0;
  expired = 0;
  route = defaultRoute;
  api.defaults.adapter = (config) => {
    sent.push([`${config.method?.toUpperCase()} ${config.url}`, config.headers?.Authorization || null]);
    const outcome = route(config);
    return outcome instanceof Promise ? outcome.then((o) => respond(config, o)) : respond(config, outcome);
  };
  window.addEventListener(AUTH_EXPIRED_EVENT, onExpired);
});
afterEach(() => {
  api.defaults.adapter = realAdapter;
  window.removeEventListener(AUTH_EXPIRED_EVENT, onExpired);
  __resetSessionChannelForTests();
  localStorage.clear();
  sessionStorage.clear();
});

// ── the API client on its own ────────────────────────────────────────────────────────────
describe("the API client's 401 handling", () => {
  it("a 401 for the session in use ends it, once, with the server's reason", async () => {
    localStorage.setItem("token", OLD);
    route = (c) => (c.url === "/organization/overview" ? ended("idle") : defaultRoute(c));
    await expect(api.get("/organization/overview")).rejects.toMatchObject({ response: { status: 401 } });
    expect(readToken()).toBeNull();
    expect(peekSessionEndReason()).toBe("idle");
    expect(expired).toBe(1);
  });

  it("a burst of 401s for the same session ends it once, not once per request", async () => {
    localStorage.setItem("token", OLD);
    route = (c) => (c.url.startsWith("/organization/") ? ended("idle") : defaultRoute(c));
    await Promise.allSettled([api.get("/organization/a"), api.get("/organization/b"), api.get("/organization/c")]);
    expect(sent.filter(([, auth]) => auth === `Bearer ${OLD}`)).toHaveLength(3);
    expect(expired).toBe(1);
  });

  it("a late 401 for an OLDER session does not end the one in use", async () => {
    localStorage.setItem("token", OLD);
    let answer;
    route = (c) => (c.url === "/organization/slow" ? new Promise((r) => { answer = r; }) : defaultRoute(c));
    const slow = api.get("/organization/slow");                 // carries OLD
    await waitFor(() => expect(sent).toHaveLength(1));
    localStorage.setItem("token", NEW);                         // signed in again meanwhile
    answer(ended("idle"));
    await expect(slow).rejects.toMatchObject({ response: { status: 401 } });   // the caller still sees it
    expect(readToken()).toBe(NEW);
    expect(peekSessionEndReason()).toBeNull();
    expect(expired).toBe(0);
  });

  it.each([
    ["a 403 (not allowed)", ["fail", 403, { detail: "org_admin access required" }]],
    ["a network failure", ["network"]],
    ["a server error", ["fail", 503, { detail: "Service temporarily unavailable" }]],
    ["an edge error", ["fail", 525, { detail: "SSL handshake failed" }]],
  ])("%s keeps the session", async (_, outcome) => {
    localStorage.setItem("token", OLD);
    route = (c) => (c.url === "/organization/x" ? outcome : defaultRoute(c));
    await expect(api.get("/organization/x")).rejects.toBeTruthy();
    expect(readToken()).toBe(OLD);
    expect(expired).toBe(0);
  });

  it("a 401 that is an answer (wrong password at step-up) keeps the session", async () => {
    localStorage.setItem("token", OLD);
    route = (c) => (c.url === "/auth/step-up" ? ["fail", 401, { detail: "Incorrect password" }] : defaultRoute(c));
    await expect(api.post("/auth/step-up", {})).rejects.toBeTruthy();
    expect(readToken()).toBe(OLD);
    expect(expired).toBe(0);
  });
});

// ── the whole app: provider, guard, sign-in page ─────────────────────────────────────────
function Console() {
  const { logout } = useAuth();
  return (
    <>
      <h1>Console</h1>
      <button type="button" onClick={logout}>Log out</button>
    </>
  );
}
// Like pages/auth/Login.jsx: establish the session, then go where the user was headed.
function SignIn() {
  const { setSession } = useAuth();
  const navigate = useNavigate();
  const reason = peekSessionEndReason();
  const signIn = async () => {
    await setSession({ access_token: NEW, user: USER }, { remember: true });
    navigate("/console", { replace: true });
  };
  return (
    <>
      <h1>Sign in</h1>
      {reason && <p>{SESSION_END_MESSAGES[reason]}</p>}
      <button type="button" onClick={signIn}>Sign in again</button>
    </>
  );
}
const openApp = () =>
  render(
    <MemoryRouter initialEntries={["/console"]}>
      <AuthProvider>
        <Routes>
          <Route path="/login" element={<SignIn />} />
          <Route element={<ProtectedRoute />}>
            <Route path="/console" element={<Console />} />
          </Route>
        </Routes>
      </AuthProvider>
    </MemoryRouter>,
  );
const consoleShown = () => screen.findByRole("heading", { name: "Console" });

describe("using the app", () => {
  it("restores a stored session at startup and stays signed in through normal requests", async () => {
    localStorage.setItem("token", OLD);
    openApp();
    await consoleShown();
    await act(async () => {
      await Promise.all([api.get("/organization/overview"), api.get("/events"), api.post("/auth/session/activity")]);
    });
    expect(screen.getByRole("heading", { name: "Console" })).toBeInTheDocument();
    expect(readToken()).toBe(OLD);
    expect(sent.every(([, auth]) => auth === `Bearer ${OLD}`)).toBe(true);
  });

  it("a dropped connection mid-use does not sign anyone out", async () => {
    localStorage.setItem("token", OLD);
    openApp();
    await consoleShown();
    route = () => ["network"];
    await act(async () => { await Promise.allSettled([api.get("/organization/overview"), api.get("/events")]); });
    expect(screen.getByRole("heading", { name: "Console" })).toBeInTheDocument();
    expect(readToken()).toBe(OLD);
  });

  it("a 403 mid-use is a permission answer, not a sign-out", async () => {
    localStorage.setItem("token", OLD);
    openApp();
    await consoleShown();
    route = (c) => (c.url === "/organization/security" ? ["fail", 403, { detail: "org_admin access required" }] : defaultRoute(c));
    await act(async () => { await api.get("/organization/security").catch(() => {}); });
    expect(screen.getByRole("heading", { name: "Console" })).toBeInTheDocument();
    expect(readToken()).toBe(OLD);
  });

  it("a session the server ended mid-use goes to sign-in with the reason, and grants nothing", async () => {
    localStorage.setItem("token", OLD);
    openApp();
    await consoleShown();
    route = (c) => (c.url === "/auth/me" ? revoked() : c.url.startsWith("/organization/") ? revoked() : defaultRoute(c));
    await act(async () => { await api.get("/organization/overview").catch(() => {}); });
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Console" })).not.toBeInTheDocument();
    expect(readToken()).toBeNull();
    expect(screen.getByText(SESSION_END_MESSAGES.revoked)).toBeInTheDocument();
  });

  it("signing out, then straight back in, survives a late 401 from the old session", async () => {
    localStorage.setItem("token", OLD);
    openApp();
    await consoleShown();

    // A slow request from the OLD session is still in flight when the user signs out.
    let answerSlow;
    route = (c) => (c.url === "/organization/slow" ? new Promise((r) => { answerSlow = r; }) : defaultRoute(c));
    let slow;
    act(() => { slow = api.get("/organization/slow").catch((e) => e); });
    // It has left, carrying the OLD token (axios attaches it a tick later).
    await waitFor(() => expect(sent).toContainEqual(["GET /organization/slow", `Bearer ${OLD}`]));
    await act(async () => screen.getByRole("button", { name: "Log out" }).click());
    await act(async () => screen.getByRole("button", { name: "Sign in again" }).click());
    await consoleShown();
    expect(readToken()).toBe(NEW);

    // The old session's request finally answers: its session is over.
    await act(async () => { answerSlow(ended("reauth")); await slow; });
    expect(screen.getByRole("heading", { name: "Console" })).toBeInTheDocument();
    expect(readToken()).toBe(NEW);
    expect(expired).toBe(0);
  });

  it("explicit logout signs out and is not undone", async () => {
    localStorage.setItem("token", OLD);
    openApp();
    await consoleShown();
    await act(async () => screen.getByRole("button", { name: "Log out" }).click());
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    expect(readToken()).toBeNull();
    expect(sent.some(([what, auth]) => what === "POST /auth/logout" && auth === `Bearer ${OLD}`)).toBe(true);
  });
});

describe("the startup check", () => {
  it("a 403 from /auth/me keeps the stored session and retries instead of signing out", async () => {
    localStorage.setItem("token", OLD);
    route = (c) => (c.url === "/auth/me" ? ["fail", 403, { detail: "Forbidden" }] : defaultRoute(c));
    openApp();
    expect(await screen.findByRole("heading", { name: /can.t reach zoikostream/i })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Sign in" })).not.toBeInTheDocument();
    expect(readToken()).toBe(OLD);
    route = defaultRoute;
    await act(async () => window.dispatchEvent(new Event("online")));
    expect(await consoleShown()).toBeInTheDocument();
  });

  it("a 401 from /auth/me still means signed out, with the reason", async () => {
    localStorage.setItem("token", OLD);
    route = (c) => (c.url === "/auth/me" ? ended("absolute") : defaultRoute(c));
    openApp();
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    expect(screen.getByText(SESSION_END_MESSAGES.absolute)).toBeInTheDocument();
    expect(readToken()).toBeNull();
  });
});

describe("credentials", () => {
  it("never reach the console, through use, failures, sign-out and sign-in", async () => {
    const spies = ["log", "info", "warn", "error", "debug"].map((m) => vi.spyOn(console, m).mockImplementation(() => {}));
    localStorage.setItem("token", OLD);
    openApp();
    await consoleShown();
    route = (c) => (c.url === "/organization/x" ? ended("idle") : defaultRoute(c));
    await act(async () => { await api.get("/organization/x").catch(() => {}); });
    await screen.findByRole("heading", { name: "Sign in" });
    route = defaultRoute;
    await act(async () => screen.getByRole("button", { name: "Sign in again" }).click());
    await consoleShown();
    const logged = spies.flatMap((s) => s.mock.calls).map((args) => args.map(String).join(" ")).join("\n");
    expect(logged).not.toContain(OLD);
    expect(logged).not.toContain(NEW);
    spies.forEach((s) => s.mockRestore());
  });
});
