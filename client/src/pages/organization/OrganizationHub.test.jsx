// Organization (ORG-06) and its Security & audit ledger.
//
//   * each hub card states one fact from its own source; a source that fails greys out its
//     own card, never the page
//   * the organization ledger reads the organization-wide endpoint and names the event each
//     row concerns
import { render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../api", () => ({
  default: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
  errMsg: (e) => e?.message ?? "Something went wrong.",
}));
vi.mock("../../ui/Toast", () => ({ notify: { error: vi.fn(), success: vi.fn() } }));

import api from "../../api";
import { ThemeProvider } from "../../theme/ThemeContext";
import { OrgScopeContext } from "../../components/organization/orgScope";
import OrganizationHub from "./OrganizationHub";
import SecurityAudit from "./SecurityAudit";

const SUMMARY = {
  total: 12, high_impact: 2, refused: 3, failed: 0, pending: 0,
  by_category: { access: 4, production: 6, evidence: 2, engagement: 0, commercial: 0, other: 0 },
  by_outcome: { applied: 9, denied: 3 }, last_recorded_at: new Date().toISOString(),
};
const LEDGER = {
  items: [{
    id: "a1", at: new Date().toISOString(), action: "api_key.rotate", title: "API key rotated",
    category: "access", category_label: "Access & security", outcome: "applied", outcome_label: "Applied",
    changed_state: true, high_impact: false, details: [], reason: null, reference: "req_x",
    actor: { kind: "member", id: "u1", name: "Dana Admin", role: "org_admin", role_label: "Organization Owner" },
    event: { id: "e1", title: "Investor day" },
  }],
  total: 1, page: 1, page_size: 25,
  scope: { kind: "organization" }, summary: SUMMARY,
  actors: [], categories: [{ value: "access", label: "Access & security" }],
  integrity: {}, integrity_note: "", export: { label: "Not offered", message: "" }, as_of: new Date().toISOString(),
};

let handlers;
beforeEach(() => {
  vi.clearAllMocks();
  handlers = {
    "/organization/users": () => ({ items: [], total: 7 }),
    "/organization/invitations": () => ({ items: [], total: 2 }),
    "/organization/team/access": () => ({ grants: 5, members: {} }),
    "/events": () => ({ items: [], total: 5 }),
    "/organization/branding": () => ({ logo_url: null }),
    "/organization/activity": () => LEDGER,
  };
  vi.mocked(api.get).mockImplementation((url) => {
    const h = handlers[url];
    if (!h) return Promise.reject(new Error(`unexpected ${url}`));
    const out = h();
    return out instanceof Promise ? out : Promise.resolve({ data: out });
  });
});

function renderWith(ui, state = { organization: { name: "Northwind", plan: "Growth", status: "active" }, features: { developers: false } }) {
  return render(
    <ThemeProvider>
      <MemoryRouter>
        <OrgScopeContext.Provider value={{ state, reload: () => {}, enableQuickActions: () => () => {} }}>
          {ui}
        </OrgScopeContext.Provider>
      </MemoryRouter>
    </ThemeProvider>
  );
}

// The area cards are the h3s inside "Manage your organization"; the side panels reuse some names.
const cardFor = (title) => screen.getByRole("heading", { name: title, level: 3 }).closest("a");
const tile = (name) => screen.getByRole("link", { name: new RegExp(`^${name}`) });

describe("the Organization hub", () => {
  it("states one real fact per card, linked to the page that owns it", async () => {
    renderWith(<OrganizationHub />);
    expect(await screen.findByText("7 members")).toBeInTheDocument();
    expect(await screen.findByText("12 recorded actions · 3 refused")).toBeInTheDocument();
    expect(cardFor("Team & roles")).toHaveAttribute("href", "/organization/users");
    expect(cardFor("Security & audit")).toHaveAttribute("href", "/organization/security");
    expect(within(cardFor("Plan, usage & billing")).getByText("Growth plan")).toBeInTheDocument();
    expect(within(cardFor("Integrations")).getByText("Not set up")).toBeInTheDocument();
  });

  it("counts from real sources and lists only the reviews those sources call for", async () => {
    renderWith(<OrganizationHub />);
    await waitFor(() =>
      expect(within(screen.getByRole("group", { name: "Assigned access grants" })).getByText("5")).toBeInTheDocument());
    expect(within(tile("Team members")).getByText("7")).toBeInTheDocument();
    expect(within(tile("Events in scope")).getByText("5")).toBeInTheDocument();
    const priorities = screen.getByRole("region", { name: "Governance priorities" });
    expect(await within(priorities).findByText("Follow up 2 pending invitations")).toBeInTheDocument();
    expect(within(priorities).getByText("Review 3 refused actions")).toBeInTheDocument();
    expect(within(priorities).getByText("Add your organization's logo")).toBeInTheDocument();
    expect(within(screen.getByRole("group", { name: "Items to review" })).getByText("4")).toBeInTheDocument();
  });

  it("greys out only the card whose source failed", async () => {
    handlers["/organization/users"] = () => Promise.reject(new Error("down"));
    renderWith(<OrganizationHub />);
    expect(await within(cardFor("Team & roles")).findByText("Unavailable right now")).toBeInTheDocument();
    expect(await screen.findByText("12 recorded actions · 3 refused")).toBeInTheDocument();
  });
});

describe("Security & audit", () => {
  it("reads the organization-wide ledger and names the event of each row", async () => {
    renderWith(<SecurityAudit />);
    expect(await screen.findByRole("button", { name: "API key rotated" })).toBeInTheDocument();
    expect(vi.mocked(api.get).mock.calls.some(([u]) => u === "/organization/activity")).toBe(true);
    const row = screen.getByRole("button", { name: "API key rotated" }).closest("tr");
    expect(within(row).getByText("Investor day")).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "Event" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /security settings/i })).toHaveAttribute("href", "/organization/settings?tab=security");
  });
});
