// Audience, Streaming Sessions and Playback & Access, after the page-level right rail was
// removed.
//
// The thing worth pinning is not "it looks nicer" — it is that the split is gone while every
// control, table and honest blank came through the move intact. A layout change that
// silently drops a filter, or turns an unmeasured "—" into a 0, is the failure mode here.
import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../api", () => ({
  default: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
  errMsg: (e) => e?.message ?? "Something went wrong.",
}));

vi.mock("../../auth/AuthContext", () => ({
  useAuth: () => ({ user: { full_name: "Vihari Owner", email: "owner@example.com" }, logout: vi.fn() }),
}));

import api from "../../api";
import { ThemeProvider } from "../../theme/ThemeContext";
import AudienceAccess from "./AudienceAccess";
import StreamingSessions from "./StreamingSessions";
import PlaybackAccess from "./PlaybackAccess";

const EVENTS = {
  total: 2,
  page: 1,
  page_size: 100,
  items: [
    {
      id: "11111111-1111-4111-8111-111111111111",
      title: "event launch",
      start_time: "2026-09-09T20:44:00Z",
      visibility: "public",
      status: "scheduled",
      registration_required: false,
      password_protected: false,
      replay_enabled: false,
      registration_limit: null,
      expected_audience: null,
    },
    {
      id: "22222222-2222-4222-8222-222222222222",
      title: "second launch",
      start_time: "2026-09-02T12:08:00Z",
      visibility: "public",
      status: "scheduled",
      registration_required: false,
      password_protected: false,
      replay_enabled: false,
      registration_limit: null,
      expected_audience: null,
    },
  ],
};

// Everything unmeasured, which is the real state of this deployment and the harder case:
// each of these must survive the move as "—", never as 0.
const ATTENDANCE = {
  unique_attendees: 0,
  returning: 0,
  avg_watch_minutes: null,
  show_rate: null,
  unique_attendees_estimated: true,
  avg_watch_minutes_estimated: true,
};

const OVERVIEW = {
  organization: { name: "Northwind" },
  sessions: {
    active: 0,
    live: 0,
    paused: 0,
    starting_soon: 0,
    current_audience: null,
    peak_audience: null,
    items: [],
    breakdown_note: "Self-service vs managed classification is not modelled.",
  },
  media_assets: {},
  entitlements: {},
  api: {},
  service_health: {},
  attention: [],
  upcoming_events: [],
};

beforeEach(() => {
  vi.mocked(api.get).mockImplementation((url) => {
    if (url === "/events") return Promise.resolve({ data: EVENTS });
    if (url === "/organization/audience-attendance") return Promise.resolve({ data: ATTENDANCE });
    if (url === "/organization/overview") return Promise.resolve({ data: OVERVIEW });
    return Promise.resolve({ data: {} });
  });
});

const renderPage = (Page) =>
  render(
    <ThemeProvider>
      <MemoryRouter>
        <Page />
      </MemoryRouter>
    </ThemeProvider>
  );

// The page-level split was expressed as a two-track grid template. Anything matching this is
// a main-plus-rail layout; grids INSIDE a section (metric rows, link grids) are fine and are
// deliberately not matched.
const RAIL = /grid-cols-\[(280px|minmax\(0,1fr\))_/;

function railTracks(container) {
  return [...container.querySelectorAll("[class*='grid-cols-[']")]
    .map((el) => el.className)
    .filter((c) => RAIL.test(c));
}

describe("no page-level right rail", () => {
  it.each([
    ["Audience", AudienceAccess],
    ["Streaming Sessions", StreamingSessions],
    ["Playback & Access", PlaybackAccess],
  ])("%s renders one column", async (_name, Page) => {
    const { container } = renderPage(Page);
    await screen.findByRole("heading", { level: 1 });
    expect(railTracks(container)).toEqual([]);
  });
});

describe("Audience keeps its content, in order, full width", () => {
  it("still has the table, its search and the playback link", async () => {
    renderPage(AudienceAccess);
    await screen.findByRole("heading", { level: 1, name: /audience access/i });
    expect(screen.getByRole("table")).toBeInTheDocument();
    expect(screen.getByPlaceholderText(/search events/i)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /playback gates/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /refresh/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /export/i })).toBeInTheDocument();
  });

  it("reads top to bottom: access by event, then attendance, then not measured, then controls", async () => {
    renderPage(AudienceAccess);
    await screen.findByRole("heading", { level: 1 });
    const order = ["Access by event", "Attendance", "Not measured", "Controls that shape the audience"];
    const tops = order.map((t) => {
      const heading = screen.getByRole("heading", { name: t });
      return [...document.querySelectorAll("*")].indexOf(heading);
    });
    expect(tops).toEqual([...tops].sort((x, y) => x - y));
  });

  it("keeps unmeasured figures as an em dash, never as zero", async () => {
    renderPage(AudienceAccess);
    await screen.findByRole("heading", { level: 1 });
    // avg_watch_minutes and show_rate are null in the payload above.
    const watch = screen.getByText("Average watch time").closest("div");
    expect(within(watch).getByText("—")).toBeInTheDocument();
    // …and the three that nothing produces at all.
    for (const label of ["Audience geography", "Device and player mix", "Blocked join attempts"]) {
      expect(within(screen.getByText(label).closest("div")).getByText("—")).toBeInTheDocument();
    }
  });

  it("keeps the estimation caveat rather than quietly presenting estimates as counts", async () => {
    renderPage(AudienceAccess);
    await screen.findByRole("heading", { level: 1 });
    expect(screen.getByText(/estimated from sampled concurrent viewers/i)).toBeInTheDocument();
  });
});

describe("Streaming Sessions keeps its content, in order, full width", () => {
  it("still has the range tabs, refresh and the analytics link", async () => {
    renderPage(StreamingSessions);
    await screen.findByRole("heading", { level: 1, name: /streaming sessions/i });
    expect(screen.getByRole("button", { name: /^24 hours$/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^7 days$/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^30 days$/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /refresh/i })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /full analytics/i })).toBeInTheDocument();
  });

  it("reads top to bottom: recent sessions, audience, not measured, where to go next", async () => {
    renderPage(StreamingSessions);
    await screen.findByRole("heading", { level: 1 });
    const order = ["Recent sessions", "Audience", "Not measured here", "Where to go next"];
    const tops = order.map((t) => {
      const heading = screen.getByRole("heading", { name: t });
      return [...document.querySelectorAll("*")].indexOf(heading);
    });
    expect(tops).toEqual([...tops].sort((x, y) => x - y));
  });

  it("keeps the empty state and its way out", async () => {
    renderPage(StreamingSessions);
    const empty = (await screen.findByText(/no sessions in this window/i)).closest("td");
    expect(empty).not.toBeNull();
    // Scoped to the empty state: "Live Events" is now legitimately on this page twice, here
    // and in Where to go next, and the CTA in the table is the one this test is about.
    expect(within(empty).getByRole("link", { name: /live events/i })).toBeInTheDocument();
    expect(within(empty).getByText(/widen the range/i)).toBeInTheDocument();
  });

  it("keeps unproduced session facts as an em dash", async () => {
    renderPage(StreamingSessions);
    await screen.findByRole("heading", { level: 1 });
    for (const label of ["Ingest protocol", "Ingest region", "Self-service vs managed", "All-time peak"]) {
      expect(within(screen.getByText(label).closest("div")).getByText("—")).toBeInTheDocument();
    }
    expect(screen.getByText(/because nothing produces them, not because the value is zero/i))
      .toBeInTheDocument();
  });

  it("offers all three onward destinations", async () => {
    renderPage(StreamingSessions);
    await screen.findByRole("heading", { name: "Where to go next" });
    const next = screen.getByRole("heading", { name: "Where to go next" }).closest("section");
    for (const label of [/live events/i, /playback & access/i, /media & replay/i]) {
      expect(within(next).getByRole("link", { name: label })).toBeInTheDocument();
    }
  });
});

describe("Playback & Access keeps its content, in one column", () => {
  it("keeps the event picker as a listbox, now laid out across the page", async () => {
    renderPage(PlaybackAccess);
    await screen.findByRole("heading", { level: 1, name: /playback & access/i });
    const list = screen.getByRole("listbox", { name: /select an event/i });
    expect(within(list).getAllByRole("option")).toHaveLength(2);
    // Selection semantics survived the move from a column to a row.
    expect(within(list).getAllByRole("option", { selected: true })).toHaveLength(1);
  });

  it("keeps the access-rule sections and the refresh control", async () => {
    renderPage(PlaybackAccess);
    await screen.findByRole("heading", { level: 1 });
    expect(screen.getByRole("button", { name: /refresh/i })).toBeInTheDocument();
    expect(screen.getByText(/visibility gate/i)).toBeInTheDocument();
    expect(screen.getByText(/registration gate/i)).toBeInTheDocument();
    expect(screen.getByText(/passphrase gate/i)).toBeInTheDocument();
    expect(screen.getByText(/waiting room/i)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /edit event/i })).toBeInTheDocument();
  });

  it("keeps viewer-side measurement honest about what is not ingested", async () => {
    renderPage(PlaybackAccess);
    await screen.findByRole("heading", { level: 1 });
    for (const label of ["Playback starts", "Rebuffer ratio", "Failures by reason"]) {
      expect(within(screen.getByText(label).closest("div")).getByText("—")).toBeInTheDocument();
    }
  });
});
