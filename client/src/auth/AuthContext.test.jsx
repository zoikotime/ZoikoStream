// Authentication routing tests.
//
// The point of this suite: a stale value in this browser must never be proof of
// authentication. The bug these tests lock out was that `AuthProvider` seeded `user`
// straight from `localStorage.getItem("user")` and hardcoded `loading: false`, so a leftover
// blob from an old session opened /organization/dashboard with no sign-in — and writing that
// key by hand did the same thing.
//
// Everything here drives the REAL router, the REAL guards and the REAL provider. Only the
// `api` module is mocked, at its boundary, so each test controls exactly what the server
// says and can assert on whether /auth/me was called at all.
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
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

import api from "../api";
import { AuthProvider, useAuth } from "./AuthContext";
import { roleHome } from "./roleHome";
import ProtectedRoute from "../components/ProtectedRoute";
import RoleRoute from "../components/RoleRoute";

const ORG_ADMIN = {
  id: "11111111-1111-1111-1111-111111111111",
  full_name: "Ada Admin",
  email: "ada@example.com",
  role: "org_admin",
  organization_name: "Northwind",
};

const SUPER_ADMIN = { ...ORG_ADMIN, id: "22222222-2222-2222-2222-222222222222",
                      role: "super_admin" };

/** A 401 shaped the way axios shapes one. */
const unauthorized = () => {
  const error = new Error("Invalid or expired token");
  error.response = { status: 401, data: { detail: "Invalid or expired token" } };
  error.config = { url: "/auth/me", headers: { Authorization: "Bearer stale" } };
  return error;
};

// Stand-ins for the real consoles: this suite is about who may REACH a route, and rendering
// the actual dashboards would drag in their data fetching without testing anything more.
const Dashboard = () => <h1>Organization dashboard</h1>;
const AdminConsole = () => <h1>Admin console</h1>;
const LoginScreen = () => <h1>Sign in</h1>;

// The root behaves as App.jsx's RootRedirect does: unauthenticated to /login, otherwise to
// the role's own home.
function RootRedirect() {
  const { user, loading } = useAuth();
  if (loading) return null;
  if (!user) return <Navigate to="/login" replace />;
  return <Navigate to={roleHome(user.role) || "/"} replace />;
}

function LogoutButton() {
  const { logout, user, loading } = useAuth();
  return (
    <div>
      <span data-testid="who">{loading ? "loading" : user ? user.email : "anonymous"}</span>
      <button type="button" onClick={logout}>Sign out</button>
    </div>
  );
}

function renderApp(path) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <AuthProvider>
        <Routes>
          <Route path="/" element={<RootRedirect />} />
          <Route path="/login" element={<LoginScreen />} />
          <Route element={<ProtectedRoute />}>
            <Route path="/organization/dashboard" element={<Dashboard />} />
            <Route path="/account" element={<LogoutButton />} />
          </Route>
          <Route element={<RoleRoute allow={["super_admin"]} />}>
            <Route path="/admin/dashboard" element={<AdminConsole />} />
          </Route>
        </Routes>
      </AuthProvider>
    </MemoryRouter>,
  );
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

describe("a browser with no session", () => {
  it("sends /organization/dashboard to /login", async () => {
    renderApp("/organization/dashboard");
    expect(await screen.findByText("Sign in")).toBeInTheDocument();
    expect(screen.queryByText("Organization dashboard")).not.toBeInTheDocument();
  });

  it("sends / to /login", async () => {
    renderApp("/");
    expect(await screen.findByText("Sign in")).toBeInTheDocument();
  });

  it("does not bother the server when there is no credential to present", async () => {
    renderApp("/organization/dashboard");
    await screen.findByText("Sign in");
    // Nothing to validate, and the resolution is the safe direction anyway.
    expect(api.get).not.toHaveBeenCalled();
  });
});

describe("stale browser state is not authentication", () => {
  it("ignores a hand-written localStorage user and redirects to /login", async () => {
    // This is the reported bug, reproduced exactly: an old (or forged) profile blob.
    localStorage.setItem("user", JSON.stringify(ORG_ADMIN));
    localStorage.setItem("isAuthenticated", "true");
    localStorage.setItem("role", "org_admin");

    renderApp("/organization/dashboard");

    expect(await screen.findByText("Sign in")).toBeInTheDocument();
    expect(screen.queryByText("Organization dashboard")).not.toBeInTheDocument();
    // And the stale keys are cleared, so the next load starts clean.
    expect(localStorage.getItem("user")).toBeNull();
    expect(localStorage.getItem("isAuthenticated")).toBeNull();
    expect(localStorage.getItem("role")).toBeNull();
  });

  it("clears a token the server rejects", async () => {
    localStorage.setItem("token", "stale.jwt.value");
    localStorage.setItem("user", JSON.stringify(ORG_ADMIN));
    api.get.mockRejectedValueOnce(unauthorized());

    renderApp("/organization/dashboard");

    expect(await screen.findByText("Sign in")).toBeInTheDocument();
    // The server WAS asked — the redirect is its answer, not a guess.
    expect(api.get).toHaveBeenCalledWith("/auth/me");
    await waitFor(() => expect(localStorage.getItem("token")).toBeNull());
    expect(localStorage.getItem("user")).toBeNull();
  });

  it("treats an expired session exactly like no session", async () => {
    localStorage.setItem("token", "expired.jwt.value");
    api.get.mockRejectedValueOnce(unauthorized());

    renderApp("/organization/dashboard");

    expect(await screen.findByText("Sign in")).toBeInTheDocument();
    expect(localStorage.getItem("token")).toBeNull();
  });
});

describe("a validated session", () => {
  it("renders the dashboard once the server confirms the user", async () => {
    localStorage.setItem("token", "good.jwt.value");
    api.get.mockResolvedValueOnce({ data: ORG_ADMIN });

    renderApp("/organization/dashboard");

    expect(await screen.findByText("Organization dashboard")).toBeInTheDocument();
    expect(api.get).toHaveBeenCalledWith("/auth/me");
  });

  it("survives a refresh, because the token is revalidated rather than trusted", async () => {
    localStorage.setItem("token", "good.jwt.value");
    api.get.mockResolvedValue({ data: ORG_ADMIN });

    const first = renderApp("/organization/dashboard");
    expect(await screen.findByText("Organization dashboard")).toBeInTheDocument();
    first.unmount();

    // Same browser, fresh page load.
    renderApp("/organization/dashboard");
    expect(await screen.findByText("Organization dashboard")).toBeInTheDocument();
    expect(api.get).toHaveBeenCalledTimes(2);
  });

  it("sends / to the role's own home, not a hardcoded organization dashboard", async () => {
    localStorage.setItem("token", "good.jwt.value");
    api.get.mockResolvedValueOnce({ data: SUPER_ADMIN });

    renderApp("/");

    expect(await screen.findByText("Admin console")).toBeInTheDocument();
    expect(screen.queryByText("Organization dashboard")).not.toBeInTheDocument();
  });

  it("keeps a role-gated console away from the wrong role", async () => {
    localStorage.setItem("token", "good.jwt.value");
    api.get.mockResolvedValueOnce({ data: ORG_ADMIN });

    renderApp("/admin/dashboard");

    // Bounced to their OWN home rather than shown the admin console.
    expect(await screen.findByText("Organization dashboard")).toBeInTheDocument();
    expect(screen.queryByText("Admin console")).not.toBeInTheDocument();
  });
});

describe("nothing protected renders before validation completes", () => {
  it("shows neither the dashboard nor the login screen while /auth/me is in flight", async () => {
    localStorage.setItem("token", "good.jwt.value");
    let resolve;
    api.get.mockReturnValueOnce(new Promise((r) => { resolve = r; }));

    renderApp("/organization/dashboard");

    // The window in which the old build flashed the dashboard.
    expect(screen.queryByText("Organization dashboard")).not.toBeInTheDocument();
    // ...and it must not flash the login screen at an authenticated user either.
    expect(screen.queryByText("Sign in")).not.toBeInTheDocument();

    resolve({ data: ORG_ADMIN });
    expect(await screen.findByText("Organization dashboard")).toBeInTheDocument();
  });
});

describe("logout", () => {
  it("clears the session, redirects to /login, and closes the dashboard", async () => {
    localStorage.setItem("token", "good.jwt.value");
    api.get.mockResolvedValueOnce({ data: ORG_ADMIN });

    renderApp("/account");
    await waitFor(() =>
      expect(screen.getByTestId("who")).toHaveTextContent(ORG_ADMIN.email));

    await userEvent.click(screen.getByRole("button", { name: "Sign out" }));

    expect(await screen.findByText("Sign in")).toBeInTheDocument();
    expect(localStorage.getItem("token")).toBeNull();
    expect(localStorage.getItem("user")).toBeNull();
  });

  it("leaves the dashboard unreachable afterwards", async () => {
    localStorage.setItem("token", "good.jwt.value");
    api.get.mockResolvedValueOnce({ data: ORG_ADMIN });

    const view = renderApp("/account");
    await waitFor(() =>
      expect(screen.getByTestId("who")).toHaveTextContent(ORG_ADMIN.email));
    await userEvent.click(screen.getByRole("button", { name: "Sign out" }));
    await screen.findByText("Sign in");
    view.unmount();

    // Typing the URL in by hand after signing out.
    renderApp("/organization/dashboard");
    expect(await screen.findByText("Sign in")).toBeInTheDocument();
    expect(screen.queryByText("Organization dashboard")).not.toBeInTheDocument();
  });
});

describe("a session that dies mid-visit", () => {
  it("ends the session when any request comes back 401", async () => {
    localStorage.setItem("token", "good.jwt.value");
    api.get.mockResolvedValueOnce({ data: ORG_ADMIN });

    renderApp("/organization/dashboard");
    expect(await screen.findByText("Organization dashboard")).toBeInTheDocument();

    // api.js dispatches this from its response interceptor on a 401.
    localStorage.removeItem("token");
    act(() => {
      window.dispatchEvent(new Event("zoiko:auth-expired"));
    });

    expect(await screen.findByText("Sign in")).toBeInTheDocument();
    expect(screen.queryByText("Organization dashboard")).not.toBeInTheDocument();
  });
});
