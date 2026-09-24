// The console consolidation, pinned as what it claims to be: a shorter rail, not a smaller
// console.
//
// Twenty flat rail entries is a list, not navigation. Three of them were merged into the
// page they were always a view of. The risk in that change is not layout — it is quietly
// losing a page, a route, or a count that told an operator something needed attention, so
// that is what these assert:
//
//   * every route the rail ever pointed at still resolves, including the three that left it;
//   * the three moved pages are reachable in the new place, as real tabs;
//   * a tab is linkable and survives a reload, because ?tab= is the source of truth;
//   * badges still render their counts, and still stay silent at zero;
//   * the active row is the current route's, and only that one.
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";

vi.mock("../../api", () => ({
  default: { get: vi.fn(() => Promise.resolve({ data: {} })), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
  errMsg: (e) => e?.message ?? "failed",
}));

vi.mock("../../auth/AuthContext", () => ({
  useAuth: () => ({ user: { full_name: "Ada Ops", role: "super_admin" } }),
}));

import AdminSidebar from "./AdminSidebar";
import ConsoleTabs from "../../pages/admin/ConsoleTabs";
import appSource from "../../App.jsx?raw";

const rail = (state) =>
  render(
    <MemoryRouter initialEntries={["/admin/dashboard"]}>
      <AdminSidebar open onClose={vi.fn()} state={state} unknown={false} onChange={vi.fn()} />
    </MemoryRouter>
  );

beforeEach(() => vi.clearAllMocks());

// ── nothing was lost ───────────────────────────────────────────────────────────────────

describe("the rail is shorter without being smaller", () => {
  it("still reaches every operating surface the console has", () => {
    rail({});
    // The full destination set, spelled out so deleting an item fails here loudly rather
    // than silently shrinking the console.
    for (const href of [
      "/admin/dashboard", "/admin/live-events", "/admin/event-readiness",
      "/admin/organizations", "/admin/users", "/admin/security", "/admin/audit",
      "/admin/media", "/admin/subscriptions", "/admin/commerce", "/admin/analytics",
      "/admin/support", "/admin/status", "/admin/settings",
      "/admin/governance", "/admin/feature-flags", "/admin/releases",
    ]) {
      expect(document.querySelector(`a[href="${href}"]`), href).toBeTruthy();
    }
  });

  it("groups them into four tiers instead of one flat list of twenty", () => {
    rail({});
    for (const label of ["Operate", "Govern", "Platform", "Advanced"]) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
    expect(document.querySelectorAll('aside a[href^="/admin/"]')).toHaveLength(17);
  });

  it("drops the three merged pages from the rail and nowhere else", () => {
    rail({});
    for (const href of ["/admin/roles", "/admin/developers", "/admin/infrastructure"]) {
      expect(document.querySelector(`aside a[href="${href}"]`), href).toBeNull();
    }
  });
});

// ── the badges still speak ─────────────────────────────────────────────────────────────

describe("attention counts survived the regrouping", () => {
  it("renders a count when there is something to attend to", () => {
    rail({ badges: { live_operations: 3, event_readiness: 1 } });
    const live = document.querySelector('a[href="/admin/live-events"]');
    expect(within(live).getByText("3")).toBeInTheDocument();
  });

  it("stays quiet at zero, so a quiet platform shows a quiet rail", () => {
    rail({ badges: { live_operations: 0, event_readiness: 0, system_status: 0 } });
    const live = document.querySelector('a[href="/admin/live-events"]');
    expect(within(live).queryByText("0")).toBeNull();
  });
});

// ── the active row ─────────────────────────────────────────────────────────────────────

describe("active highlighting follows the route", () => {
  it("marks the current page, and only it", () => {
    render(
      <MemoryRouter initialEntries={["/admin/media"]}>
        <AdminSidebar open onClose={vi.fn()} state={{}} unknown={false} onChange={vi.fn()} />
      </MemoryRouter>
    );
    const current = document.querySelectorAll('aside a[aria-current="page"]');
    expect(current).toHaveLength(1);
    expect(current[0]).toHaveAttribute("href", "/admin/media");
  });
});

// ── the merge primitive ────────────────────────────────────────────────────────────────

const Tabbed = () => (
  <ConsoleTabs
    label="Identity and access"
    idPrefix="identity"
    tabs={[
      { key: "users", label: "Users", render: () => <p>users body</p> },
      { key: "roles", label: "Roles & Capabilities", render: () => <p>roles body</p> },
    ]}
  />
);

const tabbedAt = (entry) =>
  render(
    <MemoryRouter initialEntries={[entry]}>
      <Routes>
        <Route path="/admin/users" element={<Tabbed />} />
      </Routes>
    </MemoryRouter>
  );

describe("a merged page is a real tab, not a link with extra steps", () => {
  it("shows the host page first", async () => {
    tabbedAt("/admin/users");
    expect(await screen.findByText("users body")).toBeInTheDocument();
    expect(screen.queryByText("roles body")).not.toBeInTheDocument();
  });

  it("switches to the moved page without leaving the route", async () => {
    tabbedAt("/admin/users");
    await screen.findByText("users body");

    await userEvent.click(screen.getByRole("tab", { name: /Roles/ }));

    expect(await screen.findByText("roles body")).toBeInTheDocument();
    expect(screen.queryByText("users body")).not.toBeInTheDocument();
  });

  it("is linkable — ?tab= opens straight onto the merged page", async () => {
    // The thing that makes state-only tabs feel broken: a colleague pastes a link and lands
    // on the wrong tab.
    tabbedAt("/admin/users?tab=roles");
    expect(await screen.findByText("roles body")).toBeInTheDocument();
  });

  it("falls back to the first tab on a ?tab= value that does not exist", async () => {
    tabbedAt("/admin/users?tab=nonsense");
    expect(await screen.findByText("users body")).toBeInTheDocument();
  });

  it("wires the tablist for a keyboard-only operator", async () => {
    tabbedAt("/admin/users");
    await screen.findByText("users body");

    const tabs = screen.getAllByRole("tab");
    expect(tabs[0]).toHaveAttribute("aria-selected", "true");
    tabs[0].focus();
    await userEvent.keyboard("{ArrowRight}");

    expect(await screen.findByText("roles body")).toBeInTheDocument();
  });
});

// ── the old URLs still resolve ─────────────────────────────────────────────────────────

describe("merging a page did not retire its route", () => {
  it("still registers the three moved pages, so a bookmark or runbook link lands", () => {
    // Read against the router source rather than mounting the whole app, which would drag in
    // auth, the providers and every lazy page. What must not regress is that the <Route>
    // still exists — deleting one is exactly what this catches.
    for (const path of ["/admin/roles", "/admin/developers", "/admin/infrastructure"]) {
      expect(appSource, path).toContain(`path="${path}"`);
    }
  });

  it("points the three host routes at their merged, tabbed version", () => {
    expect(appSource).toContain('path="/admin/users" element={<IdentityAccess />}');
    expect(appSource).toContain('path="/admin/organizations" element={<OrganizationsConsole />}');
    expect(appSource).toContain('path="/admin/status" element={<SystemStatusConsole />}');
  });
});
