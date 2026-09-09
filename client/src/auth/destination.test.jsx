// Post-login routing and event-console authorization.
//
// The bug these lock out: the ACCOUNT role "host" mapped straight to /host/dashboard with no
// event id, and the console then silently attached to whichever event of the organization
// ranked highest. Every host-persona login therefore opened a Producer Console for an event
// the user had no EventAssignment on, and the backend correctly refused control — "No host
// assigned", "View only", "You aren't assigned to run this event."
//
// The distinction that has to hold everywhere below:
//
//   ACCOUNT role      models/user.ROLES — a persona on the organization. Says what someone
//                     does, never which event they run.
//   EVENT role        EventAssignment — who runs a SPECIFIC event. The only thing that
//                     grants console access, and only the server may answer it.
import { act, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Navigate, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../api", () => ({
  default: { get: vi.fn(), post: vi.fn() },
  // AuthContext imports this from api.js (the module that fires it), so the mock must
  // provide it or the whole file fails to collect.
  AUTH_EXPIRED_EVENT: "zoiko:auth-expired",
  errMsg: (e) => e?.message ?? "Something went wrong.",
  errCode: () => null,
}));

vi.mock("../ui/Toast", () => ({
  notify: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
}));

import api from "../api";
import { notify } from "../ui/Toast";
import { AuthProvider, useAuth } from "./AuthContext";
import { accountHome, resolvePostLogin } from "./destination";
import { roleHome } from "./roleHome";
import EventConsoleRoute from "../components/EventConsoleRoute";
import ProtectedRoute from "../components/ProtectedRoute";
import RoleRoute from "../components/RoleRoute";

const EVENT_A = "aaaaaaaa-1111-1111-1111-111111111111";
const EVENT_B = "bbbbbbbb-2222-2222-2222-222222222222";

const account = (role, id = "u1") => ({
  id, full_name: "Test Person", email: `${role}@example.com`, role,
  organization_name: "Northwind",
});

/** The backend's answer for `GET /events/{id}/assignment`. */
const access = (over = {}) => ({
  event_id: EVENT_A, assigned_roles: [], can_host: false, can_moderate: false,
  can_contribute: false, via_org_role: false, ...over,
});

const forbidden = () => {
  const error = new Error("Not found");
  error.response = { status: 404 };
  return error;
};

// Stand-ins: this suite is about who may REACH a console, not what it renders.
const Producer = () => <h1>Producer Console</h1>;
const Moderation = () => <h1>Moderation console</h1>;
const OrgDashboard = () => <h1>Organization dashboard</h1>;
const AdminConsole = () => <h1>Admin console</h1>;
const Billing = () => <h1>Billing</h1>;
const Picker = () => <h1>Events you are assigned to</h1>;
const LoginScreen = () => <h1>Sign in</h1>;
const Legacy = () => <h1>Legacy dashboard</h1>;

function RootRedirect() {
  const { user, loading } = useAuth();
  if (loading) return null;
  if (!user) return <Navigate to="/login" replace />;
  return <Navigate to={roleHome(user.role) || "/"} replace />;
}

function renderApp(path) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <AuthProvider>
        <Routes>
          <Route path="/" element={<RootRedirect />} />
          <Route path="/login" element={<LoginScreen />} />
          <Route element={<ProtectedRoute />}>
            <Route path="/events/mine" element={<Picker />} />
            <Route path="/dashboard" element={<Legacy />} />
            {/* Mirrors App.jsx: the organization dashboard is open to ANY member, which is
                what stops a non-admin account being bounced out of it and into a console. */}
            <Route path="/organization/dashboard" element={<OrgDashboard />} />
          </Route>
          <Route element={<RoleRoute allow={["org_admin"]} />}>
            <Route path="/organization/billing" element={<Billing />} />
          </Route>
          <Route element={<RoleRoute allow={["super_admin"]} />}>
            <Route path="/admin/dashboard" element={<AdminConsole />} />
          </Route>
          {/* The real pairing from App.jsx: coarse account gate outside, per-event
              assignment gate inside. */}
          <Route element={<RoleRoute allow={["host", "moderator", "org_admin", "super_admin"]} />}>
            <Route element={<EventConsoleRoute capability="can_host" />}>
              <Route path="/host/dashboard" element={<Producer />} />
            </Route>
            <Route element={<EventConsoleRoute capability="can_moderate" />}>
              <Route path="/moderator/dashboard" element={<Moderation />} />
            </Route>
          </Route>
        </Routes>
      </AuthProvider>
    </MemoryRouter>,
  );
}

/** Sign in as `role`, with `assignment` as the answer to any /assignment lookup. */
function signedInAs(role, { assignment } = {}) {
  localStorage.setItem("token", "good.jwt.value");
  api.get.mockImplementation((url) => {
    if (url === "/auth/me") return Promise.resolve({ data: account(role) });
    if (url.includes("/assignment")) {
      return assignment ? Promise.resolve({ data: assignment }) : Promise.reject(forbidden());
    }
    return Promise.resolve({ data: {} });
  });
}

beforeEach(() => {
  localStorage.clear();
  sessionStorage.clear();
  vi.clearAllMocks();
});

afterEach(() => {
  localStorage.clear();
  sessionStorage.clear();
});

// ══ account role is not event role ══════════════════════════════════════════════════════

describe("the canonical account-role map", () => {
  it("has exactly two rules", () => {
    expect(accountHome("super_admin")).toBe("/admin/dashboard");
    // Everything else — org_admin, billing_admin, viewer, and every event persona.
    for (const role of ["org_admin", "billing_admin", "viewer", "host", "moderator",
                        "speaker", "something_new"]) {
      expect(accountHome(role)).toBe("/organization/dashboard");
    }
  });

  it("puts NO event role in the default resolver", () => {
    // THE RULE. host/moderator/speaker are EventAssignment roles; a past assignment must
    // never become a landing page, not even via an intermediate hop that auto-opens.
    for (const role of ["host", "moderator", "speaker"]) {
      const home = accountHome(role);
      expect(home).toBe("/organization/dashboard");
      for (const console of ["/host/dashboard", "/moderator/dashboard",
                             "/speaker/backstage", "/events/mine"]) {
        expect(home).not.toBe(console);
      }
    }
  });

  it("is the same map roleHome exposes, so no caller can drift", () => {
    for (const role of ["org_admin", "super_admin", "host", "moderator", "speaker",
                        "viewer", "billing_admin"]) {
      expect(roleHome(role)).toBe(accountHome(role));
    }
  });
});

// ══ the post-login resolver ═════════════════════════════════════════════════════════════

describe("post-login destination", () => {
  it("routes each account role to its own home with no deep-link", async () => {
    expect((await resolvePostLogin(account("org_admin"), null)).to)
      .toBe("/organization/dashboard");
    expect((await resolvePostLogin(account("super_admin"), null)).to)
      .toBe("/admin/dashboard");
    expect((await resolvePostLogin(account("billing_admin"), null)).to)
      .toBe("/organization/dashboard");
    expect(api.get).not.toHaveBeenCalled();
  });

  it("sends a host persona to the organization dashboard, not to a console", async () => {
    const { to } = await resolvePostLogin(account("host"), null);
    expect(to).toBe("/organization/dashboard");
    expect(api.get).not.toHaveBeenCalled();
  });

  it("ignores a host deep-link that carries no event", async () => {
    // A Producer Console with no event is not a destination — this is the exact URL the old
    // roleHome produced.
    const { to } = await resolvePostLogin(account("host"),
                                          { pathname: "/host/dashboard", search: "" });
    expect(to).toBe("/organization/dashboard");
    expect(api.get).not.toHaveBeenCalled();
  });

  it("honours a host deep-link only after the server confirms the assignment", async () => {
    api.get.mockResolvedValueOnce({ data: access({ can_host: true, assigned_roles: ["host"] }) });
    const { to, reason } = await resolvePostLogin(
      account("host"), { pathname: "/host/dashboard", search: `?event=${EVENT_A}` });
    expect(api.get).toHaveBeenCalledWith(`/events/${EVENT_A}/assignment`);
    expect(to).toBe(`/host/dashboard?event=${EVENT_A}`);
    expect(reason).toBeNull();
  });

  it("refuses a host deep-link the server does not back, and says why", async () => {
    api.get.mockResolvedValueOnce({ data: access({ can_host: false }) });
    const { to, reason } = await resolvePostLogin(
      account("host"), { pathname: "/host/dashboard", search: `?event=${EVENT_A}` });
    expect(to).toBe("/organization/dashboard");
    expect(reason).toMatch(/aren't assigned/i);
  });

  it("fails closed when the assignment check cannot be answered", async () => {
    api.get.mockRejectedValueOnce(forbidden());
    const { to } = await resolvePostLogin(
      account("host"), { pathname: "/host/dashboard", search: `?event=${EVENT_A}` });
    expect(to).toBe("/organization/dashboard");
  });

  it("does not let a moderator assignment open the Producer Console", async () => {
    // can_moderate true, can_host false: the console asked for is the host one.
    api.get.mockResolvedValueOnce({
      data: access({ can_moderate: true, assigned_roles: ["moderator"] }) });
    const { to } = await resolvePostLogin(
      account("host"), { pathname: "/host/dashboard", search: `?event=${EVENT_A}` });
    expect(to).toBe("/organization/dashboard");
  });

  it("passes an ordinary saved page through to its own guard", async () => {
    const { to } = await resolvePostLogin(
      account("org_admin"), { pathname: "/organization/events", search: "?page=2" });
    expect(to).toBe("/organization/events?page=2");
    expect(api.get).not.toHaveBeenCalled();
  });
});


// ══ the organization dashboard must stay put ════════════════════════════════════════════
//
// The reported regression: a member who asked for /organization/dashboard was carried out
// of it and into a Producer Console. Two causes, both fixed, both pinned here — the route
// was admin-only (so a non-admin got bounced to their own home), and that home auto-opened
// a single assignment.

describe("the organization dashboard does not redirect", () => {
  const withAssignment = (role) => {
    localStorage.setItem("token", "good.jwt.value");
    api.get.mockImplementation((url) => {
      if (url === "/auth/me") return Promise.resolve({ data: account(role) });
      // The user IS an active host of event A. It must change nothing about where they land.
      if (url.includes("/assignment")) {
        return Promise.resolve({ data: access({ can_host: true, assigned_roles: ["host"] }) });
      }
      if (url.includes("assignments/mine")) {
        return Promise.resolve({ data: { items: [{ event_id: EVENT_A, title: "A",
                                                   roles: ["host"], can_host: true }],
                                         account_role: role } });
      }
      return Promise.resolve({ data: {} });
    });
  };

  for (const role of ["org_admin", "host", "moderator", "speaker", "billing_admin", "viewer"]) {
    it(`stays on the dashboard for a ${role} account that also hosts an event`, async () => {
      withAssignment(role);
      renderApp("/organization/dashboard");
      expect(await screen.findByText("Organization dashboard")).toBeInTheDocument();
      expect(screen.queryByText("Producer Console")).not.toBeInTheDocument();
      // Nothing asked about an event, because nothing was routing off one.
      const asked = api.get.mock.calls.filter(([url]) => url.includes("/assignment"));
      expect(asked).toHaveLength(0);
    });
  }

  it("stays put across a refresh", async () => {
    withAssignment("host");
    const first = renderApp("/organization/dashboard");
    expect(await screen.findByText("Organization dashboard")).toBeInTheDocument();
    first.unmount();

    renderApp("/organization/dashboard");
    expect(await screen.findByText("Organization dashboard")).toBeInTheDocument();
    expect(screen.queryByText("Producer Console")).not.toBeInTheDocument();
  });

  it("stays put with every stale event key planted in storage", async () => {
    for (const [key, value] of Object.entries({
      eventRole: "host", event: EVENT_A, lastEvent: EVENT_A, assignedEvent: EVENT_A,
      host: "true", contributorRole: "host", lastRoute: `/host/dashboard?event=${EVENT_A}`,
      returnTo: `/host/dashboard?event=${EVENT_A}`, redirectTo: "/host/dashboard",
      pendingRedirect: "/host/dashboard", intendedRoute: "/host/dashboard",
    })) {
      localStorage.setItem(key, value);
      sessionStorage.setItem(key, value);
    }
    withAssignment("host");

    renderApp("/organization/dashboard");

    expect(await screen.findByText("Organization dashboard")).toBeInTheDocument();
    expect(screen.queryByText("Producer Console")).not.toBeInTheDocument();
  });
});

describe("the assignment picker never navigates by itself", () => {
  it("has no auto-open, so a single assignment is still a click", async () => {
    const { readFileSync } = await import("node:fs");
    const { resolve } = await import("node:path");
    const source = readFileSync(resolve(process.cwd(), "src/pages/MyEvents.jsx"), "utf8");
    const code = source
      .replace(/\/\*[\s\S]*?\*\//g, "")
      .replace(/\{\/\*[\s\S]*?\*\/\}/g, "")
      .replace(/^\s*\/\/.*$/gm, "");
    // The effect that used to carry a lone assignment straight into the console is gone.
    expect(code).not.toContain("navigate(");
    expect(code).not.toContain("useNavigate");
  });
});

// ══ the routes themselves ═══════════════════════════════════════════════════════════════

describe("signing in normally", () => {
  it("takes an org_admin to the organization dashboard", async () => {
    signedInAs("org_admin");
    renderApp("/");
    expect(await screen.findByText("Organization dashboard")).toBeInTheDocument();
  });

  it("takes a super_admin to the admin console", async () => {
    signedInAs("super_admin");
    renderApp("/");
    expect(await screen.findByText("Admin console")).toBeInTheDocument();
  });

  it("takes a host persona to their assignments, never to the Producer Console", async () => {
    // The reported bug, end to end: this account HAS hosted events, and previously landed
    // in the Producer Console on every login.
    signedInAs("host", { assignment: access({ can_host: true }) });
    renderApp("/");
    expect(await screen.findByText("Organization dashboard")).toBeInTheDocument();
    expect(screen.queryByText("Producer Console")).not.toBeInTheDocument();
  });
});

describe("stale browser state cannot grant a console", () => {
  it("ignores a hand-written role and eventRole in storage", async () => {
    localStorage.setItem("role", "host");
    localStorage.setItem("eventRole", "host");
    localStorage.setItem("assignedEvent", EVENT_A);
    localStorage.setItem("lastEvent", EVENT_A);
    // The ACCOUNT is an ordinary org_admin; the server says so.
    signedInAs("org_admin");

    renderApp("/");

    expect(await screen.findByText("Organization dashboard")).toBeInTheDocument();
    expect(screen.queryByText("Producer Console")).not.toBeInTheDocument();
  });

  it("cannot bypass the backend check with a stored eventRole", async () => {
    localStorage.setItem("eventRole", "host");
    localStorage.setItem("assignedEvent", EVENT_A);
    // Account may use consoles, but the server refuses THIS event.
    signedInAs("host", { assignment: null });

    renderApp(`/host/dashboard?event=${EVENT_A}`);

    expect(await screen.findByText("Organization dashboard")).toBeInTheDocument();
    expect(screen.queryByText("Producer Console")).not.toBeInTheDocument();
  });
});

describe("the Producer Console route", () => {
  it("will not open without an event", async () => {
    signedInAs("host", { assignment: access({ can_host: true }) });
    renderApp("/host/dashboard");
    expect(await screen.findByText("Organization dashboard")).toBeInTheDocument();
    expect(screen.queryByText("Producer Console")).not.toBeInTheDocument();
    // No point asking about an event that was never named.
    const asked = api.get.mock.calls.filter(([url]) => url.includes("/assignment"));
    expect(asked).toHaveLength(0);
  });

  it("opens for a confirmed host assignment", async () => {
    signedInAs("host", { assignment: access({ can_host: true, assigned_roles: ["host"] }) });
    renderApp(`/host/dashboard?event=${EVENT_A}`);
    expect(await screen.findByText("Producer Console")).toBeInTheDocument();
    expect(api.get).toHaveBeenCalledWith(`/events/${EVENT_A}/assignment`);
  });

  it("refuses an event the user is not assigned to, and says so", async () => {
    signedInAs("host", { assignment: access({ can_host: false }) });
    renderApp(`/host/dashboard?event=${EVENT_B}`);
    expect(await screen.findByText("Organization dashboard")).toBeInTheDocument();
    await waitFor(() =>
      expect(notify.error).toHaveBeenCalledWith("You aren't assigned to run this event."));
  });

  it("treats a revoked assignment as no assignment", async () => {
    // Same account, same event: the server simply stops saying can_host.
    signedInAs("host", { assignment: access({ can_host: false, assigned_roles: [] }) });
    renderApp(`/host/dashboard?event=${EVENT_A}`);
    expect(await screen.findByText("Organization dashboard")).toBeInTheDocument();
  });

  it("does not let an assignment for one event open another", async () => {
    localStorage.setItem("token", "good.jwt.value");
    api.get.mockImplementation((url) => {
      if (url === "/auth/me") return Promise.resolve({ data: account("host") });
      // Host of A only.
      if (url === `/events/${EVENT_A}/assignment`) {
        return Promise.resolve({ data: access({ can_host: true }) });
      }
      if (url === `/events/${EVENT_B}/assignment`) {
        return Promise.resolve({ data: access({ event_id: EVENT_B, can_host: false }) });
      }
      return Promise.resolve({ data: {} });
    });

    const view = renderApp(`/host/dashboard?event=${EVENT_A}`);
    expect(await screen.findByText("Producer Console")).toBeInTheDocument();
    view.unmount();

    renderApp(`/host/dashboard?event=${EVENT_B}`);
    expect(await screen.findByText("Organization dashboard")).toBeInTheDocument();
    expect(screen.queryByText("Producer Console")).not.toBeInTheDocument();
  });

  it("does not render the console while the assignment is still being checked", async () => {
    localStorage.setItem("token", "good.jwt.value");
    let release;
    api.get.mockImplementation((url) => {
      if (url === "/auth/me") return Promise.resolve({ data: account("host") });
      return new Promise((r) => { release = () => r({ data: access({ can_host: true }) }); });
    });

    renderApp(`/host/dashboard?event=${EVENT_A}`);
    await waitFor(() => expect(release).toBeTypeOf("function"));
    // The window in which a read-only console used to appear.
    expect(screen.queryByText("Producer Console")).not.toBeInTheDocument();

    await act(async () => { release(); });
    expect(await screen.findByText("Producer Console")).toBeInTheDocument();
  });

  it("stays accessible on refresh with a valid assignment", async () => {
    signedInAs("host", { assignment: access({ can_host: true }) });
    const first = renderApp(`/host/dashboard?event=${EVENT_A}`);
    expect(await screen.findByText("Producer Console")).toBeInTheDocument();
    first.unmount();

    renderApp(`/host/dashboard?event=${EVENT_A}`);
    expect(await screen.findByText("Producer Console")).toBeInTheDocument();
  });

  it("is unreachable after logout, including via browser Back", async () => {
    signedInAs("host", { assignment: access({ can_host: true }) });
    const view = renderApp(`/host/dashboard?event=${EVENT_A}`);
    expect(await screen.findByText("Producer Console")).toBeInTheDocument();
    view.unmount();

    // Signed out: no token, and the server would refuse anyway.
    localStorage.clear();
    renderApp(`/host/dashboard?event=${EVENT_A}`);
    expect(await screen.findByText("Sign in")).toBeInTheDocument();
    expect(screen.queryByText("Producer Console")).not.toBeInTheDocument();
  });

  it("lets an org_admin run their own organization's event", async () => {
    // The backend grants this through org authority rather than an assignment row, and the
    // guard simply believes the server.
    signedInAs("org_admin", {
      assignment: access({ can_host: true, via_org_role: true }) });
    renderApp(`/host/dashboard?event=${EVENT_A}`);
    expect(await screen.findByText("Producer Console")).toBeInTheDocument();
  });
});
