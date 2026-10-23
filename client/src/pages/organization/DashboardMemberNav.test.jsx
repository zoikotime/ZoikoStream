// The organization home's links, for org admins and for everyone else.
//
// Reported from the app: tapping Support & Status (and the other links in the dashboard's
// closing line) "refreshed the page instead of navigating". It was no reload: every one of those
// pages sits behind RoleRoute allow={["org_admin"]} (App.jsx), so for any other member the link
// opened, the guard sent them to their own home — this same dashboard — and it looked like a
// refresh. The sidebar offered the same admin pages to the same members. Both now offer each
// member only what they can open; the guards themselves are unchanged.
//
// The REAL dashboard, guards and sidebar; the destination pages are stand-ins.
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

let role;
vi.mock("../../auth/AuthContext", () => ({
  useAuth: () => ({ user: { full_name: "Ada Member", email: "ada@example.com", role }, loading: false, unreachable: false, logout: vi.fn() }),
}));
vi.mock("../../components/organization/dashboard/useDashboardData", () => ({
  default: () => ({
    core: {
      data: { overview: { sessions: {}, attention: [] }, upcoming: { items: [], total: 0 }, completed: { total: 0 } },
      loading: false, error: null, reload: () => {},
    },
    analytics: { data: null, loading: false, error: null, reload: () => {} },
    range: "30d",
    setRange: () => {},
  }),
}));

import ProtectedRoute from "../../components/ProtectedRoute";
import RoleRoute from "../../components/RoleRoute";
import Sidebar from "../../components/Dashboard/Sidebar";
import OrganizationDashboard from "./Dashboard";

const DASHBOARD = "/organization/dashboard";
const LINKS = [
  ["Streaming Sessions", "/organization/sessions"],
  ["Usage & Entitlements", "/organization/billing"],
  ["Settings › Developer", "/organization/settings?tab=developer"],
  ["Support & Status", "/organization/support"],
];
const NON_ADMINS = ["viewer", "speaker", "host"];

function Where() {
  const { pathname, search } = useLocation();
  const navigate = useNavigate();
  return (
    <>
      <output data-testid="location">{pathname + search}</output>
      <button type="button" onClick={() => navigate(-1)}>Browser back</button>
    </>
  );
}

const page = (title) => <h1>{title}</h1>;
function openAt(path) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Where />
      <Routes>
        <Route element={<ProtectedRoute />}>
          <Route path={DASHBOARD} element={<OrganizationDashboard />} />
        </Route>
        <Route element={<RoleRoute allow={["org_admin"]} />}>
          <Route path="/organization/sessions" element={page("Streaming Sessions page")} />
          <Route path="/organization/billing" element={page("Billing page")} />
          <Route path="/organization/settings" element={page("Settings page")} />
          <Route path="/organization/support" element={page("Support & Status page")} />
        </Route>
      </Routes>
    </MemoryRouter>
  );
}
const location = () => screen.getByTestId("location").textContent;
const signpost = () => screen.queryByText(/looking for sessions, usage, credentials or platform status/i);

beforeEach(() => {
  role = "org_admin";
});

describe("the dashboard's closing links, for an org admin", () => {
  it.each(LINKS)("%s opens its page in-app, and Back returns to the dashboard", async (label, href) => {
    openAt(DASHBOARD);
    const link = within(signpost()).getByRole("link", { name: label });
    expect(link).toHaveAttribute("href", href);
    // false = the router called preventDefault: the browser did not start a document load.
    let notPrevented;
    act(() => { notPrevented = fireEvent.click(link); });
    expect(notPrevented).toBe(false);
    expect(location()).toBe(href);
    expect(signpost()).toBeNull();
    act(() => { fireEvent.click(screen.getByRole("button", { name: "Browser back" })); });
    expect(location()).toBe(DASHBOARD);
    expect(signpost()).not.toBeNull();
  });

  it("Support & Status opens from the keyboard", async () => {
    const user = userEvent.setup();
    openAt(DASHBOARD);
    within(signpost()).getByRole("link", { name: "Support & Status" }).focus();
    await user.keyboard("{Enter}");
    expect(screen.getByRole("heading", { name: "Support & Status page" })).toBeInTheDocument();
    expect(location()).toBe("/organization/support");
  });

  it("Support & Status opened directly (or refreshed) stays there", () => {
    openAt("/organization/support");
    expect(screen.getByRole("heading", { name: "Support & Status page" })).toBeInTheDocument();
    expect(location()).toBe("/organization/support");
  });
});

describe("for members below org admin", () => {
  it.each(NON_ADMINS)("a %s is not offered the closing line's admin pages, which would only bounce back", (r) => {
    role = r;
    openAt(DASHBOARD);
    expect(signpost()).toBeNull();
    for (const [label] of LINKS) {
      expect(screen.queryByRole("link", { name: label })).toBeNull();
    }
  });

  it.each(NON_ADMINS)("the guard is unchanged: a %s opening Support & Status directly lands on the dashboard", (r) => {
    role = r;
    openAt("/organization/support");
    expect(location()).toBe(DASHBOARD);
    expect(screen.queryByRole("heading", { name: "Support & Status page" })).toBeNull();
  });
});

describe("the sidebar", () => {
  const rail = () => {
    render(
      <MemoryRouter initialEntries={[DASHBOARD]}>
        <Sidebar open state={{ badges: {}, user: { name: "Ada Member", role_label: "Member" } }} />
      </MemoryRouter>
    );
    return screen.getAllByRole("link").map((a) => [a.textContent.trim(), a.getAttribute("href")])
      .filter(([, href]) => href?.startsWith("/organization/"));
  };

  it("lists every page for an org admin, Support & Status last", () => {
    const links = rail();
    expect(links.map(([, href]) => href)).toEqual([
      DASHBOARD, "/organization/events", "/organization/audience", "/organization/users",
      "/organization/recordings", "/organization/sessions", "/organization/profile",
      "/organization/analytics", "/organization/billing", "/organization/settings", "/organization/support",
    ]);
  });

  it.each(NON_ADMINS)("lists only what a %s can open", (r) => {
    role = r;
    expect(rail().map(([, href]) => href)).toEqual([DASHBOARD]);
  });
});
