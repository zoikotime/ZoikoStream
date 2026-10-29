import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../api", () => ({
  default: {
    get: vi.fn(),
    post: vi.fn(),
    delete: vi.fn(),
  },
  errMsg: (e) => e?.message ?? "Something went wrong.",
}));

vi.mock("../../auth/AuthContext", () => ({
  useAuth: () => ({
    user: { full_name: "Admin User", email: "admin@example.com", role: "org_admin" },
    logout: vi.fn(),
  }),
}));

vi.mock("../../ui/Toast", () => ({
  notify: { error: vi.fn(), success: vi.fn(), alert: vi.fn(), warning: vi.fn(), info: vi.fn() },
}));

import api from "../../api";
import { ThemeProvider } from "../../theme/ThemeContext";
import OrganizationLayout from "../../layouts/OrganizationLayout";
import Analytics from "./Analytics";

const CONSOLE_STATE = {
  organization: { name: "Acme Corp", plan: "Enterprise", status: "active" },
  badges: {},
  user: { name: "Admin User", email: "admin@example.com", role: "org_admin", role_label: "Admin" },
  health: { status: "ok", label: "Healthy" },
  features: {},
};

const SAMPLE_ANALYTICS = {
  range: "30d",
  timezone: "UTC",
  summary: {
    peak_viewers_summed: 120,
    watch_hours: 45.5,
    peak: 80,
    engagement: 75,
  },
  reports: [
    {
      id: "evt-1",
      event: "All Hands Meeting",
      date: "2026-10-15T12:00:00Z",
      viewers: 80,
      watch_hours: 30,
      total_watch_seconds: 108000,
      average_watch_seconds: 1350,
      engagement: 82,
      measurement_state: "measured",
    },
    {
      id: "evt-2",
      event: "Tech Talk",
      date: "2026-10-18T14:00:00Z",
      viewers: 40,
      watch_hours: 15.5,
      total_watch_seconds: 55800,
      average_watch_seconds: 1395,
      engagement: 68,
      measurement_state: "measured",
    },
  ],
  trends: {
    viewership: [{ label: "15 Oct", value: 80 }, { label: "18 Oct", value: 40 }],
    watch_time: [{ label: "15 Oct", value: 30 }, { label: "18 Oct", value: 15.5 }],
  },
  breakdowns_note: "Device and location breakdowns are not persisted historically.",
  session_summary: null,
};

const SAMPLE_QOE = {
  total: {
    sessions: 150,
    playback_starts: 142,
  },
  series: {
    bucket: "day",
  },
};

function renderApp(initialPath = "/organization/analytics") {
  return render(
    <ThemeProvider>
      <MemoryRouter initialEntries={[initialPath]}>
        <Routes>
          <Route element={<OrganizationLayout />}>
            <Route path="/organization/analytics" element={<Analytics />} />
            <Route path="/organization/manage" element={<div>Organization Hub</div>} />
            <Route path="/organization/events" element={<div>Events Portfolio</div>} />
          </Route>
        </Routes>
      </MemoryRouter>
    </ThemeProvider>
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  window.scrollTo = vi.fn();
  vi.mocked(api.get).mockImplementation((url) => {
    if (url === "/organization/console-state") {
      return Promise.resolve({ data: CONSOLE_STATE });
    }
    if (url === "/organization/analytics") {
      return Promise.resolve({ data: SAMPLE_ANALYTICS });
    }
    if (url === "/organization/playback/qoe") {
      return Promise.resolve({ data: SAMPLE_QOE });
    }
    return Promise.resolve({ data: [] });
  });
});

describe("Analytics Parent and Sub-Navigation", { timeout: 20000 }, () => {
  it("renders Analytics parent link and its child navigation options", async () => {
    renderApp("/organization/analytics");

    expect(await screen.findByRole("heading", { name: "Analytics" })).toBeInTheDocument();
    const nav = screen.getByRole("navigation", { name: "Organization" });

    const parentLink = within(nav).getByRole("link", { name: "Analytics" });
    expect(parentLink).toHaveAttribute("href", "/organization/analytics");

    // Children are expanded
    expect(within(nav).getByRole("link", { name: "Analytics overview" })).toBeInTheDocument();
    expect(within(nav).getByRole("link", { name: "Event performance" })).toBeInTheDocument();
    expect(within(nav).getByRole("link", { name: "Stream quality" })).toBeInTheDocument();
    expect(within(nav).getByRole("link", { name: "Data quality" })).toBeInTheDocument();

    // Overview is current
    expect(within(nav).getByRole("link", { name: "Analytics overview" })).toHaveAttribute("aria-current", "page");
  });

  it("navigates from another page to Analytics and shows Overview", async () => {
    renderApp("/organization/manage");
    expect(await screen.findByText("Organization Hub")).toBeInTheDocument();

    const nav = screen.getByRole("navigation", { name: "Organization" });
    const analyticsLink = within(nav).getByRole("link", { name: "Analytics" });

    await userEvent.click(analyticsLink);

    expect(await screen.findByRole("heading", { name: "Analytics" })).toBeInTheDocument();
    expect(within(nav).getByRole("link", { name: "Analytics overview" })).toHaveAttribute("aria-current", "page");
  });

  it("clicking disclosure chevron toggles submenu without navigating", async () => {
    renderApp("/organization/analytics");
    await screen.findByRole("heading", { name: "Analytics" });

    const nav = screen.getByRole("navigation", { name: "Organization" });
    const collapseButton = within(nav).getByRole("button", { name: "Collapse Analytics" });

    // Collapse submenu
    await userEvent.click(collapseButton);
    expect(within(nav).queryByRole("link", { name: "Event performance" })).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Analytics" })).toBeInTheDocument();

    // Expand submenu
    const expandButton = within(nav).getByRole("button", { name: "Expand Analytics" });
    await userEvent.click(expandButton);
    expect(within(nav).getByRole("link", { name: "Event performance" })).toBeInTheDocument();
  });

  it("clicking parent Analytics expands submenu if collapsed", async () => {
    renderApp("/organization/analytics");
    await screen.findByRole("heading", { name: "Analytics" });

    const nav = screen.getByRole("navigation", { name: "Organization" });
    const collapseButton = within(nav).getByRole("button", { name: "Collapse Analytics" });
    await userEvent.click(collapseButton);
    expect(within(nav).queryByRole("link", { name: "Analytics overview" })).not.toBeInTheDocument();

    // Clicking parent Analytics expands it
    const parentLink = within(nav).getByRole("link", { name: "Analytics" });
    await userEvent.click(parentLink);

    expect(within(nav).getByRole("link", { name: "Analytics overview" })).toBeInTheDocument();
  });

  it("clicking child subview link highlights that child, and clicking parent Analytics returns to Overview", async () => {
    renderApp("/organization/analytics");
    await screen.findByRole("heading", { name: "Analytics" });

    const nav = screen.getByRole("navigation", { name: "Organization" });
    const qualityLink = within(nav).getByRole("link", { name: "Stream quality" });

    // Navigate to Stream quality subview
    await userEvent.click(qualityLink);
    expect(qualityLink).toHaveAttribute("aria-current", "page");

    // Click parent Analytics item to return to Overview
    const parentLink = within(nav).getByRole("link", { name: "Analytics" });
    await userEvent.click(parentLink);

    await waitFor(() => {
      expect(within(nav).getByRole("link", { name: "Analytics overview" })).toHaveAttribute("aria-current", "page");
    });
    expect(qualityLink).not.toHaveAttribute("aria-current");
  });

  it("clicking Analytics while already on Overview resets filters and state to default", async () => {
    renderApp("/organization/analytics");
    await screen.findByRole("heading", { name: "Analytics" });

    // Change date range filter
    const rangeSelect = screen.getByLabelText("Date range");
    await userEvent.selectOptions(rangeSelect, "7d");
    expect(rangeSelect).toHaveValue("7d");

    // Switch metric to watch hours
    const metricButton = screen.getByRole("button", { name: "Watch time" });
    await userEvent.click(metricButton);

    const nav = screen.getByRole("navigation", { name: "Organization" });
    const parentLink = within(nav).getByRole("link", { name: "Analytics" });

    // Click parent Analytics link
    await userEvent.click(parentLink);

    // Range is reset to 30d
    await waitFor(() => {
      expect(screen.getByLabelText("Date range")).toHaveValue("30d");
    });

    // Metric is reset to Viewers
    expect(screen.getByRole("button", { name: "Viewers" })).toHaveAttribute("aria-pressed", "true");
    expect(window.scrollTo).toHaveBeenCalled();
  });

  it("clicking breadcrumb Analytics resets view state", async () => {
    renderApp("/organization/analytics");
    await screen.findByRole("heading", { name: "Analytics" });

    const rangeSelect = screen.getByLabelText("Date range");
    await userEvent.selectOptions(rangeSelect, "90d");
    expect(rangeSelect).toHaveValue("90d");

    const breadcrumbs = screen.getByRole("navigation", { name: "Breadcrumb" });
    const analyticsCrumb = within(breadcrumbs).getByRole("link", { name: "Analytics" });

    await userEvent.click(analyticsCrumb);

    await waitFor(() => {
      expect(screen.getByLabelText("Date range")).toHaveValue("30d");
    });
  });

  it("parent click while already on Overview does not cause duplicate API requests when resetting range", async () => {
    renderApp("/organization/analytics");
    await screen.findByRole("heading", { name: "Analytics" });

    // Initial load: 1 call to /organization/analytics
    const initialCalls = vi.mocked(api.get).mock.calls.filter((c) => c[0] === "/organization/analytics").length;
    expect(initialCalls).toBe(1);

    // Change date range filter to 7d
    const rangeSelect = screen.getByLabelText("Date range");
    await userEvent.selectOptions(rangeSelect, "7d");

    await waitFor(() => {
      expect(vi.mocked(api.get).mock.calls.filter((c) => c[0] === "/organization/analytics").length).toBe(2);
    });

    const nav = screen.getByRole("navigation", { name: "Organization" });
    const parentLink = within(nav).getByRole("link", { name: "Analytics" });

    // Click parent Analytics link to reset
    await userEvent.click(parentLink);

    await waitFor(() => {
      expect(screen.getByLabelText("Date range")).toHaveValue("30d");
    });

    // Exactly one more call was made (total 3), verifying no duplicate calls
    await waitFor(() => {
      const callsForAnalytics = vi.mocked(api.get).mock.calls.filter((c) => c[0] === "/organization/analytics");
      expect(callsForAnalytics.length).toBe(3);
    });
  });

  it("parent click while viewing #performance or #data-quality returns to Overview and clears hash", async () => {
    renderApp("/organization/analytics");
    await screen.findByRole("heading", { name: "Analytics" });

    const nav = screen.getByRole("navigation", { name: "Organization" });
    const perfLink = within(nav).getByRole("link", { name: "Event performance" });

    // Navigate to Event performance hash
    await userEvent.click(perfLink);
    expect(perfLink).toHaveAttribute("aria-current", "page");

    // Click parent Analytics item
    const parentLink = within(nav).getByRole("link", { name: "Analytics" });
    await userEvent.click(parentLink);

    await waitFor(() => {
      expect(within(nav).getByRole("link", { name: "Analytics overview" })).toHaveAttribute("aria-current", "page");
    });
    expect(perfLink).not.toHaveAttribute("aria-current");
  });

  it("modifier click (ctrlKey) does not trigger route reset", async () => {
    renderApp("/organization/analytics");
    await screen.findByRole("heading", { name: "Analytics" });

    const rangeSelect = screen.getByLabelText("Date range");
    await userEvent.selectOptions(rangeSelect, "7d");
    expect(rangeSelect).toHaveValue("7d");

    const nav = screen.getByRole("navigation", { name: "Organization" });
    const parentLink = within(nav).getByRole("link", { name: "Analytics" });

    // In JSDOM, fireEvent accurately dispatches MouseEvent with modifier keys
    const { fireEvent } = await import("@testing-library/react");
    fireEvent.click(parentLink, { ctrlKey: true });

    // Range remains 7d on current page because Ctrl+click opens a new tab
    expect(rangeSelect).toHaveValue("7d");
  });
});
