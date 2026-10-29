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
import Recordings from "./Recordings";

const CONSOLE_STATE = {
  organization: { name: "Acme Corp", plan: "Enterprise", status: "active" },
  badges: {},
  user: { name: "Admin User", email: "admin@example.com", role: "org_admin", role_label: "Admin" },
  health: { status: "ok", label: "Healthy" },
  features: {},
};

const SAMPLE_RECORDINGS = [
  {
    id: "rec-1",
    event_id: "evt-1",
    title: "Global All-Hands",
    category: "Internal",
    started_at: "2026-10-20T14:00:00Z",
    duration_seconds: 3600,
    size_bytes: 1024 * 1024 * 500,
    url: "https://signed.example/rec-1.mp4",
    state: "ready",
    legal_hold: false,
    validation_status: "valid",
  },
  {
    id: "rec-2",
    event_id: "evt-2",
    title: "Product Keynote",
    category: "Public",
    started_at: "2026-10-22T10:00:00Z",
    duration_seconds: 1800,
    size_bytes: null,
    url: null,
    state: "processing",
    legal_hold: false,
    validation_status: null,
  },
];

const SUMMARY = {
  library_total: 2,
  in_progress: 0,
  failed_attempts: 0,
  latest_failure: null,
};

function renderApp(initialPath = "/organization/recordings") {
  return render(
    <ThemeProvider>
      <MemoryRouter initialEntries={[initialPath]}>
        <Routes>
          <Route element={<OrganizationLayout />}>
            <Route path="/organization/recordings" element={<Recordings />} />
            <Route path="/organization/manage" element={<div>Organization Hub</div>} />
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
    if (url === "/organization/recordings") {
      return Promise.resolve({ data: SAMPLE_RECORDINGS });
    }
    if (url === "/organization/recordings/summary") {
      return Promise.resolve({ data: SUMMARY });
    }
    return Promise.resolve({ data: [] });
  });
});

describe("Media & Recordings Parent and Child Navigation", { timeout: 20000 }, () => {
  it("renders Media & Recordings parent link and child options", async () => {
    renderApp("/organization/recordings");

    expect(await screen.findByRole("heading", { name: "Media & recordings" })).toBeInTheDocument();
    const nav = screen.getByRole("navigation", { name: "Organization" });

    const parentLink = within(nav).getByRole("link", { name: "Media & Recordings" });
    expect(parentLink).toHaveAttribute("href", "/organization/recordings");

    // Children are expanded
    expect(within(nav).getByRole("link", { name: "All recordings" })).toBeInTheDocument();
    expect(within(nav).getByRole("link", { name: "Replay-ready" })).toBeInTheDocument();
    expect(within(nav).getByRole("link", { name: "Processing status" })).toBeInTheDocument();

    // All recordings is current
    expect(within(nav).getByRole("link", { name: "All recordings" })).toHaveAttribute("aria-current", "page");
  });

  it("clicking disclosure chevron toggles submenu without navigating away", async () => {
    renderApp("/organization/recordings");
    await screen.findByRole("heading", { name: "Media & recordings" });

    const nav = screen.getByRole("navigation", { name: "Organization" });
    const collapseButton = within(nav).getByRole("button", { name: "Collapse Media & Recordings" });

    // Collapse
    await userEvent.click(collapseButton);
    expect(within(nav).queryByRole("link", { name: "All recordings" })).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Media & recordings" })).toBeInTheDocument();

    // Expand
    const expandButton = within(nav).getByRole("button", { name: "Expand Media & Recordings" });
    await userEvent.click(expandButton);
    expect(within(nav).getByRole("link", { name: "All recordings" })).toBeInTheDocument();
  });

  it("clicking parent Media & Recordings expands submenu if collapsed", async () => {
    renderApp("/organization/recordings");
    await screen.findByRole("heading", { name: "Media & recordings" });

    const nav = screen.getByRole("navigation", { name: "Organization" });
    const collapseButton = within(nav).getByRole("button", { name: "Collapse Media & Recordings" });
    await userEvent.click(collapseButton);
    expect(within(nav).queryByRole("link", { name: "All recordings" })).not.toBeInTheDocument();

    // Click parent row link
    const parentLink = within(nav).getByRole("link", { name: "Media & Recordings" });
    await userEvent.click(parentLink);

    // Submenu is re-expanded
    expect(within(nav).getByRole("link", { name: "All recordings" })).toBeInTheDocument();
  });

  it("navigates independently to child views like Replay-ready and Processing status", async () => {
    renderApp("/organization/recordings");
    await screen.findByRole("heading", { name: "Media & recordings" });

    const nav = screen.getByRole("navigation", { name: "Organization" });
    const readyLink = within(nav).getByRole("link", { name: "Replay-ready" });

    await userEvent.click(readyLink);

    // Filter updates to Replay-ready
    expect(readyLink).toHaveAttribute("aria-current", "page");
    const stateSelect = screen.getByLabelText("Filter by state");
    expect(stateSelect).toHaveValue("ready");

    // Processing status
    const procLink = within(nav).getByRole("link", { name: "Processing status" });
    await userEvent.click(procLink);
    expect(procLink).toHaveAttribute("aria-current", "page");
    expect(screen.getByLabelText("Filter by state")).toHaveValue("processing");
  });

  it("clicking parent Media & Recordings resets state and search filters", async () => {
    renderApp("/organization/recordings?state=ready&q=keynote");
    await screen.findByRole("heading", { name: "Media & recordings" });

    const searchInput = screen.getByLabelText("Search recordings");
    expect(searchInput).toHaveValue("keynote");
    expect(screen.getByLabelText("Filter by state")).toHaveValue("ready");

    const nav = screen.getByRole("navigation", { name: "Organization" });
    const parentLink = within(nav).getByRole("link", { name: "Media & Recordings" });

    await userEvent.click(parentLink);

    // Search query and state filter are reset
    await waitFor(() => {
      expect(screen.getByLabelText("Filter by state")).toHaveValue("all");
    });
    expect(screen.getByLabelText("Search recordings")).toHaveValue("");
    expect(within(nav).getByRole("link", { name: "All recordings" })).toHaveAttribute("aria-current", "page");
  });

  it("clicking breadcrumb Media & Recordings resets view and filters", async () => {
    renderApp("/organization/recordings?state=ready&q=keynote");
    await screen.findByRole("heading", { name: "Media & recordings" });

    const searchInput = screen.getByLabelText("Search recordings");
    expect(searchInput).toHaveValue("keynote");

    const breadcrumbs = screen.getByRole("navigation", { name: "Breadcrumb" });
    const mediaCrumb = within(breadcrumbs).getByRole("link", { name: "Media & Recordings" });

    await userEvent.click(mediaCrumb);

    await waitFor(() => {
      expect(screen.getByLabelText("Filter by state")).toHaveValue("all");
    });
    expect(screen.getByLabelText("Search recordings")).toHaveValue("");
  });
});
