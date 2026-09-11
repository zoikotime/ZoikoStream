// Security has one home: Settings -> Security.
//
// It used to be a 400px card on the Organization page, scored from controls that were edited
// on a different screen. These pin the move: gone from Organization, present in Settings,
// still driven by the same two payloads, and not duplicated across the two.
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../api", () => ({
  default: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
  errMsg: (e) => e?.message ?? "Something went wrong.",
}));

vi.mock("../../auth/AuthContext", () => ({
  useAuth: () => ({ user: { full_name: "Vihari Owner", email: "owner@example.com" }, logout: vi.fn() }),
}));

vi.mock("../../ui/Toast", () => ({
  notify: { error: vi.fn(), success: vi.fn(), alert: vi.fn() },
}));

import api from "../../api";
import { ThemeProvider } from "../../theme/ThemeContext";
import Profile from "./Profile";
import Settings from "./Settings";

// One control enabled out of six — the state in the screenshot, and the interesting one:
// a partial score must render from data, never from a constant.
// Field names are the ones derive.js actually reads: `session_timeout` is a LABEL keyed
// into TIMEOUT_HOURS (not a number), and `allowed_domains` is a string (fromApi trims it).
// Exactly one of the six controls passes here — the 8-hour session expiry — so the score
// has to come from the data rather than from a constant.
const SECURITY = {
  require_2fa: false,
  enforce_sso: false,
  min_password_length: 8,
  session_timeout: "8 hours",
  allowed_domains: "",
};
const DOMAIN = { domain: null, domain_verified: false };

const PAYLOADS = {
  "/organization/security": SECURITY,
  "/organization/domain": DOMAIN,
  "/organization/profile": { name: "Northwind", slug: "northwind" },
  "/organization/notifications": {},
  "/organization/branding": {},
  "/organization/notifications/catalog": null,
  "/organization/overview": {
    organization: { name: "Northwind" },
    sessions: { items: [] },
    entitlements: {},
    developer_ops: {},
  },
  "/organization/users": { items: [] },
  "/organization/invitations": { items: [] },
  "/organization/developer": { api_keys: [] },
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.get).mockImplementation((url) =>
    Promise.resolve({ data: PAYLOADS[url] ?? {} })
  );
});

const renderAt = (ui, path = "/") =>
  render(
    <ThemeProvider>
      <MemoryRouter initialEntries={[path]}>{ui}</MemoryRouter>
    </ThemeProvider>
  );

describe("the Organization page no longer owns Security", () => {
  it("renders no Security panel", async () => {
    renderAt(<Profile />);
    await screen.findByText(/recent activity/i);

    expect(screen.queryByText(/account credentials and organization posture/i)).not.toBeInTheDocument();
    // Including the one that used to sit in the page header — the action now has exactly
    // one home, alongside the posture it belongs with.
    expect(screen.queryByRole("button", { name: /change password/i })).not.toBeInTheDocument();
    expect(screen.queryByText(/adjust security settings/i)).not.toBeInTheDocument();
    // The checklist that made up the bulk of the panel.
    expect(screen.queryByText(/two-factor authentication required/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/single sign-on enforced/i)).not.toBeInTheDocument();
  });

  it("leaves no empty right-hand column where it used to sit", async () => {
    const { container } = renderAt(<Profile />);
    await screen.findByText(/recent activity/i);
    // The block was `lg:grid-cols-[minmax(0,1fr)_minmax(0,400px)]`; a two-track grid with
    // one child is exactly the empty column this move was meant to avoid.
    const rails = [...container.querySelectorAll("[class*='grid-cols-[']")].filter((el) =>
      /minmax\(0,400px\)/.test(el.className)
    );
    expect(rails).toEqual([]);
  });

  it("still points at the new home from the Security Score tile", async () => {
    renderAt(<Profile />);
    await screen.findByText(/recent activity/i);
    const tile = screen.getByText(/security score/i).closest("a");
    expect(tile).toHaveAttribute("href", "/organization/settings?tab=security");
  });
});

describe("Settings -> Security is the one home", () => {
  it("opens on ?tab=security via the existing deep link", async () => {
    renderAt(<Settings />, "/organization/settings?tab=security");
    expect(await screen.findByText(/account security/i)).toBeInTheDocument();
    expect(screen.getByText(/security configuration/i)).toBeInTheDocument();
  });

  it("renders the password row, now as a real inline form", async () => {
    renderAt(<Settings />, "/organization/settings?tab=security");
    await screen.findByText(/account security/i);
    expect(screen.getByText(/^password$/i)).toBeInTheDocument();
    // The control that used to walk a signed-in admin to /forgot-password is gone; the
    // change happens on this screen. Full behaviour lives in ChangePassword.test.jsx.
    expect(screen.queryByRole("button", { name: /change password/i })).not.toBeInTheDocument();
    expect(screen.getByLabelText(/current password/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /update password/i })).toBeInTheDocument();
  });

  it("renders no posture score, ring, checklist or point values", async () => {
    const { container } = renderAt(<Settings />, "/organization/settings?tab=security");
    await screen.findByText(/account security/i);

    // SECURITY above would score 15/100 ("At risk", 1 of 6) if anything still rendered it,
    // so every one of these is a live assertion rather than a vacuous one.
    expect(screen.queryByText(/security posture/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/at risk/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/15\/100/)).not.toBeInTheDocument();
    expect(screen.queryByText(/controls enabled/i)).not.toBeInTheDocument();
    // Checklist rows.
    expect(screen.queryByText(/two-factor authentication required/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/single sign-on enforced/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/organization domain verified/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/sessions expire within/i)).not.toBeInTheDocument();
    // Point values, and the SVG ring that drew the score. A bare "svg circle" would also
    // match the head of a lucide person icon, so this asks for what a progress arc actually
    // is: a circle carrying a dash array. Nothing else on the page draws one.
    expect(screen.queryByText(/^[+]?\d+\/\d+$/)).not.toBeInTheDocument();
    expect(container.querySelector("circle[stroke-dasharray]")).toBeNull();
  });

  it("keeps the editable controls, which are what the tab is now for", async () => {
    renderAt(<Settings />, "/organization/settings?tab=security");
    await screen.findByText(/security configuration/i);
    expect(screen.getByText(/require two-factor authentication/i)).toBeInTheDocument();
    expect(screen.getByText(/enforce sso/i)).toBeInTheDocument();
  });

  it("drops the in-page link that would point at the page you are on", async () => {
    renderAt(<Settings />, "/organization/settings?tab=security");
    await screen.findByText(/account security/i);
    expect(screen.queryByText(/adjust security settings/i)).not.toBeInTheDocument();
  });

  it("still fetches each payload exactly once", async () => {
    renderAt(<Settings />, "/organization/settings?tab=security");
    await screen.findByText(/account security/i);
    const urls = vi.mocked(api.get).mock.calls.map(([u]) => u);
    // Dropping the posture must not have cost either payload a consumer: /security still
    // drives the six controls below, and /domain still drives the Custom Domain panel on
    // the General tab. Neither is fetched twice either.
    expect(urls.filter((u) => u === "/organization/security")).toHaveLength(1);
    expect(urls.filter((u) => u === "/organization/domain")).toHaveLength(1);
  });
});
