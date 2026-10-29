import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../../api", () => ({
  default: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
  errMsg: (e) => e?.message ?? "Error",
}));
vi.mock("../../../ui/Toast", () => ({ notify: { error: vi.fn(), success: vi.fn(), info: vi.fn() } }));
vi.mock("../../../auth/AuthContext", () => ({
  useAuth: () => ({ user: { id: "u1", role: "org_admin" }, logout: vi.fn() }),
}));

import api from "../../../api";
import { ThemeProvider } from "../../../theme/ThemeContext";
import EventWorkspaceLayout from "./EventWorkspaceLayout";
import EventPreparation from "./EventPreparation";
import EventOverview from "./EventOverview";
import EventAudienceAccess from "./EventAudienceAccess";

const NOW = new Date().toISOString();
const EVENT = {
  id: "e1",
  title: "Quarterly town hall",
  status: "scheduled",
  display_status: "scheduled",
  start_time: "2026-11-02T17:00:00Z",
  timezone: "Europe/London",
  visibility: "public",
  category: "All-Hands",
  recording_enabled: true,
  chat_enabled: true,
  qa_enabled: true,
};
const READINESS = {
  verdict: "blocked",
  readiness_state: "in_progress",
  evaluated_at: NOW,
  impact: "tier_1",
  gates: [
    { key: "title", label: "Title set", passed: true, required: true },
    { key: "schedule", label: "Schedule confirmed", passed: false, required: true },
    { key: "speakers", label: "Speakers assigned", passed: false, required: false },
  ],
};

function LocationDisplay() {
  const loc = useLocation();
  const nav = useNavigate();
  return (
    <div>
      <div data-testid="location">{loc.pathname + loc.search + loc.hash}</div>
      <button data-testid="btn-back" type="button" onClick={() => nav(-1)}>Back</button>
      <button data-testid="btn-forward" type="button" onClick={() => nav(1)}>Forward</button>
    </div>
  );
}

function renderWorkspace(initialEntries = ["/organization/events/e1/preparation"]) {
  return render(
    <ThemeProvider>
      <MemoryRouter initialEntries={initialEntries}>
        <LocationDisplay />
        <Routes>
          <Route path="/organization/events/:id" element={<EventWorkspaceLayout />}>
            <Route index element={<EventOverview />} />
            <Route path="preparation" element={<EventPreparation />} />
            <Route path="access" element={<EventAudienceAccess />} />
          </Route>
        </Routes>
      </MemoryRouter>
    </ThemeProvider>
  );
}

describe("Event preparation navigation", { timeout: 15000 }, () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.scrollTo = vi.fn();
    const handlers = {
      "/organization/console-state": () => ({
        organization: { name: "Northwind" },
        user: { role: "org_admin" },
        badges: {},
        health: { status: "ok" },
      }),
      "/events/e1": () => EVENT,
      "/organization/events/e1/readiness": () => READINESS,
      "/organization/events/e1/live-status": () => ({ inputs: [] }),
      "/events/e1/rehearsals": () => [],
      "/events/e1/hosts": () => [{ id: "h1", full_name: "Jane Host" }],
      "/events/e1/speakers": () => [],
      "/events/e1/planning": () => [],
      "/commercial/events/e1/quotes": () => [],
      "/commercial/events/e1/order": () => null,
      "/commercial/events/e1/capacity": () => ({}),
      "/commercial/events/e1/readiness": () => ({ ready: true, blocking_reasons: [], required_checks: [] }),
      "/commercial/events/e1/incidents": () => [],
      "/commercial/events/e1/schedule": () => ({}),
      "/commercial/events/e1/payments": () => [],
      "/commercial/events/e1/invoices": () => [],
      "/commercial/events/e1/refunds": () => [],
      "/commercial/events/e1/lifecycle": () => ({}),
      "/commercial/events/e1/change-orders": () => [],
    };
    vi.mocked(api.get).mockImplementation((url) => {
      const h = handlers[url];
      if (!h) return Promise.reject(new Error("not found " + url));
      return Promise.resolve({ data: h() });
    });
  });

  it("1. Opening Readiness produces expected URL with #readiness and sets aria-current", async () => {
    renderWorkspace(["/organization/events/e1/preparation#readiness"]);
    const rail = await screen.findByRole("navigation", { name: "Event workspace" });

    expect(screen.getByTestId("location").textContent).toBe("/organization/events/e1/preparation#readiness");

    const readinessLink = within(rail).getByRole("link", { name: /Readiness Checks/i });
    expect(readinessLink).toHaveAttribute("aria-current", "page");

    const hubLink = within(rail).getByRole("link", { name: /Preparation hub/i });
    expect(hubLink).not.toHaveAttribute("aria-current");
  });

  it("2. Clicking Event team updates URL to #team and removes #readiness", async () => {
    const user = userEvent.setup();
    renderWorkspace(["/organization/events/e1/preparation#readiness"]);
    const rail = await screen.findByRole("navigation", { name: "Event workspace" });

    const teamLink = within(rail).getByRole("link", { name: /Event team/i });
    await user.click(teamLink);

    expect(screen.getByTestId("location").textContent).toBe("/organization/events/e1/preparation#team");
    expect(teamLink).toHaveAttribute("aria-current", "page");

    const readinessLink = within(rail).getByRole("link", { name: /Readiness Checks/i });
    expect(readinessLink).not.toHaveAttribute("aria-current");
  });

  it("3. Clicking Details & Schedule updates URL to #details and removes previous hash", async () => {
    const user = userEvent.setup();
    renderWorkspace(["/organization/events/e1/preparation#readiness"]);
    const rail = await screen.findByRole("navigation", { name: "Event workspace" });

    const detailsLink = within(rail).getByRole("link", { name: /Details & Schedule/i });
    await user.click(detailsLink);

    expect(screen.getByTestId("location").textContent).toBe("/organization/events/e1/preparation#details");
    expect(detailsLink).toHaveAttribute("aria-current", "page");
  });

  it("4. Switching between multiple Preparation views never leaves a stale hash", async () => {
    const user = userEvent.setup();
    renderWorkspace(["/organization/events/e1/preparation#readiness"]);
    const rail = await screen.findByRole("navigation", { name: "Event workspace" });

    const hubLink = within(rail).getByRole("link", { name: /Preparation hub/i });
    const detailsLink = within(rail).getByRole("link", { name: /Details & Schedule/i });
    const sourcesLink = within(rail).getByRole("link", { name: /Sources & Ingest/i });
    const readinessLink = within(rail).getByRole("link", { name: /Readiness Checks/i });
    const teamLink = within(rail).getByRole("link", { name: /Event team/i });

    // Click Hub -> removes hash
    await user.click(hubLink);
    expect(screen.getByTestId("location").textContent).toBe("/organization/events/e1/preparation");
    expect(hubLink).toHaveAttribute("aria-current", "page");

    // Click Details -> sets #details
    await user.click(detailsLink);
    expect(screen.getByTestId("location").textContent).toBe("/organization/events/e1/preparation#details");
    expect(detailsLink).toHaveAttribute("aria-current", "page");

    // Click Sources -> sets #sources
    await user.click(sourcesLink);
    expect(screen.getByTestId("location").textContent).toBe("/organization/events/e1/preparation#sources");
    expect(sourcesLink).toHaveAttribute("aria-current", "page");

    // Click Team -> sets #team
    await user.click(teamLink);
    expect(screen.getByTestId("location").textContent).toBe("/organization/events/e1/preparation#team");
    expect(teamLink).toHaveAttribute("aria-current", "page");

    // Click Readiness -> sets #readiness
    await user.click(readinessLink);
    expect(screen.getByTestId("location").textContent).toBe("/organization/events/e1/preparation#readiness");
    expect(readinessLink).toHaveAttribute("aria-current", "page");

    // Click Hub again -> cleanly removes hash
    await user.click(hubLink);
    expect(screen.getByTestId("location").textContent).toBe("/organization/events/e1/preparation");
    expect(hubLink).toHaveAttribute("aria-current", "page");
  });

  it("5. Parent RailLink navigates to preparation root and clears hash", async () => {
    const user = userEvent.setup();
    renderWorkspace(["/organization/events/e1/preparation#readiness"]);
    const rail = await screen.findByRole("navigation", { name: "Event workspace" });

    const prepHeaderLink = within(rail).getByRole("link", {
      name: (n) => n.startsWith("Preparation") && !n.includes("hub"),
    });
    expect(prepHeaderLink).toHaveAttribute("aria-current", "page");

    // Clicking Preparation parent navigates to preparation root and removes hash
    await user.click(prepHeaderLink);
    expect(screen.getByTestId("location").textContent).toBe("/organization/events/e1/preparation");
  });

  it("6. Browser Back and Forward restore expected URL and active rail item", async () => {
    const user = userEvent.setup();
    renderWorkspace(["/organization/events/e1/preparation#readiness"]);
    const rail = await screen.findByRole("navigation", { name: "Event workspace" });

    const teamLink = within(rail).getByRole("link", { name: /Event team/i });
    const readinessLink = within(rail).getByRole("link", { name: /Readiness Checks/i });

    await user.click(teamLink);
    expect(screen.getByTestId("location").textContent).toBe("/organization/events/e1/preparation#team");
    expect(teamLink).toHaveAttribute("aria-current", "page");

    // Back to readiness
    await user.click(screen.getByTestId("btn-back"));
    expect(screen.getByTestId("location").textContent).toBe("/organization/events/e1/preparation#readiness");
    expect(readinessLink).toHaveAttribute("aria-current", "page");

    // Forward to team
    await user.click(screen.getByTestId("btn-forward"));
    expect(screen.getByTestId("location").textContent).toBe("/organization/events/e1/preparation#team");
    expect(teamLink).toHaveAttribute("aria-current", "page");
  });

  it("7. Direct navigation to supported routes (#details, #sources, #team) highlights correct sub-item", async () => {
    renderWorkspace(["/organization/events/e1/preparation#sources"]);
    const rail = await screen.findByRole("navigation", { name: "Event workspace" });

    const sourcesLink = within(rail).getByRole("link", { name: /Sources & Ingest/i });
    expect(sourcesLink).toHaveAttribute("aria-current", "page");
  });

  it("8. Query parameters are preserved across rail navigation", async () => {
    const user = userEvent.setup();
    renderWorkspace(["/organization/events/e1/preparation?campaign=fall2026&scope=all#readiness"]);
    const rail = await screen.findByRole("navigation", { name: "Event workspace" });

    const teamLink = within(rail).getByRole("link", { name: /Event team/i });
    await user.click(teamLink);

    expect(screen.getByTestId("location").textContent).toBe(
      "/organization/events/e1/preparation?campaign=fall2026&scope=all#team"
    );

    const hubLink = within(rail).getByRole("link", { name: /Preparation hub/i });
    await user.click(hubLink);

    expect(screen.getByTestId("location").textContent).toBe(
      "/organization/events/e1/preparation?campaign=fall2026&scope=all"
    );
  });

  it("9. Existing Readiness checklist functionality remains unchanged", async () => {
    renderWorkspace(["/organization/events/e1/preparation#readiness"]);
    const heading = await screen.findByText(/Go-live readiness checklist/i);
    expect(heading).toBeInTheDocument();
    const section = heading.closest("section");
    expect(within(section).getByText(/1 of 3 verified/i)).toBeInTheDocument();
    expect(within(section).getByText(/Title set/i)).toBeInTheDocument();
    expect(within(section).getByText(/Schedule confirmed/i)).toBeInTheDocument();
  });

  it("10. In-page action buttons and panel links route correctly without leaving stale hashes", async () => {
    const user = userEvent.setup();
    renderWorkspace(["/organization/events/e1/preparation#team"]);
    const main = await screen.findByRole("main");

    // Header "Review readiness" button
    const reviewBtn = within(main).getByRole("link", { name: /Review readiness/i });
    await user.click(reviewBtn);
    expect(screen.getByTestId("location").textContent).toBe("/organization/events/e1/preparation#readiness");

    // Header "Event details" button
    const detailsBtn = within(main).getByRole("link", { name: /Event details/i });
    await user.click(detailsBtn);
    expect(screen.getByTestId("location").textContent).toBe("/organization/events/e1/preparation#details");
  });

  it("11. Switching to another workspace section clears hash and preserves search params", async () => {
    const user = userEvent.setup();
    renderWorkspace(["/organization/events/e1/preparation?env=prod#readiness"]);
    const rail = await screen.findByRole("navigation", { name: "Event workspace" });

    const accessSectionLink = within(rail).getByRole("link", { name: /^Audience Access/i });
    await user.click(accessSectionLink);

    expect(screen.getByTestId("location").textContent).toBe("/organization/events/e1/access?env=prod");
  });
});
