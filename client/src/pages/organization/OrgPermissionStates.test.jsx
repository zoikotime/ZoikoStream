// Two admin-only surfaces, seen by a member who is not an admin.
//
// The Organization sidebar shows every item to every role — that was a deliberate product
// decision and is not revisited here. What follows from it is that a host or viewer can open
// Settings and Members, and both of those call endpoints gated by require_org_admin.
//
// Before this, each handled that badly in its own way:
//
//   Settings  loaded six payloads with Promise.all. Five returned 200 for a host; the sixth,
//             GET /organization/security, returned 403 — so the batch rejected and the entire
//             page became one red "org_admin access required", discarding General, Branding,
//             Notifications and Domain, all of which had arrived.
//
//   Members   fired two admin-only requests, collected two 403s, and rendered a generic
//             "Couldn't load members" error.
//
// On this deployment that is 322 hosts and 183 viewers out of 1128 accounts.
//
// These pin the replacement: a page that keeps what the reader may see and states plainly
// what they may not. None of it touches the backend gates — see the last describe block.
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../api", () => ({
  default: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
  errMsg: (e) => e?.response?.data?.detail ?? e?.message ?? "Something went wrong.",
}));

vi.mock("../../ui/Toast", () => ({
  notify: { error: vi.fn(), success: vi.fn(), alert: vi.fn() },
}));

let currentUser = { full_name: "Ada Admin", email: "ada@example.com", role: "org_admin" };
vi.mock("../../auth/AuthContext", () => ({
  useAuth: () => ({ user: currentUser, logout: vi.fn() }),
}));

import api from "../../api";
import { ThemeProvider } from "../../theme/ThemeContext";
import InviteMembers from "./InviteMembers";
import Settings from "./Settings";

const OK = {
  "/organization/profile": {
    name: "Northwind", slug: "northwind", website: "", support_email: "",
    industry: "", company_size: "", description: "Northwind Traders",
  },
  "/organization/security": {
    require_2fa: true, enforce_sso: false, min_password_length: 12,
    session_timeout: "8 hours", allowed_domains: "northwind.com",
  },
  "/organization/notifications": { event_scheduled: true, billing: false },
  "/organization/domain": { domain: "live.northwind.com", domain_verified: true },
  "/organization/branding": { primary_color: "violet", logo_url: "" },
  "/organization/notifications/catalog": null,
  "/organization/users": {
    items: [
      { id: "u1", full_name: "Ada Admin", email: "ada@example.com", role: "org_admin", is_active: true },
      { id: "u2", full_name: "Hal Host", email: "hal@example.com", role: "host", is_active: true },
    ],
  },
  "/organization/invitations": {
    items: [{ id: "i1", email: "new@example.com", role: "viewer", status: "pending" }],
  },
};

/** An axios-shaped rejection, which is what the interceptor hands the page. */
const httpError = (status, detail) =>
  Object.assign(new Error(detail), { response: { status, data: { detail } } });

/** Answer every GET from OK, except the paths named in `deny`. */
const serve = (deny = {}) => {
  vi.mocked(api.get).mockImplementation((url) => {
    if (deny[url]) return Promise.reject(deny[url]);
    return Promise.resolve({ data: OK[url] ?? {} });
  });
};

const FORBIDDEN = { detail: "org_admin access required" };
const denyAdminOnly = () => ({
  "/organization/security": httpError(403, FORBIDDEN.detail),
  "/organization/users": httpError(403, FORBIDDEN.detail),
  "/organization/invitations": httpError(403, FORBIDDEN.detail),
});

const renderAt = (ui, path = "/") =>
  render(
    <ThemeProvider>
      <MemoryRouter initialEntries={[path]}>{ui}</MemoryRouter>
    </ThemeProvider>
  );

beforeEach(() => {
  vi.clearAllMocks();
  currentUser = { full_name: "Ada Admin", email: "ada@example.com", role: "org_admin" };
  serve();
});

// ── Settings ───────────────────────────────────────────────────────────────────────────

describe("Settings as an org admin", () => {
  it("loads every section, Security included", async () => {
    renderAt(<Settings />, "/organization/settings?tab=security");

    expect(await screen.findByText(/security configuration/i)).toBeInTheDocument();
    expect(screen.getByText(/require two-factor authentication/i)).toBeInTheDocument();
    expect(screen.getByText(/enforce sso/i)).toBeInTheDocument();
    expect(screen.queryByText(/organization admin access required/i)).not.toBeInTheDocument();
  });
});

describe("Settings as a host", () => {
  beforeEach(() => {
    currentUser = { full_name: "Hal Host", email: "hal@example.com", role: "host" };
    serve(denyAdminOnly());
  });

  it("still renders the page instead of one page-wide error", async () => {
    renderAt(<Settings />, "/organization/settings");

    // The General panel's data — proof the five permitted payloads survived the 403.
    expect(await screen.findByDisplayValue("Northwind")).toBeInTheDocument();
    expect(screen.queryByText(/couldn't load settings/i)).not.toBeInTheDocument();
  });

  it("shows the General, Branding and Notifications data it is allowed to read", async () => {
    renderAt(<Settings />, "/organization/settings");

    expect(await screen.findByDisplayValue("Northwind")).toBeInTheDocument();
    expect(screen.getByDisplayValue("Northwind Traders")).toBeInTheDocument();
    // Tab strip intact, so the rest of the page is reachable rather than merely present.
    expect(screen.getByRole("button", { name: /branding/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /notifications/i })).toBeInTheDocument();
  });

  it("permission-gates the Security policy instead of faking or hiding it silently", async () => {
    renderAt(<Settings />, "/organization/settings?tab=security");

    expect(await screen.findByText(/organization admin access required/i)).toBeInTheDocument();
    // No fabricated policy: the CONTROLS are absent, not rendered showing defaults that
    // would read as this organization's real settings. Asserted on the inputs rather than on
    // their labels, because the permission note deliberately names the same policies in prose
    // — matching that text would pass whether or not the form was really gone.
    expect(screen.queryByText(/require two-factor authentication/i)).not.toBeInTheDocument();
    expect(screen.queryByPlaceholderText(/acme\.com/i)).not.toBeInTheDocument();
    expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
    expect(screen.queryByRole("switch")).not.toBeInTheDocument();
  });

  it("keeps the personal password change, which is not admin-gated", async () => {
    // PATCH /api/auth/password has no org_admin gate — changing your own password is every
    // member's business, and gating the whole tab would have taken it away from 505 accounts.
    renderAt(<Settings />, "/organization/settings?tab=security");
    expect(await screen.findByText(/account security/i)).toBeInTheDocument();
  });

  it("does not retry the request it already knows is refused", async () => {
    renderAt(<Settings />, "/organization/settings?tab=security");
    await screen.findByText(/organization admin access required/i);

    const securityCalls = vi.mocked(api.get).mock.calls.filter(
      ([url]) => url === "/organization/security"
    );
    expect(securityCalls).toHaveLength(1);
  });
});

describe("Settings distinguishes a refusal from a fault", () => {
  it("shows a real error state when a REQUIRED settings call fails", async () => {
    // /organization/profile is readable by every role, so a 500 on it is a genuine fault and
    // must not be smoothed into a partial page.
    currentUser = { full_name: "Hal Host", email: "hal@example.com", role: "host" };
    serve({
      ...denyAdminOnly(),
      "/organization/profile": httpError(500, "Internal Server Error"),
    });

    renderAt(<Settings />, "/organization/settings");
    expect(await screen.findByText(/couldn't load settings/i)).toBeInTheDocument();
  });

  it("does not blame the reader when the optional Security call faults rather than refuses", async () => {
    currentUser = { full_name: "Hal Host", email: "hal@example.com", role: "host" };
    serve({ "/organization/security": httpError(500, "Internal Server Error") });

    renderAt(<Settings />, "/organization/settings?tab=security");

    expect(await screen.findByText(/couldn't load the security policy/i)).toBeInTheDocument();
    // A 500 is not a permission problem, and saying so would send the reader to an admin
    // who cannot help them.
    expect(screen.queryByText(/organization admin access required/i)).not.toBeInTheDocument();
  });
});

// ── Members ────────────────────────────────────────────────────────────────────────────

describe("Members as an org admin", () => {
  it("loads members and invitations as before", async () => {
    renderAt(<InviteMembers />);

    expect(await screen.findByText("Hal Host")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /invite member/i })).toBeInTheDocument();
    expect(screen.queryByText(/organization admin access is required/i)).not.toBeInTheDocument();
  });
});

describe.each(["host", "viewer"])("Members as a %s", (role) => {
  beforeEach(() => {
    currentUser = { full_name: "Not An Admin", email: "n@example.com", role };
    serve(denyAdminOnly());
  });

  it("renders an intentional permission state", async () => {
    renderAt(<InviteMembers />);

    expect(await screen.findByText(/organization admin access is required to manage members/i))
      .toBeInTheDocument();
    expect(screen.getByText(/contact an organization admin/i)).toBeInTheDocument();
    expect(screen.queryByText(/couldn't load members/i)).not.toBeInTheDocument();
  });

  it("never claims the organization is empty", async () => {
    renderAt(<InviteMembers />);
    await screen.findByText(/organization admin access is required/i);

    // "No members found" would be a statement about the organization, and a false one.
    expect(screen.queryByText(/no members/i)).not.toBeInTheDocument();
    // No zeroed KPI tiles standing in for figures nobody read.
    expect(screen.queryByText(/pending invites/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/team members/i)).not.toBeInTheDocument();
  });

  it("offers no member-management controls", async () => {
    renderAt(<InviteMembers />);
    await screen.findByText(/organization admin access is required/i);

    expect(screen.queryByRole("button", { name: /invite member/i })).not.toBeInTheDocument();
    expect(screen.queryByPlaceholderText(/search/i)).not.toBeInTheDocument();
  });

  it("sends no request it already knows will be refused", async () => {
    renderAt(<InviteMembers />);
    await screen.findByText(/organization admin access is required/i);

    await waitFor(() => {
      const urls = vi.mocked(api.get).mock.calls.map(([u]) => u);
      expect(urls).not.toContain("/organization/users");
      expect(urls).not.toContain("/organization/invitations");
    });
  });
});

// ── the gate that actually protects the data ───────────────────────────────────────────

describe("the frontend predicate is presentation only", () => {
  it("mirrors the server ladder exactly, and admits nothing the server would not", async () => {
    // server/app/security.py: _ROLE_RANK + require_min_role("org_admin").
    const { isOrgAdmin } = await import("../../auth/orgRole");

    expect(isOrgAdmin({ role: "org_admin" })).toBe(true);
    expect(isOrgAdmin({ role: "super_admin" })).toBe(true);
    for (const role of ["viewer", "speaker", "host"]) {
      expect(isOrgAdmin({ role })).toBe(false);
    }
    // An unknown or absent role is least-privileged, never most.
    expect(isOrgAdmin({ role: "billing_admin" })).toBe(false);
    expect(isOrgAdmin({})).toBe(false);
    expect(isOrgAdmin(null)).toBe(false);
  });

  it("reads the role from the authenticated session, not from localStorage", async () => {
    // A role typed into devtools must not change what anyone can reach. The page takes its
    // role from AuthContext's `user` (populated by GET /auth/me); this asserts that writing
    // the key a browser's owner can edit does not unlock the admin view.
    currentUser = { full_name: "Not An Admin", email: "n@example.com", role: "viewer" };
    localStorage.setItem("role", "org_admin");
    localStorage.setItem("user", JSON.stringify({ role: "org_admin" }));
    serve(denyAdminOnly());

    renderAt(<InviteMembers />);

    expect(await screen.findByText(/organization admin access is required/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /invite member/i })).not.toBeInTheDocument();
    localStorage.clear();
  });
});
