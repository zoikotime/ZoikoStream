// Trust & evidence: the Security overview and Data residency cards on Support & Status.
//
// The bug: both cards linked to /organization/settings?tab=security, which is Account Security
// (the change-password form) and answers neither question. Each now opens its own detail page,
// Back returns to Support & Status without growing a loop in history, and Account Security
// itself is untouched. The content tests pin the other half of the brief: these pages say only
// what the platform actually does, and "Not available" where it records nothing.
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation, useNavigate } from "react-router-dom";
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
import appSource from "../../App.jsx?raw";
import { ThemeProvider } from "../../theme/ThemeContext";
import Settings from "./Settings";
import SupportStatus from "./SupportStatus";
import { DataResidency, SecurityOverview } from "./TrustEvidence";

const SUPPORT = "/organization/support";
const SECURITY = "/organization/support/security";
const RESIDENCY = "/organization/support/data-residency";

const SUBPROCESSORS = [
  { name: "Google Cloud Storage", service: "Object storage", processing_purpose: "Storing recordings, exports and generated documents", region: null, status: "active" },
  { name: "LiveKit", service: "Live streaming and media ingest", processing_purpose: "Carrying live audio and video for events", region: null, status: "active" },
];

const PAYLOADS = {
  "/organization/overview": {
    generated_at: "2026-09-22T11:30:00Z",
    organization: { name: "Northwind", region: "US East" },
    service_health: { status: "ok", label: "Healthy", cause: null },
    security_support: { maintenance_window: null },
    lifecycle: [{ stage: "platform", label: "Platform", status: "ok", in_use: true, availability: 100, detail: null, open_incidents: 0 }],
    sessions: { items: [] },
    entitlements: {},
    developer_ops: {},
  },
  "/privacy/subprocessors": SUBPROCESSORS,
  // What the real Settings page reads, for the Account Security regression test.
  "/organization/security": { require_2fa: false, enforce_sso: false, min_password_length: 10, session_timeout: "8 hours", allowed_domains: "" },
  "/organization/domain": { domain: null, domain_verified: false },
  "/organization/profile": { name: "Northwind", slug: "northwind" },
  "/organization/notifications": {},
  "/organization/branding": {},
  "/organization/notifications/catalog": null,
  "/organization/users": { items: [] },
  "/organization/invitations": { items: [] },
  "/organization/developer": { api_keys: [] },
};

let payloads;
beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  payloads = { ...PAYLOADS };
  vi.mocked(api.get).mockImplementation((url) =>
    url in payloads && payloads[url] instanceof Error
      ? Promise.reject(payloads[url])
      : Promise.resolve({ data: payloads[url] ?? {} })
  );
});

// Where the router is, plus a stand-in for the browser's Back button (MemoryRouter has none).
function Chrome() {
  const location = useLocation();
  const navigate = useNavigate();
  return (
    <>
      <output data-testid="location">{location.pathname + location.search}</output>
      <button type="button" onClick={() => navigate(-1)}>Browser back</button>
    </>
  );
}

function renderAt(entries) {
  return render(
    <ThemeProvider>
      <MemoryRouter initialEntries={Array.isArray(entries) ? entries : [entries]}>
        <Chrome />
        <Routes>
          <Route path={SUPPORT} element={<SupportStatus />} />
          <Route path={SECURITY} element={<SecurityOverview />} />
          <Route path={RESIDENCY} element={<DataResidency />} />
          {/* A stub, so "did it go to Settings?" is a visible, checkable outcome. */}
          <Route path="/organization/settings" element={<h1>Settings page</h1>} />
        </Routes>
      </MemoryRouter>
    </ThemeProvider>
  );
}

const location = () => screen.getByTestId("location").textContent;
const h1 = (name) => screen.findByRole("heading", { level: 1, name });
const supportLoaded = () => screen.findByRole("heading", { name: /trust & evidence/i });
const securityCard = () => screen.getByRole("link", { name: /^security overview/i });
const residencyCard = () => screen.getByRole("link", { name: /^data residency/i });
const backLink = () => screen.getByRole("link", { name: /back to support & status/i });
const fact = (label) => screen.getByText(label).closest("li");

describe("the Security overview card", () => {
  it("does not point at, or land on, Settings → Security", async () => {
    const user = userEvent.setup();
    renderAt(SUPPORT);
    await supportLoaded();
    expect(securityCard().getAttribute("href")).not.toMatch(/settings/);

    await user.click(securityCard());
    expect(location()).not.toMatch(/settings/);
    expect(screen.queryByRole("heading", { name: "Settings page" })).not.toBeInTheDocument();
    expect(screen.queryByText(/current password/i)).not.toBeInTheDocument();
  });

  it("opens the Security Overview page", async () => {
    const user = userEvent.setup();
    renderAt(SUPPORT);
    await supportLoaded();
    expect(securityCard()).toHaveAttribute("href", SECURITY);

    await user.click(securityCard());
    expect(await h1("Security Overview")).toBeInTheDocument();
    expect(location()).toBe(SECURITY);
  });
});

describe("the Data residency card", () => {
  it("does not point at, or land on, Settings → Security", async () => {
    const user = userEvent.setup();
    renderAt(SUPPORT);
    await supportLoaded();
    expect(residencyCard().getAttribute("href")).not.toMatch(/settings/);

    await user.click(residencyCard());
    expect(location()).not.toMatch(/settings/);
    expect(screen.queryByRole("heading", { name: "Settings page" })).not.toBeInTheDocument();
  });

  it("opens the Data Residency page", async () => {
    const user = userEvent.setup();
    renderAt(SUPPORT);
    await supportLoaded();
    expect(residencyCard()).toHaveAttribute("href", RESIDENCY);

    await user.click(residencyCard());
    expect(await h1("Data Residency")).toBeInTheDocument();
    expect(location()).toBe(RESIDENCY);
  });
});

describe("direct and refreshed routes", () => {
  it.each([
    [SECURITY, "Security Overview"],
    [RESIDENCY, "Data Residency"],
  ])("%s renders on its own, with no prior navigation", async (path, title) => {
    renderAt(path);
    expect(await h1(title)).toBeInTheDocument();
    expect(location()).toBe(path);
  });

  it.each([
    [SECURITY, "Security Overview"],
    [RESIDENCY, "Data Residency"],
  ])("%s renders again after a reload (a fresh mount at the same URL)", async (path, title) => {
    const first = renderAt(path);
    await h1(title);
    first.unmount();

    renderAt(path);
    expect(await h1(title)).toBeInTheDocument();
  });

  it("is registered in the real router, inside the organization admin group", () => {
    const support = appSource.indexOf(`path="${SUPPORT}" element={<SupportStatus />}`);
    const security = appSource.indexOf(`path="${SECURITY}" element={<SecurityOverview />}`);
    const residency = appSource.indexOf(`path="${RESIDENCY}" element={<DataResidency />}`);
    const adminGroup = appSource.indexOf(`<RoleRoute allow={["org_admin"]} />`);
    expect(support).toBeGreaterThan(adminGroup);
    // Directly after Support & Status, before that admin group closes.
    expect(security).toBeGreaterThan(support);
    expect(residency).toBeGreaterThan(security);
    expect(appSource.slice(support, residency)).not.toContain("</Route>");
  });
});

describe("going back", () => {
  it.each([
    ["Security overview", securityCard, "Security Overview"],
    ["Data residency", residencyCard, "Data Residency"],
  ])("the Back action on %s returns to Support & Status without a history loop", async (_n, card, title) => {
    const user = userEvent.setup();
    renderAt(SUPPORT);
    await supportLoaded();
    await user.click(card());
    await h1(title);

    await user.click(backLink());
    await supportLoaded();
    expect(location()).toBe(SUPPORT);

    // Back went back rather than pushing another copy of the support page: one more browser
    // Back has nowhere to go, so it cannot bounce to the detail page.
    await user.click(screen.getByRole("button", { name: "Browser back" }));
    expect(location()).toBe(SUPPORT);
    expect(screen.queryByRole("heading", { level: 1, name: title })).not.toBeInTheDocument();
  });

  it("the browser's Back returns to Support & Status", async () => {
    const user = userEvent.setup();
    renderAt(SUPPORT);
    await supportLoaded();
    await user.click(residencyCard());
    await h1("Data Residency");

    await user.click(screen.getByRole("button", { name: "Browser back" }));
    await supportLoaded();
    expect(location()).toBe(SUPPORT);
  });

  it("opened directly, Back navigates to Support & Status", async () => {
    const user = userEvent.setup();
    renderAt(SECURITY);
    await h1("Security Overview");
    expect(backLink()).toHaveAttribute("href", SUPPORT);

    await user.click(backLink());
    await supportLoaded();
    expect(location()).toBe(SUPPORT);
  });
});

describe("card interaction and accessibility", () => {
  it.each([
    ["Security overview", securityCard, "Security Overview"],
    ["Data residency", residencyCard, "Data Residency"],
  ])("%s opens from the keyboard with Enter", async (_n, card, title) => {
    const user = userEvent.setup();
    renderAt(SUPPORT);
    await supportLoaded();
    card().focus();
    expect(card()).toHaveFocus();

    await user.keyboard("{Enter}");
    expect(await h1(title)).toBeInTheDocument();
  });

  it("both cards are real links with a meaningful name, a focus ring and nothing nested", async () => {
    renderAt(SUPPORT);
    await supportLoaded();
    expect(securityCard()).toHaveAccessibleName("Security overview: Practices, certifications, and reporting.");
    expect(residencyCard()).toHaveAccessibleName("Data residency: Where data is processed and stored.");
    for (const card of [securityCard(), residencyCard()]) {
      expect(card.tagName).toBe("A");
      expect(card.className).toContain("cursor-pointer");
      expect(card.className).toMatch(/focus-visible:/);
      expect(card.querySelectorAll("a, button, input, [tabindex], [onclick]")).toHaveLength(0);
    }
  });

  it("the Back action is keyboard operable too", async () => {
    const user = userEvent.setup();
    renderAt(RESIDENCY);
    await h1("Data Residency");
    backLink().focus();
    await user.keyboard("{Enter}");
    await supportLoaded();
    expect(location()).toBe(SUPPORT);
  });
});

describe("Account Security is unchanged", () => {
  it("still serves the change-password form at /organization/settings?tab=security", async () => {
    render(
      <ThemeProvider>
        <MemoryRouter initialEntries={["/organization/settings?tab=security"]}>
          <Settings />
        </MemoryRouter>
      </ThemeProvider>
    );
    expect(await screen.findByText(/account security/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/^current password/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/^new password/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/^confirm new password/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /update password/i })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: /security overview|data residency/i })).not.toBeInTheDocument();
  });
});

describe("Support & Status around the cards", () => {
  it("keeps the other two trust cards inert and marked On request", async () => {
    renderAt(SUPPORT);
    await supportLoaded();
    expect(screen.queryByRole("link", { name: /compliance documents/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /report a vulnerability/i })).not.toBeInTheDocument();
    expect(screen.getAllByText(/on request/i)).toHaveLength(2);
  });

  it("no longer sends anything on the page to Settings → Security", async () => {
    renderAt(SUPPORT);
    await supportLoaded();
    const links = screen.getAllByRole("link").map((a) => a.getAttribute("href"));
    expect(links.filter((href) => href?.includes("settings"))).toEqual([]);
    expect(screen.getByRole("link", { name: "Trust Center" })).toHaveAttribute("href", "/trust");
  });
});

describe("Security Overview says only what is true", () => {
  it("states what is in place and marks what is not", async () => {
    renderAt(SECURITY);
    await h1("Security Overview");
    expect(within(fact("Password storage")).getByText("In place")).toBeInTheDocument();
    expect(within(fact("Role-based access")).getByText("In place")).toBeInTheDocument();
    expect(within(fact("Administrative audit trail")).getByText("In place")).toBeInTheDocument();
    // Not implemented in this codebase, so not claimed.
    expect(within(fact("Two-factor authentication (2FA)")).getByText("Not available")).toBeInTheDocument();
    expect(within(fact("Single sign-on (SAML)")).getByText("Not available")).toBeInTheDocument();
    expect(within(fact("Encryption at rest")).getByText("Not currently published")).toBeInTheDocument();
  });

  it("claims no certification or compliance regime", async () => {
    renderAt(SECURITY);
    await h1("Security Overview");
    expect(within(fact("Security certifications (e.g. SOC 2, ISO 27001)")).getByText("Not currently published"))
      .toBeInTheDocument();
    expect(screen.getAllByText(/SOC 2/)).toHaveLength(1);
    expect(screen.queryByText(/HIPAA|PCI|certified|compliant|end-to-end encrypt/i)).not.toBeInTheDocument();
  });

  it("covers every area of the brief", async () => {
    renderAt(SECURITY);
    await h1("Security Overview");
    for (const area of [
      "Authentication & account protection", "Multi-factor authentication & single sign-on",
      "Access control", "Sessions", "Audit logging", "Data protection & secure transport",
      "Certifications & compliance", "Incident response & security reporting", "Need more detail?",
    ]) {
      expect(screen.getByRole("heading", { name: area })).toBeInTheDocument();
    }
    expect(screen.getByRole("link", { name: /contact support/i })).toHaveAttribute("href", "/contact");
    expect(screen.getByRole("link", { name: /trust center/i })).toHaveAttribute("href", "/trust");
  });
});

describe("Data Residency says only what is recorded", () => {
  it("does not infer a location — every unrecorded one reads Not available", async () => {
    renderAt(RESIDENCY);
    await h1("Data Residency");
    for (const label of ["Application and database location", "Organization residency setting", "Recordings and files", "Backups"]) {
      expect(within(fact(label)).getByText("Not available")).toBeInTheDocument();
    }
    expect(within(fact("Live media processing")).getByText("Provider-selected")).toBeInTheDocument();
    // The organization's display region is not residency, and is not read or shown here.
    expect(screen.queryByText("US East")).not.toBeInTheDocument();
    await waitFor(() => expect(api.get).toHaveBeenCalledWith("/privacy/subprocessors"));
    expect(vi.mocked(api.get).mock.calls.map(([url]) => url)).toEqual(["/privacy/subprocessors"]);
  });

  it("separates control-plane data from streaming and media data", async () => {
    renderAt(RESIDENCY);
    await h1("Data Residency");
    expect(screen.getByText("Control-plane data")).toBeInTheDocument();
    expect(screen.getByText("Streaming and media data")).toBeInTheDocument();
  });

  it("lists the published subprocessors, with Not available where no region is recorded", async () => {
    renderAt(RESIDENCY);
    await h1("Data Residency");
    const table = await screen.findByRole("table");
    const gcs = within(table).getByText("Google Cloud Storage").closest("tr");
    expect(within(gcs).getByText("Not available")).toBeInTheDocument();
  });

  it("shows a recorded region when the list carries one", async () => {
    payloads["/privacy/subprocessors"] = [{ ...SUBPROCESSORS[1], region: "Global edge network" }];
    renderAt(RESIDENCY);
    await h1("Data Residency");
    const row = (await screen.findByText("LiveKit")).closest("tr");
    expect(within(row).getByText("Global edge network")).toBeInTheDocument();
  });

  it("says so when the list cannot be loaded, and can retry", async () => {
    const user = userEvent.setup();
    payloads["/privacy/subprocessors"] = new Error("boom");
    renderAt(RESIDENCY);
    expect(await screen.findByText(/couldn’t load the subprocessor list/i)).toBeInTheDocument();

    payloads["/privacy/subprocessors"] = SUBPROCESSORS;
    await user.click(screen.getByRole("button", { name: /try again/i }));
    expect(await screen.findByRole("table")).toBeInTheDocument();
  });
});

describe("themes", () => {
  it.each(["light", "dark"])("both pages render in %s mode", async (theme) => {
    localStorage.setItem("theme", theme);
    const first = renderAt(SECURITY);
    await h1("Security Overview");
    expect(document.documentElement.classList.contains("dark")).toBe(theme === "dark");
    first.unmount();

    renderAt(RESIDENCY);
    expect(await h1("Data Residency")).toBeInTheDocument();
    expect(document.documentElement.classList.contains("dark")).toBe(theme === "dark");
  });
});
