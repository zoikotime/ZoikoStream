// The organization home screen, after it stopped being an operations console.
//
// What these pin, in order of what would actually hurt if it regressed:
//   1. every headline figure comes from the API — none is hardcoded from the design mock
//   2. "no producer for this figure" and "the figure is zero" stay visibly different
//   3. the operational panels that moved to their own pages do not creep back
//   4. empty and error states say what is absent AND what would fill it
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../api", () => ({
  default: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
  errMsg: (e) => e?.message ?? "Something went wrong.",
}));

vi.mock("../../auth/AuthContext", () => ({
  useAuth: vi.fn(() => ({ user: { full_name: "Ajay Kumar", email: "ajay@example.com" } })),
}));

import api from "../../api";
import { useAuth } from "../../auth/AuthContext";
import { ThemeProvider } from "../../theme/ThemeContext";
import OrganizationDashboard from "./Dashboard";
import { greetingName } from "../../components/organization/dashboard/dashboardConfig";

// Shapes copied from the real payloads: services/org.py::overview / analytics, and the
// /events Page envelope (schemas/admin.Page).
const OVERVIEW = {
  organization: { name: "Northwind" },
  sessions: { active: 1, live: 1, paused: 0, starting_soon: 2, current_audience: 40 },
  attention: [],
};

const ANALYTICS = {
  range: "30d",
  summary: { viewers: 18000, watch_hours: 421.5, peak: 1250, engagement: 68 },
  trends: {
    viewership: [
      { label: "May 1", value: 2400 },
      { label: "May 8", value: 6800 },
      { label: "May 15", value: 9200 },
    ],
  },
};

const UPCOMING = {
  total: 6,
  page: 1,
  page_size: 4,
  items: [
    {
      id: "11111111-1111-4111-8111-111111111111",
      title: "Tech Summit",
      start_time: "2026-10-20T10:00:00Z",
      visibility: "public",
      status: "scheduled",
      expected_audience: 523,
    },
    {
      id: "22222222-2222-4222-8222-222222222222",
      title: "Product Launch",
      start_time: "2026-10-25T14:00:00Z",
      visibility: "private",
      status: "published",
      expected_audience: null,
    },
  ],
};

const COMPLETED = { total: 45, page: 1, page_size: 1, items: [] };

// Per-test overrides keyed by what the request is for.
let world;

function route(url, config = {}) {
  const params = config.params || {};
  if (url === "/organization/overview") return world.overview();
  if (url === "/organization/analytics") {
    world.analyticsRanges.push(params.range);
    return world.analytics();
  }
  if (url === "/events") {
    return params.status === "ended" ? world.completed() : world.upcoming(params);
  }
  return Promise.resolve({ data: {} });
}

beforeEach(() => {
  world = {
    analyticsRanges: [],
    overview: () => Promise.resolve({ data: OVERVIEW }),
    analytics: () => Promise.resolve({ data: ANALYTICS }),
    upcoming: () => Promise.resolve({ data: UPCOMING }),
    completed: () => Promise.resolve({ data: COMPLETED }),
  };
  vi.mocked(api.get).mockImplementation(route);
  vi.mocked(useAuth).mockReturnValue({
    user: { full_name: "Ajay Kumar", email: "ajay@example.com" },
  });
});

function renderDashboard() {
  return render(
    <ThemeProvider>
      <MemoryRouter initialEntries={["/organization/dashboard"]}>
        <OrganizationDashboard />
      </MemoryRouter>
    </ThemeProvider>
  );
}

// Cards are addressed through the "At a glance" landmark and their own accessible name, so
// an assertion reads exactly one card and cannot match the panel below that happens to share
// a title ("Upcoming events" is both a card and a section).
async function card(label) {
  const glance = await screen.findByRole("region", { name: /at a glance/i });
  return within(glance).getByRole("link", { name: new RegExp(`^${label}`, "i") });
}

describe("welcome header", () => {
  it("greets the signed-in member by first name", async () => {
    renderDashboard();
    expect(await screen.findByRole("heading", { name: /welcome back, ajay/i })).toBeInTheDocument();
  });

  it("does not use the old operations heading", async () => {
    renderDashboard();
    await screen.findByRole("heading", { name: /welcome back/i });
    expect(screen.queryByText(/organization overview/i)).not.toBeInTheDocument();
  });

  it("falls back through full_name, then email, then no name at all", () => {
    expect(greetingName({ full_name: "Ada Lovelace" })).toBe("Ada");
    expect(greetingName({ email: "ada.lovelace@example.com" })).toBe("Ada");
    expect(greetingName({ full_name: "  ", email: "" })).toBeNull();
    expect(greetingName(undefined)).toBeNull();
  });

  it("never renders a raw email address as the greeting", () => {
    expect(greetingName({ email: "ada@example.com" })).not.toContain("@");
  });
});

describe("KPI cards", () => {
  it("shows the counts the API reported, not the design mock's numbers", async () => {
    renderDashboard();
    expect(within(await card("Upcoming events")).getByText("6")).toBeInTheDocument();
    expect(within(await card("Live now")).getByText("1")).toBeInTheDocument();
    expect(within(await card("Completed events")).getByText("45")).toBeInTheDocument();
    expect(within(await card("Total viewers")).getByText("18,000")).toBeInTheDocument();
  });

  it("counts upcoming events from the server's total, not from the page it lists", async () => {
    renderDashboard();
    // Four rows are requested for the panel; the count must still be the full 6.
    const upcoming = await card("Upcoming events");
    expect(within(upcoming).getByText("6")).toBeInTheDocument();
    expect(within(upcoming).getByText(/next on/i)).toBeInTheDocument();
  });

  it("renders a real zero as 0, never as unavailable", async () => {
    world.overview = () =>
      Promise.resolve({ data: { ...OVERVIEW, sessions: { live: 0, starting_soon: 0 } } });
    renderDashboard();
    const live = await card("Live now");
    expect(within(live).getByText("0")).toBeInTheDocument();
    expect(within(live).getByText(/nothing broadcasting/i)).toBeInTheDocument();
  });

  it("renders a missing figure as an em dash WITH a reason, never as 0", async () => {
    world.completed = () => Promise.reject(new Error("boom"));
    renderDashboard();
    const completed = await card("Completed events");
    expect(within(completed).getByText("—")).toBeInTheDocument();
    expect(within(completed).getByText(/couldn’t load events/i)).toBeInTheDocument();
    expect(within(completed).queryByText("0")).not.toBeInTheDocument();
  });

  it("still renders the rest of the dashboard when a supporting count fails", async () => {
    world.upcoming = () => Promise.reject(new Error("boom"));
    renderDashboard();
    // The overview is the hard dependency; a failed /events must not blank the page.
    expect(within(await card("Live now")).getByText("1")).toBeInTheDocument();
  });
});

describe("the operations console that used to be here", () => {
  it("no longer shows the six operational tiles", async () => {
    renderDashboard();
    await screen.findByRole("heading", { name: /welcome back/i });
    for (const gone of [
      /api request success/i,
      /ready media assets/i,
      /peak audience/i,
      /current usage/i,
      /service health/i,
      /needs attention/i,
      /right now/i,
    ]) {
      expect(screen.queryByText(gone)).not.toBeInTheDocument();
    }
  });

  it("signposts where that detail moved instead of dropping it silently", async () => {
    renderDashboard();
    expect(await screen.findByRole("link", { name: /streaming sessions/i })).toHaveAttribute(
      "href",
      "/organization/sessions"
    );
    expect(screen.getByRole("link", { name: /usage & entitlements/i })).toBeInTheDocument();
  });

  it("keeps attention items when there are any, and shows nothing when there are none", async () => {
    renderDashboard();
    await screen.findByRole("heading", { name: /welcome back/i });
    expect(screen.queryByText(/attention required/i)).not.toBeInTheDocument();

    world.overview = () =>
      Promise.resolve({
        data: {
          ...OVERVIEW,
          attention: [{ id: "a1", severity: "critical", title: "Credential expires in 3 days" }],
        },
      });
    renderDashboard();
    expect(await screen.findByText(/attention required/i)).toBeInTheDocument();
  });
});

describe("upcoming events panel", () => {
  it("lists the events the API returned", async () => {
    renderDashboard();
    expect(await screen.findByText("Tech Summit")).toBeInTheDocument();
    expect(screen.getByText("Product Launch")).toBeInTheDocument();
  });

  it("labels the organizer's own figure as Expected, never as Registered", async () => {
    renderDashboard();
    // The figure and its label are separate elements (the reference stacks them), so assert
    // the pair rather than one concatenated string.
    const row = (await screen.findByText("Tech Summit")).closest("li");
    expect(within(row).getByText("523")).toBeInTheDocument();
    expect(within(row).getByText(/^expected$/i)).toBeInTheDocument();
    // The backend has no registration count on this payload; the word must never appear.
    expect(screen.queryByText(/registered/i)).not.toBeInTheDocument();
  });

  it("says how many more there are beyond the ones listed", async () => {
    renderDashboard();
    expect(await screen.findByText(/4 more upcoming events/i)).toBeInTheDocument();
  });

  it("offers a way forward when there are none", async () => {
    world.upcoming = () => Promise.resolve({ data: { total: 0, items: [] } });
    renderDashboard();
    expect(await screen.findByText(/no upcoming events/i)).toBeInTheDocument();
    expect(screen.getByText(/schedule an event/i)).toBeInTheDocument();
  });
});

describe("analytics panel", () => {
  it("shows the summary figures under the same labels the Analytics page uses", async () => {
    renderDashboard();
    expect(await screen.findByText("Total Viewers")).toBeInTheDocument();
    expect(screen.getByText("421.5 hrs")).toBeInTheDocument();
    expect(screen.getByText("1,250")).toBeInTheDocument();
    expect(screen.getByText("68%")).toBeInTheDocument();
  });

  it("re-requests analytics when the period changes", async () => {
    renderDashboard();
    await screen.findByText("Total Viewers");
    expect(world.analyticsRanges).toEqual(["30d"]);

    await userEvent.selectOptions(screen.getByLabelText(/analytics period/i), "7d");
    await waitFor(() => expect(world.analyticsRanges).toContain("7d"));
  });

  it("does not re-request analytics on the sessions refresh", async () => {
    renderDashboard();
    await screen.findByText("Total Viewers");
    const before = world.analyticsRanges.length;

    await userEvent.click(screen.getByRole("button", { name: /refresh/i }));
    await waitFor(() => expect(vi.mocked(api.get).mock.calls.length).toBeGreaterThan(3));
    // The 90-day aggregate must not be re-pulled every time the live counts refresh.
    expect(world.analyticsRanges.length).toBe(before);
  });

  it("explains an empty trend instead of drawing a flat line across an empty chart", async () => {
    world.analytics = () =>
      Promise.resolve({
        data: { summary: { viewers: 0, watch_hours: 0, peak: 0, engagement: 0 }, trends: { viewership: [] } },
      });
    renderDashboard();
    expect(await screen.findByText(/no analytics yet/i)).toBeInTheDocument();
    expect(
      screen.getByText(/analytics will appear after your events receive traffic/i)
    ).toBeInTheDocument();
  });

  it("offers a retry when analytics alone fails", async () => {
    world.analytics = () => Promise.reject(new Error("nope"));
    renderDashboard();
    expect(await screen.findByText(/couldn’t load analytics/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /try again/i })).toBeInTheDocument();
  });
});

describe("failure of the page's hard dependency", () => {
  it("diagnoses an unreachable API and offers a retry", async () => {
    world.overview = () => Promise.reject(Object.assign(new Error("Network Error"), {}));
    renderDashboard();
    expect(await screen.findByText(/couldn’t load your dashboard/i)).toBeInTheDocument();
    expect(screen.getByText(/api is unreachable/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /try again/i })).toBeInTheDocument();
  });

  it("names a stale server build on a 404", async () => {
    world.overview = () => Promise.reject({ response: { status: 404 } });
    renderDashboard();
    expect(await screen.findByText(/older build/i)).toBeInTheDocument();
  });
});
