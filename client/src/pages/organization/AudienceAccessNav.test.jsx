import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Navigate, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../api", () => ({
  default: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
  errMsg: (e) => e?.message ?? "Error",
}));
vi.mock("../../auth/AuthContext", () => ({
  useAuth: () => ({ user: { id: "u1", role: "org_admin", full_name: "Admin" }, logout: vi.fn() }),
}));

import api from "../../api";
import { ThemeProvider } from "../../theme/ThemeContext";
import OrganizationLayout from "../../layouts/OrganizationLayout";
import AudienceAccess from "./AudienceAccess";
import PlaybackAccess from "./PlaybackAccess";

function LegacyPlaybackRedirect() {
  const location = useLocation();
  return (
    <Navigate
      to={{
        pathname: "/organization/audience/access",
        search: location.search,
        hash: location.hash,
      }}
      replace
    />
  );
}

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

const EVENTS = {
  total: 1,
  page: 1,
  page_size: 100,
  items: [
    {
      id: "e1",
      title: "Quarterly Review",
      start_time: "2026-11-01T12:00:00Z",
      visibility: "public",
      status: "scheduled",
      registration_required: false,
      password_protected: false,
      replay_enabled: true,
      waiting_room_enabled: false,
    },
  ],
};

const SUMMARY = { total_unique_viewers: 50, peak_concurrency: 25, total_watch_hours: 100, replay_watch_share: 0.2 };
const ATTENDANCE = { total: 50, buckets: [] };
const INSIGHTS = { devices: [], countries: [], total_sessions: 50 };
const QOE = { live: { sessions: 10, startup_ms_median: 1200 }, replay: {}, total: { sessions: 10, failed_sessions: 0 } };

function renderApp(initialEntries = ["/organization/audience"]) {
  return render(
    <ThemeProvider>
      <MemoryRouter initialEntries={initialEntries}>
        <LocationDisplay />
        <Routes>
          <Route element={<OrganizationLayout />}>
            <Route path="/organization/audience" element={<AudienceAccess />} />
            <Route path="/organization/audience/access" element={<PlaybackAccess />} />
            <Route path="/organization/playback" element={<LegacyPlaybackRedirect />} />
          </Route>
        </Routes>
      </MemoryRouter>
    </ThemeProvider>
  );
}

describe("Audience & Access navigation and canonical routes", { timeout: 15000 }, () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.scrollTo = vi.fn();
    const handlers = {
      "/organization/console-state": () => ({
        organization: { name: "Acme Corp" },
        user: { role: "org_admin" },
        badges: {},
        health: { status: "ok" },
      }),
      "/events": () => EVENTS,
      "/organization/audience-summary": () => SUMMARY,
      "/organization/audience-attendance": () => ATTENDANCE,
      "/organization/audience-insights": () => INSIGHTS,
      "/organization/playback/qoe": () => QOE,
      "/events/e1/access-links": () => [],
    };
    vi.mocked(api.get).mockImplementation((url) => {
      const h = handlers[url];
      if (!h) return Promise.resolve({ data: [] });
      return Promise.resolve({ data: h() });
    });
  });

  it("1. Clicking Audience & Access opens Audience overview at canonical /organization/audience", async () => {
    const user = userEvent.setup();
    renderApp(["/organization/audience/access"]);
    const rail = await screen.findByRole("navigation", { name: "Organization" });

    const audienceTopLink = within(rail).getByRole("link", { name: "Audience & Access" });
    await user.click(audienceTopLink);

    expect(screen.getByTestId("location").textContent).toBe("/organization/audience");
    expect(await screen.findByRole("heading", { level: 1, name: /audience & access/i })).toBeInTheDocument();

    const overviewChild = within(rail).getByRole("link", { name: "Audience overview" });
    expect(overviewChild).toHaveAttribute("aria-current", "page");
  });

  it("2. Clicking Event access opens Playback & Access page at canonical /organization/audience/access", async () => {
    const user = userEvent.setup();
    renderApp(["/organization/audience"]);
    const rail = await screen.findByRole("navigation", { name: "Organization" });

    const eventAccessLink = within(rail).getByRole("link", { name: "Event access" });
    await user.click(eventAccessLink);

    expect(screen.getByTestId("location").textContent).toBe("/organization/audience/access");
    expect(await screen.findByRole("heading", { level: 1, name: /playback & access/i })).toBeInTheDocument();
  });

  it("3. Highlights the correct parent and child sidebar items when on /organization/audience/access", async () => {
    renderApp(["/organization/audience/access"]);
    const rail = await screen.findByRole("navigation", { name: "Organization" });

    // In expanded rail, parent has active state while child has aria-current="page"
    const eventAccessLink = within(rail).getByRole("link", { name: "Event access" });
    expect(eventAccessLink).toHaveAttribute("aria-current", "page");

    const overviewLink = within(rail).getByRole("link", { name: "Audience overview" });
    expect(overviewLink).not.toHaveAttribute("aria-current");
  });

  it("4. Directly opening the canonical URL /organization/audience/access renders the page directly", async () => {
    renderApp(["/organization/audience/access"]);
    expect(screen.getByTestId("location").textContent).toBe("/organization/audience/access");
    expect(await screen.findByRole("heading", { level: 1, name: /playback & access/i })).toBeInTheDocument();
  });

  it("5. Legacy URL /organization/playback redirects to /organization/audience/access preserving query and hash", async () => {
    renderApp(["/organization/playback?window=7d#access-rules"]);
    // Should be redirected immediately to canonical route
    expect(screen.getByTestId("location").textContent).toBe("/organization/audience/access?window=7d#access-rules");
    expect(await screen.findByRole("heading", { level: 1, name: /playback & access/i })).toBeInTheDocument();
  });

  it("6. Browser Back and Forward restore expected page and navigation state without loop", async () => {
    const user = userEvent.setup();
    renderApp(["/organization/audience"]);
    const rail = await screen.findByRole("navigation", { name: "Organization" });

    const eventAccessLink = within(rail).getByRole("link", { name: "Event access" });
    await user.click(eventAccessLink);
    expect(screen.getByTestId("location").textContent).toBe("/organization/audience/access");

    // Click Back -> restores /organization/audience
    await user.click(screen.getByTestId("btn-back"));
    expect(screen.getByTestId("location").textContent).toBe("/organization/audience");
    const overviewLink = within(rail).getByRole("link", { name: "Audience overview" });
    expect(overviewLink).toHaveAttribute("aria-current", "page");

    // Click Forward -> restores /organization/audience/access
    await user.click(screen.getByTestId("btn-forward"));
    expect(screen.getByTestId("location").textContent).toBe("/organization/audience/access");
    expect(within(rail).getByRole("link", { name: "Event access" })).toHaveAttribute("aria-current", "page");
  });

  it("7. Breadcrumbs accurately reflect the route hierarchy on Event access page", async () => {
    renderApp(["/organization/audience/access"]);
    await screen.findByRole("heading", { level: 1, name: /playback & access/i });

    const breadcrumbsNav = screen.getByRole("navigation", { name: "Breadcrumb" });
    expect(within(breadcrumbsNav).getByRole("link", { name: "Organization" })).toHaveAttribute(
      "href",
      "/organization/manage"
    );
    expect(within(breadcrumbsNav).getByRole("link", { name: "Audience & Access" })).toHaveAttribute(
      "href",
      "/organization/audience"
    );
    expect(within(breadcrumbsNav).getByText("Event access")).toBeInTheDocument();
  });

  it("8. Breadcrumbs on Audience overview page reflect Audience & Access hierarchy", async () => {
    renderApp(["/organization/audience"]);
    await screen.findByRole("heading", { level: 1, name: /audience & access/i });

    const breadcrumbsNav = screen.getByRole("navigation", { name: "Breadcrumb" });
    expect(within(breadcrumbsNav).getByRole("link", { name: "Organization" })).toHaveAttribute(
      "href",
      "/organization/manage"
    );
    expect(within(breadcrumbsNav).getByRole("link", { name: "Audience & Access" })).toHaveAttribute(
      "href",
      "/organization/audience"
    );
    expect(within(breadcrumbsNav).getByText("Audience overview")).toBeInTheDocument();
  });
});
