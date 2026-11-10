import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { ThemeProvider } from "../../../theme/ThemeContext";

vi.mock("../../../api", () => ({
  default: {
    get: vi.fn(),
    post: vi.fn(),
    patch: vi.fn(),
    delete: vi.fn(),
  },
  errMsg: (e) => e?.message ?? "Error",
}));

vi.mock("../../../ui/Toast", () => ({
  notify: { error: vi.fn(), success: vi.fn(), info: vi.fn() },
}));

vi.mock("../../../auth/AuthContext", () => ({
  useAuth: () => ({ user: { id: "u1", role: "org_admin" }, logout: vi.fn() }),
}));

import api from "../../../api";
import EventWorkspaceLayout from "./EventWorkspaceLayout";
import EventOverview from "./EventOverview";
import EventPreparation from "./EventPreparation";
import EventAudienceAccess from "./EventAudienceAccess";
import EventLiveControl from "./EventLiveControl";
import EventReplay from "./EventReplay";
import EventInsights from "./EventInsights";
import EventActivity from "./EventActivity";
import { resolveTargetElement, scrollToSection, HASH_ALIASES } from "./workspace";

const NOW = "2026-10-30T10:00:00Z";
const EVENT = {
  id: "e1",
  title: "Annual Global Broadcast",
  status: "scheduled",
  display_status: "scheduled",
  start_time: "2026-11-05T14:00:00Z",
  end_time: "2026-11-05T16:00:00Z",
  timezone: "UTC",
  visibility: "public",
  registration_required: true,
  category: "Keynote",
  recording_enabled: true,
  chat_enabled: true,
  qa_enabled: true,
};

const READINESS = {
  verdict: "passed",
  readiness_state: "ready",
  evaluated_at: NOW,
  impact: "tier_1",
  gates: [
    { key: "title", label: "Title set", passed: true, required: true },
    { key: "schedule", label: "Schedule confirmed", passed: true, required: true },
  ],
};

const LEDGER = () => ({
  items: [
    {
      id: "a1", at: NOW, action: "event.update", title: "Event details edited", category: "production",
      category_label: "Production changes", outcome: "applied", outcome_label: "Applied", changed_state: true,
      high_impact: false, actor: { id: "u1", name: "Ada Admin" }, details: [{ label: "Changed", value: "Title" }], reason: null,
      reference: "req_0123456789abcdef01234567",
    },
    {
      id: "a2", action: "event.end", title: "End event — rejected", outcome: "rejected", outcome_label: "Rejected",
      changed_state: false, reason: "The event was not live.", details: [], category: "production",
    },
  ],
  total: 2, page: 1, page_size: 25,
  scope: { kind: "event", event_id: "e1", title: EVENT.title, status: "scheduled", timezone: EVENT.timezone },
  summary: {
    total: 2, high_impact: 0, refused: 1, failed: 0, pending: 0,
    by_category: { access: 0, production: 2, evidence: 0, engagement: 0, commercial: 0, other: 0 },
    by_outcome: { applied: 1, rejected: 1 }, last_recorded_at: NOW,
  },
  actors: [{ value: "u1", label: "Ada Admin", count: 2 }],
  categories: [
    { value: "access", label: "Access & security" },
    { value: "production", label: "Production changes" },
    { value: "evidence", label: "Evidence" },
    { value: "engagement", label: "Audience interaction" },
    { value: "commercial", label: "Billing & commercial" },
    { value: "other", label: "Other" },
  ],
  integrity: {
    tenant_scope: { state: "explicit", label: "Explicit in each record" },
    hash_verification: { state: "not_established", label: "Not established" },
    immutability: { state: "not_asserted", label: "Not asserted" },
    time_anchoring: { state: "not_verified", label: "Not verified" },
  },
  export: { available: false, state: "not_offered", label: "Not offered", message: "" },
});

function LocationDisplay() {
  const loc = useLocation();
  const nav = useNavigate();
  return (
    <div>
      <div data-testid="route-location">{loc.pathname + loc.search + loc.hash}</div>
      <button data-testid="btn-back" type="button" onClick={() => nav(-1)}>Back</button>
      <button data-testid="btn-forward" type="button" onClick={() => nav(1)}>Forward</button>
    </div>
  );
}

function renderWorkspace(initialEntries = ["/organization/events/e1"]) {
  return render(
    <ThemeProvider>
      <MemoryRouter initialEntries={initialEntries}>
        <LocationDisplay />
        <Routes>
          <Route path="/organization/events/:id" element={<EventWorkspaceLayout />}>
            <Route index element={<EventOverview />} />
            <Route path="preparation" element={<EventPreparation />} />
            <Route path="access" element={<EventAudienceAccess />} />
            <Route path="live" element={<EventLiveControl />} />
            <Route path="replay" element={<EventReplay />} />
            <Route path="insights" element={<EventInsights />} />
            <Route path="activity" element={<EventActivity />} />
          </Route>
        </Routes>
      </MemoryRouter>
    </ThemeProvider>
  );
}

describe("Event Workspace Sidebar Sub-heading Navigation (All 6 Sections)", () => {
  let scrollToSpy;
  let windowScrollToSpy;

  beforeEach(() => {
    vi.clearAllMocks();

    scrollToSpy = vi.fn(function ({ top }) {
      this.scrollTop = top;
    });
    Object.defineProperty(HTMLElement.prototype, "scrollTo", {
      configurable: true,
      value: scrollToSpy,
    });

    windowScrollToSpy = vi.fn(function ({ top }) {
      window.pageYOffset = top;
    });
    window.scrollTo = windowScrollToSpy;

    api.get.mockImplementation((url) => {
      if (url === "/organization/console-state") {
        return Promise.resolve({
          data: {
            organization: { name: "Northwind" },
            user: { role: "org_admin" },
            badges: {},
            health: { status: "ok", label: "Healthy" },
          },
        });
      }
      if (url === "/events/e1") return Promise.resolve({ data: EVENT });
      if (url === "/organization/events/e1/readiness") return Promise.resolve({ data: READINESS });
      if (url === "/organization/events/e1/live-status" || url === "/organization/events/e1/live-state") {
        return Promise.resolve({
          data: {
            state: "idle",
            session_count: 0,
            sessions: [],
            incidents: [],
            runs: [],
            inputs: [],
          },
        });
      }
      if (url === "/events/e1/planning") return Promise.resolve({ data: [] });
      if (url === "/events/e1/rehearsals") return Promise.resolve({ data: [] });
      if (url === "/events/e1/hosts") return Promise.resolve({ data: [{ id: "h1", full_name: "Jane Host" }] });
      if (url === "/events/e1/speakers") return Promise.resolve({ data: [] });

      if (url === "/commercial/events/e1/quotes") return Promise.resolve({ data: [] });
      if (url === "/commercial/events/e1/order") return Promise.resolve({ data: null });
      if (url === "/commercial/events/e1/capacity") return Promise.resolve({ data: {} });
      if (url === "/commercial/events/e1/readiness") return Promise.resolve({ data: { ready: true, blocking_reasons: [], required_checks: [] } });
      if (url === "/commercial/events/e1/incidents") return Promise.resolve({ data: [] });
      if (url === "/commercial/events/e1/schedule") return Promise.resolve({ data: {} });
      if (url === "/commercial/events/e1/payments") return Promise.resolve({ data: [] });
      if (url === "/commercial/events/e1/invoices") return Promise.resolve({ data: [] });
      if (url === "/commercial/events/e1/refunds") return Promise.resolve({ data: [] });
      if (url === "/commercial/events/e1/lifecycle") return Promise.resolve({ data: {} });
      if (url === "/commercial/events/e1/change-orders") return Promise.resolve({ data: [] });

      if (url.includes("/links") || url.includes("/registrations")) return Promise.resolve({ data: [] });
      if (url.includes("/recordings")) return Promise.resolve({ data: [] });
      if (url.includes("/replay-entitlements")) return Promise.resolve({ data: [] });

      if (url === "/organization/events/e1/insights") {
        return Promise.resolve({
          data: {
            as_of: NOW,
            measured: true,
            total_sessions: 10,
            live_presence_timeline: [],
            viewers: 10,
            audience: { peak_viewers: 10, unique_viewers: 10 },
            broadcast: { on_air_seconds: 120 },
            sessions: { playback_starts: 10, failed_sessions: 0 },
            live: { rebuffer_ratio: 0.01, startup_ms_median: 200 },
            replay: { playback_starts: 5 },
            outcomes: { viewers: 10, watched_10_min: 8, reached_half: 7, reached_ninety: 6 },
          },
        });
      }
      if (url.includes("/feedback")) return Promise.resolve({ data: [] });
      if (url.includes("/activity")) {
        return Promise.resolve({
          data: LEDGER(),
        });
      }
      return Promise.resolve({ data: [] });
    });
  });

  afterEach(() => {
    delete HTMLElement.prototype.scrollTo;
  });

  describe("1. Target resolution and alias handling", () => {
    it("resolves canonical IDs and aliases correctly", () => {
      expect(HASH_ALIASES["invitations"]).toBe("registrations");
      expect(HASH_ALIASES["links"]).toBe("access-links");
      expect(HASH_ALIASES["live-monitor"]).toBe("monitor");
      expect(HASH_ALIASES["stream-health"]).toBe("sessions");
      expect(HASH_ALIASES["details-schedule"]).toBe("details");
      expect(HASH_ALIASES["sources-ingest"]).toBe("sources");
      expect(HASH_ALIASES["readiness-checks"]).toBe("readiness");
      expect(HASH_ALIASES["event-team"]).toBe("team");
      expect(HASH_ALIASES["assets"]).toBe("recordings");
      expect(HASH_ALIASES["preservation"]).toBe("retention");
      expect(HASH_ALIASES["viewer-activity"]).toBe("viewers");
      expect(HASH_ALIASES["stream-quality"]).toBe("quality");
      expect(HASH_ALIASES["outcomes-viewing"]).toBe("outcomes");
    });

    it("resolves DOM elements by ID, canonical ID, and data-section attributes", () => {
      const div = document.createElement("div");
      div.id = "registrations";
      div.setAttribute("data-section", "registrations");
      document.body.appendChild(div);

      expect(resolveTargetElement("registrations")).toBe(div);
      expect(resolveTargetElement("invitations")).toBe(div);

      document.body.removeChild(div);
    });

    it("scrollToSection('top') scrolls both container and window smoothly to 0", () => {
      const main = document.createElement("main");
      document.body.appendChild(main);

      const success = scrollToSection("top");
      expect(success).toBe(true);
      expect(windowScrollToSpy).toHaveBeenCalledWith({ top: 0, behavior: "smooth" });

      document.body.removeChild(main);
    });
  });

  describe("2. Preparation section sub-heading navigation", () => {
    it("renders and navigates all Preparation sub-items correctly", async () => {
      const user = userEvent.setup();
      renderWorkspace(["/organization/events/e1/preparation"]);

      const rail = await screen.findByRole("navigation", { name: "Event workspace" });

      // Click Details & Schedule -> #details
      const detailsLink = within(rail).getByRole("link", { name: /Details & Schedule/i });
      await user.click(detailsLink);
      expect(screen.getByTestId("route-location").textContent).toBe("/organization/events/e1/preparation#details");
      expect(detailsLink).toHaveAttribute("aria-current", "page");

      // Click Sources & Ingest -> #sources
      const sourcesLink = within(rail).getByRole("link", { name: /Sources & Ingest/i });
      await user.click(sourcesLink);
      expect(screen.getByTestId("route-location").textContent).toBe("/organization/events/e1/preparation#sources");
      expect(sourcesLink).toHaveAttribute("aria-current", "page");

      // Click Readiness Checks -> #readiness
      const readinessLink = within(rail).getByRole("link", { name: /Readiness Checks/i });
      await user.click(readinessLink);
      expect(screen.getByTestId("route-location").textContent).toBe("/organization/events/e1/preparation#readiness");
      expect(readinessLink).toHaveAttribute("aria-current", "page");

      // Click Event team -> #team
      const teamLink = within(rail).getByRole("link", { name: /Event team/i });
      await user.click(teamLink);
      expect(screen.getByTestId("route-location").textContent).toBe("/organization/events/e1/preparation#team");
      expect(teamLink).toHaveAttribute("aria-current", "page");

      // Click Preparation hub -> clears hash back to top
      const hubLink = within(rail).getByRole("link", { name: /Preparation hub/i });
      await user.click(hubLink);
      expect(screen.getByTestId("route-location").textContent).toBe("/organization/events/e1/preparation");
      expect(hubLink).toHaveAttribute("aria-current", "page");
    });
  });

  describe("3. Audience & Access section sub-heading navigation", () => {
    it("renders and navigates all Audience Access sub-items correctly", async () => {
      const user = userEvent.setup();
      renderWorkspace(["/organization/events/e1/access"]);

      const rail = await screen.findByRole("navigation", { name: "Event workspace" });

      // Viewer experience preview -> #viewer-preview
      const previewLink = within(rail).getByRole("link", { name: /Viewer experience preview/i });
      await user.click(previewLink);
      expect(screen.getByTestId("route-location").textContent).toBe("/organization/events/e1/access#viewer-preview");
      expect(previewLink).toHaveAttribute("aria-current", "page");

      // Access diagnostics -> #diagnostics
      const diagLink = within(rail).getByRole("link", { name: /Access diagnostics/i });
      await user.click(diagLink);
      expect(screen.getByTestId("route-location").textContent).toBe("/organization/events/e1/access#diagnostics");
      expect(diagLink).toHaveAttribute("aria-current", "page");

      // Invitations -> #registrations
      const invLink = within(rail).getByRole("link", { name: /Invitations/i });
      await user.click(invLink);
      expect(screen.getByTestId("route-location").textContent).toBe("/organization/events/e1/access#registrations");
      expect(invLink).toHaveAttribute("aria-current", "page");

      // Links & revocation -> #access-links
      const linksLink = within(rail).getByRole("link", { name: /Links & revocation/i });
      await user.click(linksLink);
      expect(screen.getByTestId("route-location").textContent).toBe("/organization/events/e1/access#access-links");
      expect(linksLink).toHaveAttribute("aria-current", "page");

      // Access overview -> clears hash
      const overviewLink = within(rail).getByRole("link", { name: /Access overview/i });
      await user.click(overviewLink);
      expect(screen.getByTestId("route-location").textContent).toBe("/organization/events/e1/access");
      expect(overviewLink).toHaveAttribute("aria-current", "page");
    });
  });

  describe("4. Replay & Assets section sub-heading navigation", () => {
    it("renders and navigates all Replay sub-items correctly", async () => {
      const user = userEvent.setup();
      renderWorkspace(["/organization/events/e1/replay"]);

      const rail = await screen.findByRole("navigation", { name: "Event workspace" });

      // Recordings & processing -> #recordings
      const recLink = within(rail).getByRole("link", { name: /Recordings & processing/i });
      await user.click(recLink);
      expect(screen.getByTestId("route-location").textContent).toBe("/organization/events/e1/replay#recordings");
      expect(recLink).toHaveAttribute("aria-current", "page");

      // Reports -> #reports
      const repLink = within(rail).getByRole("link", { name: /Reports/i });
      await user.click(repLink);
      expect(screen.getByTestId("route-location").textContent).toBe("/organization/events/e1/replay#reports");
      expect(repLink).toHaveAttribute("aria-current", "page");

      // Retention & export -> #retention
      const retLink = within(rail).getByRole("link", { name: /Retention & export/i });
      await user.click(retLink);
      expect(screen.getByTestId("route-location").textContent).toBe("/organization/events/e1/replay#retention");
      expect(retLink).toHaveAttribute("aria-current", "page");

      // Replay overview -> clears hash
      const ovLink = within(rail).getByRole("link", { name: /Replay overview/i });
      await user.click(ovLink);
      expect(screen.getByTestId("route-location").textContent).toBe("/organization/events/e1/replay");
      expect(ovLink).toHaveAttribute("aria-current", "page");
    });
  });

  describe("5. Insights section sub-heading navigation", () => {
    it("renders and navigates all Insights sub-items including feedback", async () => {
      const user = userEvent.setup();
      renderWorkspace(["/organization/events/e1/insights"]);

      const rail = await screen.findByRole("navigation", { name: "Event workspace" });

      // Viewer activity -> #viewers
      const viewersLink = within(rail).getByRole("link", { name: /Viewer activity/i });
      await user.click(viewersLink);
      expect(screen.getByTestId("route-location").textContent).toBe("/organization/events/e1/insights#viewers");
      expect(viewersLink).toHaveAttribute("aria-current", "page");

      // Stream quality -> #quality
      const qualityLink = within(rail).getByRole("link", { name: /Stream quality/i });
      await user.click(qualityLink);
      expect(screen.getByTestId("route-location").textContent).toBe("/organization/events/e1/insights#quality");
      expect(qualityLink).toHaveAttribute("aria-current", "page");

      // Event outcomes -> #outcomes
      const outcomesLink = within(rail).getByRole("link", { name: /Event outcomes/i });
      await user.click(outcomesLink);
      expect(screen.getByTestId("route-location").textContent).toBe("/organization/events/e1/insights#outcomes");
      expect(outcomesLink).toHaveAttribute("aria-current", "page");

      // Viewer feedback -> #feedback (verified not normalized away!)
      const feedbackLink = within(rail).getByRole("link", { name: /Viewer feedback/i });
      await user.click(feedbackLink);
      expect(screen.getByTestId("route-location").textContent).toBe("/organization/events/e1/insights#feedback");
      expect(feedbackLink).toHaveAttribute("aria-current", "page");
    });
  });

  describe("6. Activity & Audit section sub-heading navigation", () => {
    it("renders and navigates Activity timeline and queries", async () => {
      const user = userEvent.setup();
      renderWorkspace(["/organization/events/e1/activity"]);

      const rail = await screen.findByRole("navigation", { name: "Event workspace" });

      // Audit decisions -> ?outcome=refused
      const decLink = within(rail).getByRole("link", { name: /Audit decisions/i });
      await user.click(decLink);
      expect(screen.getByTestId("route-location").textContent).toBe("/organization/events/e1/activity?outcome=refused");
      expect(decLink).toHaveAttribute("aria-current", "page");

      // Evidence records -> ?category=evidence
      const evLink = within(rail).getByRole("link", { name: /Evidence records/i });
      await user.click(evLink);
      expect(screen.getByTestId("route-location").textContent).toBe("/organization/events/e1/activity?category=evidence");
      expect(evLink).toHaveAttribute("aria-current", "page");

      // Activity timeline -> /activity without query params
      const timeLink = within(rail).getByRole("link", { name: /Activity timeline/i });
      await user.click(timeLink);
      expect(screen.getByTestId("route-location").textContent).toBe("/organization/events/e1/activity");
      expect(timeLink).toHaveAttribute("aria-current", "page");
    });
  });
});