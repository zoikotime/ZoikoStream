// Identity & Access is the platform-administrator console, and its client must not
// re-open a scope the server closed.
//
// The scope itself lives in crud.admin.IDENTITY_ACCESS_ROLES and is pinned by
// server/test_identity_access_scope.py — that is deliberately where it is enforced, because
// a filter the browser applies is a filter anyone can skip. What these assert is the part
// that is genuinely the client's: the Role control offers only the two platform
// appointments, "All roles" sends no role parameter (so the server's scope applies rather
// than a client-chosen one), and the page states what it is showing.
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";

vi.mock("../../api", () => ({
  default: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
  errMsg: (e) => e?.message ?? "failed",
}));

import api from "../../api";
import { ThemeProvider } from "../../theme/ThemeContext";
import Users from "./Users";
import { ASSIGNABLE_PLATFORM_ROLES } from "./roleInfo";

const USERS = [
  { id: "u1", full_name: "Ada Platform", email: "ada@zoiko.com", role: "super_admin", is_active: true, organization_name: "ZoikoStream Platform", created_at: "2026-09-01T00:00:00Z" },
  { id: "u2", full_name: "Bo Tenant", email: "bo@northwind.com", role: "org_admin", is_active: true, organization_name: "Northwind", created_at: "2026-09-02T00:00:00Z" },
];

const serve = (over = {}) =>
  vi.mocked(api.get).mockImplementation((url, cfg) => {
    if (url === "/admin/users") {
      return Promise.resolve({ data: { items: USERS, total: USERS.length, page: cfg?.params?.page || 1, page_size: 50 } });
    }
    if (url === "/admin/users/summary") {
      return Promise.resolve({ data: { total: 2, active: 2, inactive: 0, super_admins: 1, ...over } });
    }
    if (url === "/admin/organizations") return Promise.resolve({ data: { items: [] } });
    return Promise.resolve({ data: {} });
  });

const show = () =>
  render(
    <ThemeProvider>
      <MemoryRouter>
        <Users />
      </MemoryRouter>
    </ThemeProvider>
  );

const lastListParams = () => {
  const calls = vi.mocked(api.get).mock.calls.filter(([url]) => url === "/admin/users");
  return calls[calls.length - 1]?.[1]?.params || {};
};

beforeEach(() => {
  vi.clearAllMocks();
  serve();
});

describe("the Role control offers only the platform appointments", () => {
  it("lists exactly All roles, Super Admin and Org Admin", async () => {
    show();
    const select = await screen.findByLabelText("Filter by role");
    const options = [...select.querySelectorAll("option")].map((o) => o.textContent);

    expect(options).toEqual(["All roles", "Super Admin", "Org Admin"]);
  });

  it("offers no option for a role granted inside an organization", async () => {
    show();
    const select = await screen.findByLabelText("Filter by role");
    const values = [...select.querySelectorAll("option")].map((o) => o.value);

    for (const role of ["host", "speaker", "viewer", "billing_admin", "moderator"]) {
      expect(values, role).not.toContain(role);
    }
  });

  it("keeps the dropdown and the assignable set as one list", async () => {
    show();
    const select = await screen.findByLabelText("Filter by role");
    const values = [...select.querySelectorAll("option")].map((o) => o.value).filter((v) => v !== "all");

    expect(values).toEqual(ASSIGNABLE_PLATFORM_ROLES);
  });
});

describe("what the client sends", () => {
  it("sends no role parameter for All roles, so the server's scope is what applies", async () => {
    // The client must not name a role here. Sending one would make the browser the authority
    // on scope, which is exactly the arrangement the server-side predicate replaced.
    show();
    await screen.findByText("Ada Platform");

    expect(lastListParams().role).toBeUndefined();
  });

  it("narrows to one role when that role is chosen", async () => {
    show();
    await screen.findByText("Ada Platform");

    await userEvent.selectOptions(await screen.findByLabelText("Filter by role"), "org_admin");

    await waitFor(() => expect(lastListParams().role).toBe("org_admin"));
  });

  it("does not filter rows client-side — it re-queries", async () => {
    show();
    await screen.findByText("Ada Platform");
    const before = vi.mocked(api.get).mock.calls.filter(([u]) => u === "/admin/users").length;

    await userEvent.selectOptions(await screen.findByLabelText("Filter by role"), "super_admin");

    await waitFor(() =>
      expect(
        vi.mocked(api.get).mock.calls.filter(([u]) => u === "/admin/users").length
      ).toBeGreaterThan(before)
    );
  });
});

describe("the page says what it is showing", () => {
  it("describes itself as the platform administrators, not every account", async () => {
    show();
    expect(await screen.findByText(/Super Admin and Org Admin accounts/i)).toBeInTheDocument();
    expect(screen.queryByText("Every account across every organization")).not.toBeInTheDocument();
  });

  it("renders the KPI row from the scoped summary rather than counting the page", async () => {
    // The cards used to read the whole users table while the table below them listed a
    // subset. They now read /admin/users/summary, which the server scopes the same way.
    serve({ total: 2, active: 2, inactive: 0, super_admins: 1 });
    show();

    await screen.findByText("Ada Platform");
    expect(vi.mocked(api.get).mock.calls.some(([u]) => u === "/admin/users/summary")).toBe(true);
  });
});
