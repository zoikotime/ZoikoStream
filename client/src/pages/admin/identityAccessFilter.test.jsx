// The Role FILTER offers two roles, and hides nobody.
//
// It listed all six of User.ROLES while the Edit modal could only assign two, so half the
// filter pointed at roles this console has no say over. Narrowing it removes three ways to
// SLICE the list — never a way to SEE someone.
//
// That distinction is the whole test file. "All roles" sends no `role` param at all
// (useUsersData: `role === "all" ? undefined : role`), so the backend applies no role
// predicate and every Billing Admin, Host, Speaker and Viewer still comes back, still
// labelled with its real role in the Role column, still findable by search. A regression
// that made "All roles" mean "only the two visible options" would hide 505 accounts on this
// deployment, so it is asserted from two directions: the request that goes out, and the rows
// that come back.
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../api", () => ({
  default: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
  errMsg: (e) => e?.message ?? "Something went wrong.",
}));

vi.mock("react-hot-toast", () => ({
  default: { success: vi.fn(), error: vi.fn() },
}));

vi.mock("../../ui/Toast", () => ({
  notify: { error: vi.fn(), success: vi.fn(), alert: vi.fn() },
}));

import api from "../../api";
import { ThemeProvider } from "../../theme/ThemeContext";
import Users from "./Users";
import { ASSIGNABLE_PLATFORM_ROLES, ROLES } from "./roleInfo";

const REMOVED = ["Billing Admin", "Host", "Speaker", "Viewer"];

// One account per real role, mirroring what the live database actually holds.
const USERS = [
  { id: "1", full_name: "Sara Super", email: "sara@zoiko.com", username: "sara",
    organization_name: "Zoiko", role: "super_admin", is_active: true, created_at: "2026-01-05T00:00:00Z" },
  { id: "2", full_name: "Omar Orgadmin", email: "omar@zoiko.com", username: "omar",
    organization_name: "Zoiko", role: "org_admin", is_active: true, created_at: "2026-02-05T00:00:00Z" },
  { id: "3", full_name: "Hana Host", email: "hana@zoiko.com", username: "hana",
    organization_name: "Northwind", role: "host", is_active: true, created_at: "2026-03-05T00:00:00Z" },
  { id: "4", full_name: "Vik Viewer", email: "vik@zoiko.com", username: "vik",
    organization_name: "Northwind", role: "viewer", is_active: false, created_at: "2026-04-05T00:00:00Z" },
  { id: "5", full_name: "Sam Speaker", email: "sam@zoiko.com", username: "sam",
    organization_name: "Zoiko", role: "speaker", is_active: true, created_at: "2026-05-05T00:00:00Z" },
  { id: "6", full_name: "Bea Billing", email: "bea@zoiko.com", username: "bea",
    organization_name: "Zoiko", role: "billing_admin", is_active: true, created_at: "2026-06-05T00:00:00Z" },
];

const SUMMARY = { total: 6, active: 5, inactive: 1, super_admins: 1 };

/** Serve /admin/users honouring the `role` param exactly as the backend does. */
const serve = () => {
  vi.mocked(api.get).mockImplementation((url, cfg) => {
    if (url === "/admin/users/summary") return Promise.resolve({ data: SUMMARY });
    if (url === "/admin/users") {
      const role = cfg?.params?.role;
      const q = cfg?.params?.q;
      let items = USERS;
      if (role) items = items.filter((u) => u.role === role);
      if (q) {
        const n = q.toLowerCase();
        items = items.filter((u) =>
          [u.full_name, u.email, u.username].some((f) => f.toLowerCase().includes(n)));
      }
      return Promise.resolve({ data: { items, total: items.length } });
    }
    return Promise.resolve({ data: {} });
  });
};

const renderPage = () =>
  render(
    <ThemeProvider>
      <Users />
    </ThemeProvider>
  );

const roleFilter = () => screen.getByLabelText(/filter by role/i);
const filterOptions = () =>
  within(roleFilter()).getAllByRole("option").map((o) => o.textContent.trim());

/** The params of the most recent GET /admin/users. */
const lastListParams = () => {
  const calls = vi.mocked(api.get).mock.calls.filter(([u]) => u === "/admin/users");
  return calls[calls.length - 1]?.[1]?.params;
};

beforeEach(() => {
  vi.clearAllMocks();
  serve();
});

describe("what the Role filter offers", () => {
  it("contains exactly All roles, Super Admin and Org Admin", async () => {
    renderPage();
    await screen.findByText("Sara Super");
    expect(filterOptions()).toEqual(["All roles", "Super Admin", "Org Admin"]);
  });

  it.each(REMOVED)("does not offer %s", async (label) => {
    renderPage();
    await screen.findByText("Sara Super");
    expect(filterOptions()).not.toContain(label);
  });

  it("offers no option for any of the four removed role values", async () => {
    renderPage();
    await screen.findByText("Sara Super");
    const values = within(roleFilter()).getAllByRole("option").map((o) => o.value);
    for (const r of ["billing_admin", "host", "speaker", "viewer"]) {
      expect(values).not.toContain(r);
    }
  });
});

describe('"All roles" still means ALL', () => {
  it("sends no role param, so the backend applies no role predicate", async () => {
    renderPage();
    await screen.findByText("Sara Super");
    // The regression that would hide 505 accounts: sending role=super_admin,org_admin here.
    expect(lastListParams().role).toBeUndefined();
  });

  it("shows host, viewer, speaker and billing_admin accounts", async () => {
    renderPage();
    await screen.findByText("Sara Super");

    expect(screen.getByText("Hana Host")).toBeInTheDocument();
    expect(screen.getByText("Vik Viewer")).toBeInTheDocument();
    expect(screen.getByText("Sam Speaker")).toBeInTheDocument();
    expect(screen.getByText("Bea Billing")).toBeInTheDocument();
  });

  it("labels each account with its REAL role, not a narrowed one", async () => {
    renderPage();
    await screen.findByText("Sara Super");

    // The Role column must stay truthful even for roles the filter no longer lists.
    for (const label of ["Super Admin", "Org Admin", "Host", "Viewer", "Speaker", "Billing Admin"]) {
      expect(screen.getAllByText(label).length).toBeGreaterThan(0);
    }
  });
});

describe("the two remaining filters actually filter", () => {
  it("Super Admin narrows the request and the table to super_admin", async () => {
    const u = userEvent.setup();
    renderPage();
    await screen.findByText("Sara Super");

    await u.selectOptions(roleFilter(), "super_admin");

    await waitFor(() => expect(lastListParams().role).toBe("super_admin"));
    await waitFor(() => expect(screen.queryByText("Hana Host")).not.toBeInTheDocument());
    expect(screen.getByText("Sara Super")).toBeInTheDocument();
  });

  it("Org Admin narrows the request and the table to org_admin", async () => {
    const u = userEvent.setup();
    renderPage();
    await screen.findByText("Sara Super");

    await u.selectOptions(roleFilter(), "org_admin");

    await waitFor(() => expect(lastListParams().role).toBe("org_admin"));
    await waitFor(() => expect(screen.queryByText("Sara Super")).not.toBeInTheDocument());
    expect(screen.getByText("Omar Orgadmin")).toBeInTheDocument();
  });

  it("returning to All roles brings the legacy-role accounts back", async () => {
    const u = userEvent.setup();
    renderPage();
    await screen.findByText("Sara Super");

    await u.selectOptions(roleFilter(), "super_admin");
    await waitFor(() => expect(screen.queryByText("Hana Host")).not.toBeInTheDocument());

    await u.selectOptions(roleFilter(), "all");
    await waitFor(() => expect(screen.getByText("Hana Host")).toBeInTheDocument());
    expect(lastListParams().role).toBeUndefined();
  });
});

describe("search still reaches accounts the filter no longer lists", () => {
  it.each([
    ["hana", "Hana Host"],
    ["vik", "Vik Viewer"],
    ["sam", "Sam Speaker"],
    ["bea", "Bea Billing"],
  ])("finds %s by name", async (term, name) => {
    const u = userEvent.setup();
    renderPage();
    await screen.findByText("Sara Super");

    await u.type(screen.getByPlaceholderText(/search by name, email, or username/i), term);

    // Debounced 300ms, then a fresh server-side query.
    await waitFor(() => expect(lastListParams().q).toBe(term), { timeout: 3000 });
    await waitFor(() => expect(screen.getByText(name)).toBeInTheDocument());
  });

  it("searches server-side rather than over an already-fetched page", async () => {
    const u = userEvent.setup();
    renderPage();
    await screen.findByText("Sara Super");

    await u.type(screen.getByPlaceholderText(/search by name, email, or username/i), "hana");
    // A new request carrying q — not a client-side filter over the first 100 rows.
    await waitFor(() => expect(lastListParams().q).toBe("hana"), { timeout: 3000 });
  });
});

describe("nothing was removed from the platform's role vocabulary", () => {
  it("User.ROLES still carries all six", () => {
    expect(ROLES).toEqual(["super_admin", "org_admin", "billing_admin", "host", "speaker", "viewer"]);
  });

  it("the filter is driven by the assignable subset, which is the two platform roles", () => {
    expect(ASSIGNABLE_PLATFORM_ROLES).toEqual(["super_admin", "org_admin"]);
    for (const r of ["billing_admin", "host", "speaker", "viewer"]) {
      expect(ROLES).toContain(r);
      expect(ASSIGNABLE_PLATFORM_ROLES).not.toContain(r);
    }
  });

  it("renders the table without issuing a single write", async () => {
    renderPage();
    await screen.findByText("Sara Super");
    // Listing accounts must never PATCH or DELETE one.
    expect(api.patch).not.toHaveBeenCalled();
    expect(api.delete).not.toHaveBeenCalled();
  });
});
