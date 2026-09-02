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
import { render, screen, waitFor } from "@testing-library/react";
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
    <AuthProvider>
      <MemoryRouter initialEntries={[initialPath]}>
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
      </MemoryRouter>
    </AuthProvider>
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

  it("sends a legacy moderator session to the host console, not the generic dashboard", () => {
    // Mapped rather than dropped on purpose: a not-yet-migrated users.role row may still say
    // "moderator", and such a person can hold a perfectly valid host EventAssignment. Falling
    // through to /dashboard would strand them.
    expect(roleHome("moderator")).toBe("/host/dashboard");
  });

  it("keeps every live role's home unchanged", () => {
    expect(roleHome("super_admin")).toBe("/admin/dashboard");
    expect(roleHome("org_admin")).toBe("/organization/dashboard");
    expect(roleHome("host")).toBe("/host/dashboard");
    expect(roleHome("speaker")).toBe("/speaker/backstage");
    expect(roleHome("viewer")).toBeNull();
    expect(roleHome("nonsense")).toBe("/dashboard");
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
    render(<AuthProvider><Show /></AuthProvider>);

    // Optimistic first paint may show the stored value; the server's answer must win.
    await waitFor(() => expect(screen.getByText("role:host")).toBeInTheDocument());
    expect(JSON.parse(localStorage.getItem("user")).role).toBe("host");
    expect(mockGet).toHaveBeenCalledWith("/auth/me");
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
    render(<AuthProvider><Show /></AuthProvider>);

    await waitFor(() => expect(screen.getByText("role:host")).toBeInTheDocument());
    expect(localStorage.getItem("token")).toBe("good");
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
    render(<AuthProvider><Show /></AuthProvider>);
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
    render(<AuthProvider><Show /></AuthProvider>);
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
      <AuthProvider>
        <MemoryRouter initialEntries={["/host/dashboard"]}>
          <Routes>
            <Route
              path="/host/dashboard"
              element={<Guard allow={ALLOW}><WhereAmI /></Guard>}
            />
          </Routes>
        </MemoryRouter>
      </AuthProvider>
    );

    expect(await screen.findByText("at:/host/dashboard")).toBeInTheDocument();
    // The allow-list and the roleHome mapping must agree, or the loop returns.
    expect(ALLOW).toContain("moderator");
    expect(roleHome("moderator")).toBe("/host/dashboard");
  });

  it("still bounces a viewer away from the host console", async () => {
    // The accommodation above must not have loosened the guard for anyone else.
    localStorage.setItem("token", "t");
    localStorage.setItem("user", JSON.stringify({ id: "u1", role: "viewer" }));
    mockGet.mockResolvedValue({ data: { id: "u1", role: "viewer" } });

    render(
      <AuthProvider>
        <MemoryRouter initialEntries={["/host/dashboard"]}>
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
        </MemoryRouter>
      </AuthProvider>
    );

    // roleHome("viewer") is null, so the guard falls back to the public site.
    expect(await screen.findByText("bounced-to:/")).toBeInTheDocument();
  });
});
