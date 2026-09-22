// "Request live event" on /organization/events.
//
// ── THE BUG ────────────────────────────────────────────────────────────────────────────
// The topbar button worked on every /organization/* page except the one it points AT.
//
// It opens the dialog by navigating to /organization/events?create=true, and the Events
// page used to read that parameter in a useState initializer — which runs once, when the
// route mounts. From the dashboard, audience, members and the rest, the click DOES mount
// the Events route, so the initializer saw create=true and the dialog opened. From
// /organization/events itself there is no mount: the topbar lives in OrganizationLayout, so
// the click changes only the query string and React Router re-renders the same mounted
// element. The initializer never ran again, and the button did nothing.
//
// These run the REAL page under the REAL layout, so the button under test is the one in the
// shipped topbar, not a stand-in.
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../api", () => ({
  default: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
  errMsg: (e) => e?.message ?? "Something went wrong.",
}));
vi.mock("../../ui/Toast", () => ({
  notify: { error: vi.fn(), success: vi.fn(), alert: vi.fn() },
}));
vi.mock("../../auth/AuthContext", () => ({
  useAuth: () => ({
    user: { full_name: "Vihari Owner", email: "owner@example.com" },
    logout: vi.fn(),
  }),
}));

import api from "../../api";
import { ThemeProvider } from "../../theme/ThemeContext";
import OrganizationLayout from "../../layouts/OrganizationLayout";
import OrganizationEvents from "./Events";

const CONSOLE_STATE = {
  organization: { name: "Northwind", plan: "Growth" },
  workspace: { label: "production", slug: "production" },
  workspaces: [{ label: "production", slug: "production" }],
  badges: {},
  user: { name: "Vihari Owner", email: "owner@example.com", role: "Organization Owner" },
  health: { status: "ok" },
};

// One real row, so the table renders instead of the empty state — which carries its own
// second "Create event" button and would make that name ambiguous.
const EVENT = {
  id: "e1", title: "TEST", slug: "test-5", status: "scheduled",
  start_time: "2026-09-21T11:31:00Z", visibility: "public",
  registration_required: false, duration_minutes: 9,
};

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  vi.mocked(api.get).mockImplementation((url) => {
    if (url === "/organization/console-state") return Promise.resolve({ data: CONSOLE_STATE });
    if (url === "/events") return Promise.resolve({ data: { items: [EVENT] } });
    return Promise.resolve({ data: { items: [] } });
  });
});

function renderAt(at) {
  return render(
    <ThemeProvider>
      <MemoryRouter initialEntries={[at]}>
        <Routes>
          <Route element={<OrganizationLayout />}>
            <Route path="/organization/events" element={<OrganizationEvents />} />
            {/* A neighbour, so the cross-page path that already worked can be checked too. */}
            <Route path="/organization/dashboard" element={<h1>Dashboard page</h1>} />
          </Route>
        </Routes>
      </MemoryRouter>
    </ThemeProvider>
  );
}

const requestButton = () => screen.getByRole("button", { name: /request live event/i });
const createButton = () => screen.getByRole("button", { name: /^create event$/i });
const modal = () => screen.queryByRole("heading", { name: /^create event$/i });
const eventsPage = () => screen.findByRole("heading", { name: /^events$/i });

// ── the regression ─────────────────────────────────────────────────────────────────────

describe("/organization/events → Request live event", () => {
  it("opens the modal", async () => {
    const u = userEvent.setup();
    renderAt("/organization/events");
    await eventsPage();
    expect(modal()).not.toBeInTheDocument();

    await u.click(requestButton());

    expect(modal()).toBeInTheDocument();
  });

  it("does not redirect away from the Events page", async () => {
    const u = userEvent.setup();
    renderAt("/organization/events");
    await eventsPage();

    await u.click(requestButton());

    expect(await eventsPage()).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: /dashboard page/i })).not.toBeInTheDocument();
  });

  it("works a second time, after the first one was dismissed", async () => {
    // The parameter is cleared on close, so the next navigate() is a real location change
    // rather than a no-op to an identical URL. Without that, one click per page load.
    const u = userEvent.setup();
    renderAt("/organization/events");
    await eventsPage();

    await u.click(requestButton());
    expect(modal()).toBeInTheDocument();

    await u.click(screen.getByRole("button", { name: "Close" }));
    await waitFor(() => expect(modal()).not.toBeInTheDocument());

    await u.click(requestButton());
    expect(modal()).toBeInTheDocument();
  });
});

// ── the two buttons are independent ────────────────────────────────────────────────────

describe("Create event and Request live event on the same page", () => {
  it("both open the same dialog, each on its own", async () => {
    const u = userEvent.setup();
    renderAt("/organization/events");
    await eventsPage();

    await u.click(createButton());
    expect(modal()).toBeInTheDocument();
    await u.click(screen.getByRole("button", { name: "Close" }));
    await waitFor(() => expect(modal()).not.toBeInTheDocument());

    await u.click(requestButton());
    expect(modal()).toBeInTheDocument();
  });

  it("Create event still opens the dialog without touching the URL", async () => {
    const u = userEvent.setup();
    renderAt("/organization/events");
    await eventsPage();

    await u.click(createButton());

    expect(modal()).toBeInTheDocument();
    // Closing a dialog the URL never asked for must not need the URL to change back.
    await u.click(screen.getByRole("button", { name: "Close" }));
    await waitFor(() => expect(modal()).not.toBeInTheDocument());
    expect(createButton()).toBeInTheDocument();
  });

  it("neither button is blocked by the other being present", async () => {
    renderAt("/organization/events");
    await eventsPage();

    expect(requestButton()).toBeEnabled();
    expect(createButton()).toBeEnabled();
  });
});

// ── the paths that already worked must keep working ────────────────────────────────────

describe("the routes that were never broken", () => {
  it("still opens from the ?create=true deep link on first load", async () => {
    renderAt("/organization/events?create=true");
    await eventsPage();

    expect(modal()).toBeInTheDocument();
  });

  it("still opens when the click comes from another organization page", async () => {
    const u = userEvent.setup();
    renderAt("/organization/dashboard");
    await screen.findByRole("heading", { name: /dashboard page/i });

    await u.click(requestButton());

    await eventsPage();
    expect(modal()).toBeInTheDocument();
  });

  it("does not reopen after dismissal, because the parameter is cleared", async () => {
    const u = userEvent.setup();
    renderAt("/organization/events?create=true");
    await eventsPage();
    expect(modal()).toBeInTheDocument();

    await u.click(screen.getByRole("button", { name: "Close" }));

    await waitFor(() => expect(modal()).not.toBeInTheDocument());
  });
});
