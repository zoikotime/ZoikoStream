// The "Trust Center" link in the Support & Status footer.
//
// Production bug: it was <Link to="/organization/settings?tab=security">, so "Trust Center"
// opened Account Security — the change-password form. The real Trust Center is the public
// /trust page (pages/Trust.jsx), which already exists; the link now goes there. These pin the
// destination, the /trust route itself, and that nothing else in the footer moved.
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../api", () => ({
  default: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
  errMsg: (e) => e?.message ?? "Something went wrong.",
}));

import api from "../../api";
import appSource from "../../App.jsx?raw";
import { ThemeProvider } from "../../theme/ThemeContext";
import Trust from "../Trust";
import SupportStatus from "./SupportStatus";

const SETTINGS_SECURITY = "/organization/settings?tab=security";

const PAYLOADS = {
  "/organization/overview": {
    generated_at: "2026-09-22T11:30:00Z",
    organization: { region: "US East" },
    service_health: { status: "ok", label: "Healthy", cause: null },
    security_support: { maintenance_window: null },
    lifecycle: [{ stage: "platform", label: "Platform", status: "ok", in_use: true, availability: 100, detail: null, open_incidents: 0 }],
  },
  "/trust": {
    advisories: [], documents: [], purposes: [], scopes: [], vulnerability_categories: [],
    disclosure_policy: { bounty: false, public_credit: false, coordinated_disclosure_policy: false },
  },
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.get).mockImplementation((url) => Promise.resolve({ data: PAYLOADS[url] ?? {} }));
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

function renderAt(path) {
  return render(
    <ThemeProvider>
      <MemoryRouter initialEntries={[path]}>
        <Chrome />
        <Routes>
          <Route path="/organization/support" element={<SupportStatus />} />
          <Route path="/trust" element={<Trust />} />
          {/* Stand-in for Account Security, so landing there is a visible outcome. */}
          <Route path="/organization/settings" element={<h1>Account Security</h1>} />
        </Routes>
      </MemoryRouter>
    </ThemeProvider>
  );
}

const location = () => screen.getByTestId("location").textContent;
const supportLoaded = () => screen.findByRole("heading", { name: /trust & evidence/i });
const trustLoaded = () => screen.findByRole("heading", { level: 1, name: /security, transparently/i });
const footer = () => document.querySelector("footer");
const trustCenterLink = () => within(footer()).getByRole("link", { name: "Trust Center" });

describe("the Trust Center footer link", () => {
  it("is rendered in the Support & Status footer", async () => {
    renderAt("/organization/support");
    await supportLoaded();
    expect(trustCenterLink()).toBeInTheDocument();
  });

  it("points at /trust", async () => {
    renderAt("/organization/support");
    await supportLoaded();
    expect(trustCenterLink()).toHaveAttribute("href", "/trust");
  });

  it("regression: clicking it opens the Trust Center, not Account Security", async () => {
    // The production bug: this click landed on /organization/settings?tab=security.
    const user = userEvent.setup();
    renderAt("/organization/support");
    await supportLoaded();

    await user.click(trustCenterLink());

    expect(await trustLoaded()).toBeInTheDocument();
    expect(location()).toBe("/trust");
    expect(location()).not.toContain("/organization/settings");
    expect(screen.queryByRole("heading", { name: "Account Security" })).not.toBeInTheDocument();
  });

  it("opens from the keyboard and shows a focus ring", async () => {
    const user = userEvent.setup();
    renderAt("/organization/support");
    await supportLoaded();
    const link = trustCenterLink();
    expect(link.tagName).toBe("A");
    expect(link).not.toHaveAttribute("aria-disabled");
    expect(link.className).toMatch(/focus-visible:/);

    link.focus();
    expect(link).toHaveFocus();
    await user.keyboard("{Enter}");
    expect(await trustLoaded()).toBeInTheDocument();
    expect(location()).toBe("/trust");
  });

  it("browser Back from the Trust Center returns to Support & Status", async () => {
    const user = userEvent.setup();
    renderAt("/organization/support");
    await supportLoaded();
    await user.click(trustCenterLink());
    await trustLoaded();

    await user.click(screen.getByRole("button", { name: "Browser back" }));
    await supportLoaded();
    expect(location()).toBe("/organization/support");
  });
});

describe("the /trust route itself", () => {
  it("renders the public Trust Center when opened directly", async () => {
    renderAt("/trust");
    expect(await trustLoaded()).toBeInTheDocument();
    expect(screen.getByText("ZoikoStream Trust Center")).toBeInTheDocument();
    expect(location()).toBe("/trust");
    expect(api.get).toHaveBeenCalledWith("/trust");
  });

  it("renders again after a reload (a fresh mount at /trust) and stays on /trust", async () => {
    const first = renderAt("/trust");
    await trustLoaded();
    first.unmount();

    renderAt("/trust");
    expect(await trustLoaded()).toBeInTheDocument();
    expect(location()).toBe("/trust");
    expect(screen.queryByRole("heading", { name: "Account Security" })).not.toBeInTheDocument();
  });

  it("is one public route in the real router, with no redirect to Settings", () => {
    expect(appSource.match(/path="\/trust"/g)).toHaveLength(1);
    expect(appSource).toContain('<Route path="/trust" element={<Trust />} />');
    // Public: in the unauthenticated block that /contact and /status share, before /login.
    const publicBlock = appSource.slice(appSource.indexOf('path="/contact"'), appSource.indexOf('path="/login"'));
    expect(publicBlock).toContain('path="/trust"');
    expect(appSource).not.toMatch(/<Navigate[^>]*to="\/organization\/settings\?tab=security"/);
  });
});

describe("the rest of the footer and the trust cards are unchanged", () => {
  it("keeps Privacy and Terms as they were — plain text, not links", async () => {
    renderAt("/organization/support");
    await supportLoaded();
    for (const label of ["Privacy", "Terms"]) {
      expect(within(footer()).getByText(label)).toBeInTheDocument();
      expect(within(footer()).queryByRole("link", { name: label })).not.toBeInTheDocument();
    }
    expect(within(footer()).getByRole("link", { name: "Incident History" })).toHaveAttribute("href", "/organization/dashboard");
    expect(within(footer()).getByRole("link", { name: "Contact" })).toHaveAttribute("href", "/contact");
  });

  it("keeps Security overview and Data residency on their own pages", async () => {
    renderAt("/organization/support");
    await supportLoaded();
    expect(screen.getByRole("link", { name: /^security overview/i })).toHaveAttribute("href", "/organization/support/security");
    expect(screen.getByRole("link", { name: /^data residency/i })).toHaveAttribute("href", "/organization/support/data-residency");
  });

  it("leaves nothing on Support & Status pointing at Account Security", async () => {
    renderAt("/organization/support");
    await supportLoaded();
    const hrefs = screen.getAllByRole("link").map((a) => a.getAttribute("href"));
    expect(hrefs).not.toContain(SETTINGS_SECURITY);
  });
});

// Every "Trust Center" label in the app, checked at the source: the nearest link target before
// the label must not be Account Security. Legitimate Account Security links (labelled
// "Security" / "Security Settings") are untouched by this and are not matched.
const SOURCES = import.meta.glob(["../../**/*.{js,jsx}", "!../../**/*.test.{js,jsx}"], {
  query: "?raw",
  import: "default",
  eager: true,
});

function trustCenterTargets() {
  const found = [];
  for (const [file, src] of Object.entries(SOURCES)) {
    for (const m of src.matchAll(/Trust Center/g)) {
      const before = src.slice(Math.max(0, m.index - 300), m.index);
      const targets = [...before.matchAll(/\b(?:to|href)\s*[=:]\s*[{"'`]+([^"'`}\s]+)/g)].map((t) => t[1]);
      if (targets.length) found.push({ file, target: targets.at(-1) });
    }
  }
  return found;
}

describe("no Trust Center link anywhere points at Account Security", () => {
  it("checks every Trust Center-labelled link in the client", () => {
    const found = trustCenterTargets();
    // The scan really sees the links it is guarding, including the Support & Status footer.
    // (Glob keys are relative to this file, so Support & Status is "./SupportStatus.jsx".)
    expect(found.some(({ file, target }) => file === "./SupportStatus.jsx" && target === "/trust")).toBe(true);
    expect(found.length).toBeGreaterThanOrEqual(5); // also PrivacyPolicy, SecurityReport, PrivacyCenter, TrustEvidence
    for (const { file, target } of found) {
      expect(target, `${file} links "Trust Center" to ${target}`).not.toContain("settings");
    }
  });
});
