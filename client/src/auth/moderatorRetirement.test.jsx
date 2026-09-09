// Routing + session behaviour after the moderator role was retired.
//
// Two things are under test, and they are connected:
//
//   1. The role is gone from the app's routing vocabulary. /moderator/dashboard no longer
//      renders a console; roleHome() no longer sends anyone there; the role-token maps no
//      longer describe it.
//   2. A STALE stored session can no longer keep it alive. This is the half that mattered in
//      production: AuthContext used to trust localStorage.user forever, so a browser holding
//      a role="moderator" blob kept being routed as a moderator no matter what the server
//      thought. The role removal is only real if the session layer stops replaying it.
import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Routes, Route, Navigate, useLocation } from "react-router-dom";

import { roleHome } from "./roleHome";
import { ROLE_ORDER, ROLE_TONE } from "../data/moderation";
import { ROLES as ADMIN_ROLES } from "../pages/admin/roleInfo";
import { ROLE_PATH } from "../pages/organization/AssignPeopleModal";

// api.js is mocked at the module level so AuthContext's /auth/me call is controllable and no
// real network is attempted. AUTH_EXPIRED_EVENT must keep its real value — AuthContext
// subscribes to it by name.
const mockGet = vi.fn();
vi.mock("../api", () => ({
  default: { get: (...args) => mockGet(...args) },
  AUTH_EXPIRED_EVENT: "zoiko:auth-expired",
}));

const { AuthProvider, useAuth } = await import("./AuthContext");

// A minimal stand-in for RoleRoute's decision, so the test exercises the real roleHome()
// mapping rather than a copy of it.
function Guard({ allow, children }) {
  const { user, loading } = useAuth();
  if (loading) return <p>loading</p>;
  if (!user) return <p>redirected-to-login</p>;
  if (!allow.includes(user.role)) {
    const home = roleHome(user.role) || "/";
    return <p>bounced-to:{home}</p>;
  }
  return children;
}

function LegacyRedirect() {
  const { search } = useLocation();
  return <Navigate to={`/host/dashboard${search}`} replace />;
}

function WhereAmI() {
  const { pathname, search } = useLocation();
  return <p>at:{pathname}{search}</p>;
}

// Mirrors App.jsx's real shape: the legacy redirect sits OUTSIDE the guard (so a signed-out
// visitor is forwarded first and then asked to sign in by the destination, which is what
// preserves ?event=<id> through login), and /host/dashboard sits INSIDE it.
const renderApp = (initialPath) =>
  render(
    <MemoryRouter initialEntries={[initialPath]}>
      <AuthProvider>
        <Routes>
          <Route path="/moderator/dashboard" element={<LegacyRedirect />} />
          <Route
            path="/host/dashboard"
            element={
              <Guard allow={["host", "moderator", "org_admin", "super_admin"]}>
                <WhereAmI />
              </Guard>
            }
          />
          <Route path="/login" element={<p>login-page</p>} />
        </Routes>
      </AuthProvider>
    </MemoryRouter>
  );

beforeEach(() => {
  localStorage.clear();
  mockGet.mockReset();
});

afterEach(() => {
  localStorage.clear();
});

// ── the role is gone from the routing vocabulary ────────────────────────────────────────

describe("moderator is no longer a destination", () => {
  it("never routes any role to the deleted /moderator/dashboard", () => {
    for (const role of ["super_admin", "org_admin", "host", "speaker", "viewer", "moderator"]) {
      expect(roleHome(role)).not.toBe("/moderator/dashboard");
    }
  });

  it("does not strand a legacy moderator session", () => {
    // Unchanged intent, new destination. A not-yet-migrated users.role row may still say
    // "moderator", and that person must land somewhere they can actually use — falling
    // through to a generic /dashboard would strand them.
    //
    // They no longer need the host console to avoid that: no ACCOUNT role maps to an event
    // console any more (an EventAssignment is what opens one), and the organization
    // dashboard is open to every member. So a legacy moderator lands there like everybody
    // else, and nothing has to special-case a retired role to keep it working.
    expect(roleHome("moderator")).toBe("/organization/dashboard");
    expect(roleHome("moderator")).not.toBe("/dashboard");
  });

  it("keeps every live role's home a real, non-console destination", () => {
    expect(roleHome("super_admin")).toBe("/admin/dashboard");
    expect(roleHome("org_admin")).toBe("/organization/dashboard");
    // CHANGED DELIBERATELY: "host" is an account persona, not an assignment to a broadcast.
    // Mapping it to the Producer Console made a past assignment the user's landing page and
    // opened a console for an event they had no claim on. An event console is now reached
    // only with an explicit ?event=<id> the server confirms.
    expect(roleHome("host")).toBe("/organization/dashboard");
    // Same reasoning as "host": backstage is an event console, reached with a confirmed
    // assignment, not a place an account role begins.
    expect(roleHome("speaker")).toBe("/organization/dashboard");
    // `viewer` was null ("no app home"). It is now the organization dashboard, which is safe
    // because /organization/overview authorizes with get_my_org — any member — rather than
    // require_org_admin. Invited and public viewers still reach events by link, not by this
    // map, so nothing about their path changed.
    expect(roleHome("viewer")).toBe("/organization/dashboard");
    // An unknown role no longer falls through to a legacy generic dashboard.
    expect(roleHome("nonsense")).toBe("/organization/dashboard");
  });

  it("has dropped the role from the participant, admin and assignment vocabularies", () => {
    expect(ROLE_TONE).not.toHaveProperty("moderator");
    expect(ROLE_ORDER).not.toHaveProperty("moderator");
    expect(ADMIN_ROLES).not.toContain("moderator");
    expect(ROLE_PATH).not.toHaveProperty("Moderator");
    // ...and the surviving assignment paths still work.
    expect(ROLE_PATH.Host).toBe("hosts");
    expect(ROLE_PATH.Speaker).toBe("speakers");
  });

  it("forwards the retired URL to the host console, preserving ?event=<id>", async () => {
    // The whole point of the redirect: assignment emails sent before the retirement carry
    // /moderator/dashboard?event=<id>, and losing that id would open the wrong console.
    localStorage.setItem("token", "t");
    localStorage.setItem("user", JSON.stringify({ role: "host" }));
    mockGet.mockResolvedValue({ data: { id: "u1", role: "host" } });

    renderApp("/moderator/dashboard?event=abc-123");
    expect(await screen.findByText("at:/host/dashboard?event=abc-123")).toBeInTheDocument();
  });
});

// ── the session layer stops replaying a stale role ──────────────────────────────────────

describe("stale stored sessions", () => {
  it("replaces a stale stored role with what the server actually says", async () => {
    localStorage.setItem("token", "real-token");
    localStorage.setItem("user", JSON.stringify({ id: "u1", role: "moderator" }));
    mockGet.mockResolvedValue({ data: { id: "u1", role: "host" } });

    function Show() {
      const { user, loading } = useAuth();
      return <p>{loading ? "loading" : `role:${user?.role}`}</p>;
    }
    // AuthProvider sits inside a router now: logout navigates to /login rather than
    // relying on a guard to bounce the current page, so it needs router context. The
    // assertions below are unchanged.
    render(<MemoryRouter><AuthProvider><Show /></AuthProvider></MemoryRouter>);

    // The server's answer wins over the stale stored role — the point of the test, and it
    // still holds. What changed is that there is no optimistic paint from storage at all:
    // the stored role is never rendered, not even for one frame.
    await waitFor(() => expect(screen.getByText("role:host")).toBeInTheDocument());
    expect(mockGet).toHaveBeenCalledWith("/auth/me");
    // And the stale key is REMOVED rather than overwritten. Nothing writes the profile back,
    // so a later build cannot be tempted to read it as identity again.
    expect(localStorage.getItem("user")).toBeNull();
  });

  it("clears the session entirely when /auth/me returns 401", async () => {
    localStorage.setItem("token", "expired");
    localStorage.setItem("user", JSON.stringify({ id: "u1", role: "moderator" }));
    mockGet.mockRejectedValue({ response: { status: 401 } });

    // The legacy URL forwards first (unguarded, by design), then the host console's guard
    // sees a session that /auth/me has just invalidated and sends the visitor to login.
    renderApp("/moderator/dashboard?event=abc-123");

    expect(await screen.findByText("redirected-to-login")).toBeInTheDocument();
    expect(localStorage.getItem("token")).toBeNull();
    expect(localStorage.getItem("user")).toBeNull();
  });

  it("does NOT sign the user out over a network blip or a 500", async () => {
    // The other half of the 401 rule. Treating "the API is unreachable" as "your session is
    // finished" would be its own bug — an outage would log everybody out.
    localStorage.setItem("token", "good");
    localStorage.setItem("user", JSON.stringify({ id: "u1", role: "host" }));
    mockGet.mockRejectedValue({ message: "Network Error" });

    function Show() {
      const { user, loading } = useAuth();
      return <p>{loading ? "loading" : `role:${user?.role ?? "none"}`}</p>;
    }
    // AuthProvider sits inside a router now: logout navigates to /login rather than
    // relying on a guard to bounce the current page, so it needs router context. The
    // assertions below are unchanged.
    render(<MemoryRouter><AuthProvider><Show /></AuthProvider></MemoryRouter>);

    // The session is NOT destroyed: the credential survives, so the next navigation or
    // reload re-validates and signs them straight back in with no password.
    await waitFor(() => expect(screen.getByText("role:none")).toBeInTheDocument());
    expect(localStorage.getItem("token")).toBe("good");

    // What changed, and why: the stored profile is no longer rendered as identity while the
    // server is unreachable. Trusting it was the bug — a blob anyone can edit decided what
    // the console showed. So an outage costs this page load, not the session. Proof:
    mockGet.mockResolvedValue({ data: { id: "u1", role: "host" } });
    cleanup();
    render(<MemoryRouter><AuthProvider><Show /></AuthProvider></MemoryRouter>);
    await waitFor(() => expect(screen.getByText("role:host")).toBeInTheDocument());
  });

  it("ignores a stored user when no token is present", async () => {
    // A leftover `user` key with no token is not a session. This is what makes "log out" and
    // "clear site data halfway" both end up signed out rather than half-authenticated.
    localStorage.setItem("user", JSON.stringify({ id: "u1", role: "moderator" }));

    renderApp("/moderator/dashboard");

    expect(await screen.findByText("redirected-to-login")).toBeInTheDocument();
    expect(localStorage.getItem("user")).toBeNull();
    expect(mockGet).not.toHaveBeenCalled();
  });

  it("drops the user when a later request reports the session expired", async () => {
    // api.js dispatches AUTH_EXPIRED_EVENT on a 401 from any authenticated call; this is what
    // turns that into a re-render so the guards react at once instead of on the next reload.
    localStorage.setItem("token", "good");
    localStorage.setItem("user", JSON.stringify({ id: "u1", role: "host" }));
    mockGet.mockResolvedValue({ data: { id: "u1", role: "host" } });

    function Show() {
      const { user, loading } = useAuth();
      return <p>{loading ? "loading" : `role:${user?.role ?? "none"}`}</p>;
    }
    // AuthProvider sits inside a router now: logout navigates to /login rather than
    // relying on a guard to bounce the current page, so it needs router context. The
    // assertions below are unchanged.
    render(<MemoryRouter><AuthProvider><Show /></AuthProvider></MemoryRouter>);
    await waitFor(() => expect(screen.getByText("role:host")).toBeInTheDocument());

    window.dispatchEvent(new Event("zoiko:auth-expired"));
    await waitFor(() => expect(screen.getByText("role:none")).toBeInTheDocument());
  });

  it("does not block the public homepage on a validation round trip", async () => {
    // loading must start false with no token, or a signed-out visitor's first paint of "/"
    // is a spinner (or a blank screen) instead of the landing page.
    function Show() {
      const { loading } = useAuth();
      return <p>loading:{String(loading)}</p>;
    }
    // AuthProvider sits inside a router now: logout navigates to /login rather than
    // relying on a guard to bounce the current page, so it needs router context. The
    // assertions below are unchanged.
    render(<MemoryRouter><AuthProvider><Show /></AuthProvider></MemoryRouter>);
    expect(screen.getByText("loading:false")).toBeInTheDocument();
  });
});

// ── a legacy moderator session must not be bounced in a loop ────────────────────────────

describe("route guard behaviour for a legacy role", () => {
  it("does not bounce a legacy moderator between roleHome and the guard", async () => {
    // roleHome("moderator") points at /host/dashboard, so the host console's allow-list must
    // include it. If it did not, RoleRoute would redirect the role to a route that rejects it
    // — an infinite redirect loop. This test is the guard against reintroducing that.
    localStorage.setItem("token", "t");
    localStorage.setItem("user", JSON.stringify({ id: "u1", role: "moderator" }));
    mockGet.mockResolvedValue({ data: { id: "u1", role: "moderator" } });

    const ALLOW = ["host", "moderator", "org_admin", "super_admin"];
    render(
      <MemoryRouter initialEntries={["/host/dashboard"]}>
        <AuthProvider>
          <Routes>
            <Route
              path="/host/dashboard"
              element={<Guard allow={ALLOW}><WhereAmI /></Guard>}
            />
          </Routes>
        </AuthProvider>
      </MemoryRouter>
    );

    expect(await screen.findByText("at:/host/dashboard")).toBeInTheDocument();
    // The allow-list still carries the transitional entry, so a legacy moderator can load
    // the console shell it is forwarded to.
    expect(ALLOW).toContain("moderator");
    // And the loop this test exists to prevent is now structurally impossible rather than
    // merely avoided: roleHome no longer points at a guarded console at all, so there is no
    // pair of route + home that can disagree.
    expect(roleHome("moderator")).toBe("/organization/dashboard");
    expect(ALLOW).not.toContain(roleHome("moderator"));
  });

  it("still bounces a viewer away from the host console", async () => {
    // The accommodation above must not have loosened the guard for anyone else.
    localStorage.setItem("token", "t");
    localStorage.setItem("user", JSON.stringify({ id: "u1", role: "viewer" }));
    mockGet.mockResolvedValue({ data: { id: "u1", role: "viewer" } });

    render(
      <MemoryRouter initialEntries={["/host/dashboard"]}>
        <AuthProvider>
          <Routes>
            <Route
              path="/host/dashboard"
              element={
                <Guard allow={["host", "moderator", "org_admin", "super_admin"]}>
                  <WhereAmI />
                </Guard>
              }
            />
          </Routes>
        </AuthProvider>
      </MemoryRouter>
    );

    // roleHome("viewer") is null, so the guard falls back to the public site.
    // A viewer is still refused the host console. They land on the organization dashboard
    // now instead of the public site, because that is where roleHome sends every member.
    expect(await screen.findByText("bounced-to:/organization/dashboard")).toBeInTheDocument();
  });
});
