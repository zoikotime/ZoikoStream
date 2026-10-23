// The organization home screen, after it stopped being an operations console.
//
// What these pin, in order of what would actually hurt if it regressed:
//   1. every headline figure comes from the API — none is hardcoded from the design mock
//   2. "no producer for this figure" and "the figure is zero" stay visibly different
//   3. the operational panels that moved to their own pages do not creep back
//   4. empty and error states say what is absent AND what would fill it
//   5. the ORG-01 composition: upcoming (30 days) · live now · needs action · replay ready, with
//      the priority queue ahead of analytics, and links only for people who may follow them
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../api", () => ({
  default: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
  errMsg: (e) => e?.message ?? "Something went wrong.",
}));

vi.mock("../../auth/AuthContext", () => ({
  useAuth: vi.fn(() => ({ user: { full_name: "Ajay Kumar", email: "ajay@example.com", role: "org_admin" } })),
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
  media_assets: { ready: 3, processing: 0, not_captured: 1, total: 4 },
};

const ANALYTICS = {
  range: "30d",
  summary: { peak_viewers_summed: 18000, watch_hours: 421.5, peak: 1250, engagement: 68 },
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
    world.eventParams.push(params);
    // The pipeline's live and recently-ended rows are their own requests; empty unless a test
    // says otherwise, so the upcoming fixture is not counted three times.
    if (params.display_status === "on_air" || params.status === "ended") return Promise.resolve({ data: { total: 0, items: [] } });
    return world.upcoming(params);
  }
  return Promise.resolve({ data: {} });
}

beforeEach(() => {
  world = {
    analyticsRanges: [],
    eventParams: [],
    overview: () => Promise.resolve({ data: OVERVIEW }),
    analytics: () => Promise.resolve({ data: ANALYTICS }),
    upcoming: () => Promise.resolve({ data: UPCOMING }),
  };
  vi.mocked(api.get).mockImplementation(route);
  // The organization console's admin, as every page this dashboard links to requires.
  vi.mocked(useAuth).mockReturnValue({
    user: { full_name: "Ajay Kumar", email: "ajay@example.com", role: "org_admin" },
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
  const name = new RegExp(`^${label}`, "i");
  // A card the reader can open is a link; one they cannot is a named group.
  return within(glance).queryByRole("link", { name }) || within(glance).getByRole("group", { name });
}

// ORG-01 titles the page "Organization overview" and greets the member in the line beneath it.
const pageHeading = () => screen.findByRole("heading", { level: 1, name: /organization overview/i });

describe("welcome header", () => {
  it("greets the signed-in member by first name", async () => {
    renderDashboard();
    await pageHeading();
    expect(screen.getByText(/welcome back, ajay\./i)).toBeInTheDocument();
  });

  it("does not use the old operations heading", async () => {
    renderDashboard();
    await pageHeading();
    expect(screen.queryByText(/operations console/i)).not.toBeInTheDocument();
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
    expect(within(await card("Needs Action")).getByText("0")).toBeInTheDocument();
    expect(within(await card("Replay ready")).getByText("3")).toBeInTheDocument();
  });

  // ZST-UX-NAV-001 Figure D names the tile "[Needs Action]"; that contract outranks the visual
  // references' copy (ZST-ENG-DASH-001 §02, levels 2 and 3).
  it("names the attention tile Needs Action, in its accessible name as well as on screen", async () => {
    renderDashboard();
    const glance = await screen.findByRole("region", { name: /at a glance/i });
    const tile = within(glance).getByRole("group", { name: "Needs Action" });
    expect(within(tile).getByText("Needs Action")).toBeVisible();
    expect(within(glance).queryByText(/requires attention/i)).not.toBeInTheDocument();
  });

  it("counts upcoming events from the server's total, not from the page it lists", async () => {
    renderDashboard();
    // Four rows are requested for the panel; the count must still be the full 6.
    const upcoming = await card("Upcoming events");
    expect(within(upcoming).getByText("6")).toBeInTheDocument();
    expect(within(upcoming).getByText(/next 30 days · next on/i)).toBeInTheDocument();
  });

  it("asks the server for the next 30 days only", async () => {
    renderDashboard();
    await card("Upcoming events");
    const params = world.eventParams[0];
    const days = (new Date(params.date_to) - new Date(params.date_from)) / 86400000;
    expect(Math.round(days)).toBe(30);
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
    world.upcoming = () => Promise.reject(new Error("boom"));
    world.overview = () => Promise.resolve({ data: { ...OVERVIEW, media_assets: undefined } });
    renderDashboard();
    const upcoming = await card("Upcoming events");
    expect(within(upcoming).getByText("—")).toBeInTheDocument();
    expect(within(upcoming).getByText(/couldn’t load events/i)).toBeInTheDocument();
    const replay = await card("Replay ready");
    expect(within(replay).getByText("—")).toBeInTheDocument();
    expect(within(replay).getByText(/media status unavailable/i)).toBeInTheDocument();
    expect(within(replay).queryByText("0")).not.toBeInTheDocument();
  });

  it("names each recording's media state in words, on a 700 step (the 600s measured 3.2–4.0:1 at 12px)", async () => {
    vi.mocked(api.get).mockImplementation((url, config) => {
      if (url === "/organization/recordings") {
        return Promise.resolve({ data: [
          { id: "r1", title: "Town hall", state: "storage_unavailable", started_at: "2026-10-01T10:00:00Z" },
          { id: "r2", title: "Board sync", state: "ready", started_at: "2026-10-02T10:00:00Z" },
        ] });
      }
      if (url === "/organization/recordings/summary") return Promise.resolve({ data: { in_progress: 1, library_total: 2 } });
      return route(url, config);
    });
    renderDashboard();
    const media = await screen.findByRole("region", { name: "Media & recordings" });
    expect(await within(media).findByText("Storage unavailable")).toHaveClass("text-amber-700");
    expect(within(media).getByText("Ready")).toHaveClass("text-emerald-700");
    expect(within(media).getByText("Recording")).toHaveClass("text-amber-700");
  });

  it("still renders the rest of the dashboard when a supporting count fails", async () => {
    world.upcoming = () => Promise.reject(new Error("boom"));
    renderDashboard();
    // The overview is the hard dependency; a failed /events must not blank the page.
    expect(within(await card("Live now")).getByText("1")).toBeInTheDocument();
  });

  it("counts what needs action and says how much of it is critical", async () => {
    world.overview = () =>
      Promise.resolve({
        data: {
          ...OVERVIEW,
          attention: [
            { id: "a1", severity: "critical", title: "Town hall readiness blocked", to: "/organization/events/e1" },
            { id: "a2", severity: "warning", title: "Credential expires in 9 days", to: "/organization/credentials" },
          ],
        },
      });
    renderDashboard();
    const needs = await card("Needs Action");
    expect(within(needs).getByText("2")).toBeInTheDocument();
    expect(within(needs).getByText(/1 critical/i)).toBeInTheDocument();
  });

  it("links Live now to the Events list's live filter", async () => {
    renderDashboard();
    expect(await card("Live now")).toHaveAttribute("href", "/organization/events?status=on_air");
  });
});

describe("a member who is not an admin", () => {
  beforeEach(() => {
    vi.mocked(useAuth).mockReturnValue({ user: { full_name: "Hana Host", email: "h@example.com", role: "host" } });
  });

  it("sees the figures, but no card links to a page they cannot open", async () => {
    renderDashboard();
    const glance = await screen.findByRole("region", { name: /at a glance/i });
    await within(glance).findByRole("group", { name: /^live now/i });
    expect(within(glance).queryAllByRole("link")).toHaveLength(0);
  });

  it("is not pointed at admin-only pages by the signpost", async () => {
    renderDashboard();
    await pageHeading();
    expect(screen.queryByText(/looking for sessions/i)).not.toBeInTheDocument();
  });
});

// services/org.py::analytics renamed summary.viewers to peak_viewers_summed; the dashboard
// kept reading the old key and rendered "—" for every org. The audience total now lives in
// the analytics panel (the KPI row carries ORG-01's four), and these pin the real wire shape.
describe("Total viewers reads the analytics service's real key", () => {
  // A single ended event with one viewer: the production case that surfaced the bug.
  const ONE_VIEWER = {
    range: "30d",
    summary: { peak_viewers_summed: 1, watch_hours: 0.033, peak: 1, engagement: 0 },
    trends: { viewership: [{ label: "Sep 27", value: 1 }] },
  };

  function metric(label) {
    // The overview panel's <dt>label</dt><dd>value</dd> pairs.
    return screen.getByText(label, { selector: "dt" }).nextElementSibling;
  }

  it("shows the recorded audience", async () => {
    world.analytics = () => Promise.resolve({ data: ONE_VIEWER });
    renderDashboard();
    await waitFor(() => expect(metric("Total Viewers")).toHaveTextContent("1"));
  });

  it("is the sum of per-event peaks, not the single highest peak", async () => {
    // Two events peaking at 3 and 2: summed = 5, peak = 3. The two must not be conflated.
    world.analytics = () =>
      Promise.resolve({
        data: {
          ...ONE_VIEWER,
          summary: { peak_viewers_summed: 5, watch_hours: 0.5, peak: 3, engagement: 12 },
        },
      });
    renderDashboard();
    await waitFor(() => expect(metric("Total Viewers")).toHaveTextContent("5"));
    expect(metric("Peak Concurrent")).toHaveTextContent("3");
  });

  it("leaves the other analytics figures exactly as the API reported them", async () => {
    world.analytics = () => Promise.resolve({ data: ONE_VIEWER });
    renderDashboard();
    await waitFor(() => expect(metric("Total Viewers")).toHaveTextContent("1"));
    expect(metric("Watch Time")).toHaveTextContent("0.033 hrs");
    expect(metric("Peak Concurrent")).toHaveTextContent("1");
    expect(metric("Avg. Engagement")).toHaveTextContent("0%");
  });

  it("does not fall back to the retired `viewers` key", async () => {
    world.analytics = () =>
      Promise.resolve({ data: { summary: { viewers: 99, peak: 1 }, trends: { viewership: [] } } });
    renderDashboard();
    await waitFor(() => expect(metric("Total Viewers")).toBeTruthy());
    expect(metric("Total Viewers")).not.toHaveTextContent("99");
  });

  // The mobile app showed "—" / "No audience recorded yet" beside a web dashboard showing 1.
  // A build from before the key change reads `summary.viewers`, which the API no longer sends;
  // the card then claimed an empty organization. analytics() always sends a number, so a
  // missing or malformed one now says the count is unavailable rather than inventing an empty.
  it("says the count is unavailable, not that there was no audience, when the key is missing", async () => {
    world.analytics = () =>
      Promise.resolve({ data: { summary: { viewers: 1, peak: 1 }, trends: { viewership: [] } } });
    renderDashboard();
    await waitFor(() => expect(metric("Total Viewers")).toBeTruthy());
    expect(metric("Total Viewers")).toHaveTextContent("—");
  });

  it.each([
    ["a string", "1"],
    ["NaN", Number.NaN],
    ["null", null],
  ])("treats %s as unavailable, never printing it", async (_name, value) => {
    world.analytics = () =>
      Promise.resolve({ data: { summary: { peak_viewers_summed: value, peak: 1 }, trends: { viewership: [] } } });
    renderDashboard();
    await waitFor(() => expect(metric("Total Viewers")).toBeTruthy());
    expect(metric("Total Viewers")).toHaveTextContent("—");
    expect(metric("Total Viewers")).not.toHaveTextContent("NaN");
  });

  it("is not mistaken for zero while analytics is still loading", async () => {
    world.analytics = () => new Promise(() => {});
    renderDashboard();
    await pageHeading();
    expect(screen.queryByText("Total Viewers")).not.toBeInTheDocument();
  });

  it("formats a larger count and reads it from the same 30-day window the note names", async () => {
    world.analytics = () =>
      Promise.resolve({ data: { ...ONE_VIEWER, summary: { ...ONE_VIEWER.summary, peak_viewers_summed: 1234567 } } });
    renderDashboard();
    await waitFor(() => expect(metric("Total Viewers")).toHaveTextContent((1234567).toLocaleString()));
    expect(world.analyticsRanges[0]).toBe("30d");
  });
});

describe("the operations console that used to be here", () => {
  it("no longer shows the six operational tiles", async () => {
    renderDashboard();
    await pageHeading();
    for (const gone of [
      /api request success/i,
      /ready media assets/i,
      /peak audience/i,
      /current usage/i,
      /service health/i,
      /right now/i,
    ]) {
      expect(screen.queryByText(gone)).not.toBeInTheDocument();
    }
  });

  it("signposts where that detail moved instead of dropping it silently", async () => {
    renderDashboard();
    expect(await screen.findByRole("link", { name: /plan, usage & billing/i })).toHaveAttribute(
      "href",
      "/organization/billing"
    );
    expect(screen.getByText(/live control/i)).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /streaming sessions/i })).not.toBeInTheDocument();
  });

  it("always shows the priority queue — empty says so in words, items come first", async () => {
    renderDashboard();
    const queue = await screen.findByRole("region", { name: /requires attention/i });
    expect(within(queue).getByText(/priority action queue/i)).toBeInTheDocument();
    expect(within(queue).getByText(/nothing needs attention/i)).toBeInTheDocument();
  });

  it("lists attention items when there are any", async () => {
    world.overview = () =>
      Promise.resolve({
        data: {
          ...OVERVIEW,
          attention: [{ id: "a1", severity: "critical", title: "Credential expires in 3 days", to: "/organization/credentials", action: "Rotate" }],
        },
      });
    renderDashboard();
    const queue = await screen.findByRole("region", { name: /requires attention/i });
    expect(await within(queue).findByText("Credential expires in 3 days")).toBeInTheDocument();
  });
});

// The pipeline table — the next event is also featured in the Next priority card above it.
const pipeline = () => within(screen.getByRole("region", { name: "Event pipeline" }));

describe("upcoming events panel", () => {
  it("lists the events the API returned", async () => {
    renderDashboard();
    await pageHeading();
    expect(await pipeline().findByText("Tech Summit")).toBeInTheDocument();
    expect(pipeline().getByText("Product Launch")).toBeInTheDocument();
  });

  it("labels the organizer's own figure as Expected, never as Registered", async () => {
    renderDashboard();
    await pageHeading();
    // The figure and its label are separate elements (the reference stacks them), so assert
    // the pair rather than one concatenated string.
    const row = (await pipeline().findByText("Tech Summit")).closest("tr");
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
        data: { summary: { peak_viewers_summed: 0, watch_hours: 0, peak: 0, engagement: 0 }, trends: { viewership: [] } },
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
