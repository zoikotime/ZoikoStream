import { render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../api", () => ({
  default: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
  errMsg: (e) => e?.message ?? "Something went wrong.",
  API_BASE: "http://api.test",
  AUTH_EXPIRED_EVENT: "zoiko:auth-expired",
}));
vi.mock("../../ui/Toast", () => ({ notify: { error: vi.fn(), success: vi.fn(), alert: vi.fn() } }));
vi.mock("../../auth/AuthContext", () => ({
  useAuth: () => ({ user: { id: "u1", full_name: "Admin", email: "admin@zoiko.test", role: "org_admin" }, logout: vi.fn() }),
}));
vi.mock("../../components/organization/RecordingPanel", () => ({ default: () => null }));
vi.mock("../../components/organization/EventCommercial", () => ({ default: () => null }));

import api from "../../api";
import { ThemeProvider } from "../../theme/ThemeContext";
import Events from "./Events";
import { isUpcomingEvent } from "../../data/events";

const MOCK_NOW = new Date("2026-10-27T09:00:00.000Z").getTime();

// Realistic fixtures matching the reproduction report:
// - "manual testing": 27 Oct 2026 09:23 GMT+5:30 -> 2026-10-27T03:53:00.000Z (future or future-scheduled)
// - "webinar": 29 Sep 2026 10:13 GMT+5:30 -> 2026-09-29T04:43:00.000Z (past start time, start_overdue = true)
const eventScheduledFuture = {
  id: "e-future",
  title: "manual testing",
  slug: "manual-testing",
  status: "scheduled",
  display_status: "scheduled",
  start_overdue: false,
  start_time: "2026-10-27T12:00:00.000Z", // future relative to MOCK_NOW
  timezone: "Asia/Kolkata",
  visibility: "public",
  category: "Webinar",
  duration_minutes: 60,
};

const eventPublishedPast = {
  id: "e-past-published",
  title: "webinar",
  slug: "webinar",
  status: "published",
  display_status: "published",
  start_overdue: true,
  start_time: "2026-09-29T04:43:00.000Z", // past relative to MOCK_NOW
  timezone: "Asia/Kolkata",
  visibility: "public",
  category: "Webinar",
  duration_minutes: 60,
};

const eventLiveNow = {
  id: "e-live",
  title: "All-Hands Live Broadcast",
  slug: "all-hands-live",
  status: "live",
  display_status: "live",
  start_overdue: false,
  start_time: "2026-10-27T08:30:00.000Z",
  timezone: "UTC",
  visibility: "public",
};

const eventCompleted = {
  id: "e-ended",
  title: "Product Keynote Ended",
  slug: "product-keynote",
  status: "ended",
  display_status: "ended",
  start_overdue: false,
  start_time: "2026-10-20T10:00:00.000Z",
  timezone: "UTC",
  visibility: "public",
};

const eventDraft = {
  id: "e-draft",
  title: "Q4 Planning Draft",
  slug: "q4-planning-draft",
  status: "draft",
  display_status: "draft",
  start_overdue: false,
  start_time: null,
  timezone: "UTC",
  visibility: "private",
};

const ALL_FIXTURES = [eventScheduledFuture, eventPublishedPast, eventLiveNow, eventCompleted, eventDraft];

function renderEvents(initialEntry = "/organization/events?status=upcoming") {
  return render(
    <ThemeProvider>
      <MemoryRouter initialEntries={[initialEntry]}>
        <Events />
      </MemoryRouter>
    </ThemeProvider>,
  );
}

const table = () => within(screen.queryByRole("table") || document.createElement("div"));

describe("Upcoming business rule predicate (isUpcomingEvent)", () => {
  it("qualifies a future Scheduled or Published event as upcoming", () => {
    expect(isUpcomingEvent(eventScheduledFuture, MOCK_NOW)).toBe(true);
    const futurePublished = { ...eventScheduledFuture, status: "published", display_status: "published" };
    expect(isUpcomingEvent(futurePublished, MOCK_NOW)).toBe(true);
  });

  it("disqualifies a Published event whose start time has passed (start_overdue or past start_time)", () => {
    expect(isUpcomingEvent(eventPublishedPast, MOCK_NOW)).toBe(false);
    // Even if start_overdue was false, past start_time disqualifies it
    const pastPubWithoutFlag = { ...eventPublishedPast, start_overdue: false };
    expect(isUpcomingEvent(pastPubWithoutFlag, MOCK_NOW)).toBe(false);
  });

  it("disqualifies Missed, Draft, Live, Ended, and Cancelled events", () => {
    expect(isUpcomingEvent(eventDraft, MOCK_NOW)).toBe(false);
    expect(isUpcomingEvent(eventLiveNow, MOCK_NOW)).toBe(false);
    expect(isUpcomingEvent(eventCompleted, MOCK_NOW)).toBe(false);
    const missed = { ...eventScheduledFuture, display_status: "missed" };
    expect(isUpcomingEvent(missed, MOCK_NOW)).toBe(false);
  });

  it("handles events at the exact current-time boundary deterministically", () => {
    const boundaryEvent = {
      ...eventScheduledFuture,
      start_time: new Date(MOCK_NOW).toISOString(),
    };
    // At exact boundary (t === now), t is not strictly < now, so it qualifies
    expect(isUpcomingEvent(boundaryEvent, MOCK_NOW)).toBe(true);
    // 1 millisecond later, start time has passed
    expect(isUpcomingEvent(boundaryEvent, MOCK_NOW + 1)).toBe(false);
  });

  it("handles timezone offsets and daylight-saving transitions correctly across zones", () => {
    // 09:00 UTC == 14:30 IST (+05:30) == 05:00 EDT (-04:00)
    const utcIso = "2026-10-27T09:00:00.000Z";
    const istIso = "2026-10-27T14:30:00+05:30";
    const edtIso = "2026-10-27T05:00:00-04:00";

    const baseEvent = { status: "scheduled", display_status: "scheduled", start_overdue: false };

    // At 08:59 UTC (1 minute before), all are in the future
    const clockBefore = new Date("2026-10-27T08:59:00.000Z").getTime();
    expect(isUpcomingEvent({ ...baseEvent, start_time: utcIso }, clockBefore)).toBe(true);
    expect(isUpcomingEvent({ ...baseEvent, start_time: istIso }, clockBefore)).toBe(true);
    expect(isUpcomingEvent({ ...baseEvent, start_time: edtIso }, clockBefore)).toBe(true);

    // At 09:01 UTC (1 minute after), all are in the past
    const clockAfter = new Date("2026-10-27T09:01:00.000Z").getTime();
    expect(isUpcomingEvent({ ...baseEvent, start_time: utcIso }, clockAfter)).toBe(false);
    expect(isUpcomingEvent({ ...baseEvent, start_time: istIso }, clockAfter)).toBe(false);
    expect(isUpcomingEvent({ ...baseEvent, start_time: edtIso }, clockAfter)).toBe(false);
  });
});

describe("Organization Events Page: Upcoming Metric & Tab Filtering", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.useFakeTimers({ now: MOCK_NOW, shouldAdvanceTime: true });

    vi.mocked(api.get).mockImplementation((url, cfg) => {
      if (url === "/events/status-counts") {
        return Promise.resolve({
          data: {
            counts: {
              draft: 1,
              published: 1,
              scheduled: 1,
              ready_to_arm: 0,
              armed: 0,
              live: 1,
              degraded: 0,
              ended: 1,
              cancelled: 0,
              archived: 0,
            },
            display_counts: {
              draft: 1,
              published: 1,
              scheduled: 1,
              upcoming: 1, // Only 1 truly upcoming event (manual testing)
              missed: 0,
              ready_to_arm: 0,
              armed: 0,
              live: 1,
              degraded: 0,
              ended: 1,
              cancelled: 0,
              archived: 0,
            },
            attention: {
              blocked: 0,
              conditional: 1,
              events: [{ id: "e-future", title: "manual testing", verdict: "conditional", blockers: 0, failing: 1 }],
            },
          },
        });
      }

      if (url === "/events") {
        const params = cfg?.params || {};
        let filtered = [...ALL_FIXTURES];

        // Status / display_status filter
        if (params.display_status === "upcoming") {
          filtered = filtered.filter((e) => isUpcomingEvent(e, MOCK_NOW));
        } else if (params.display_status === "on_air") {
          filtered = filtered.filter((e) => ["live", "degraded"].includes(e.status));
        } else if (params.display_status === "ended") {
          filtered = filtered.filter((e) => e.status === "ended");
        } else if (params.display_status === "draft") {
          filtered = filtered.filter((e) => e.status === "draft");
        } else if (params.display_status) {
          filtered = filtered.filter((e) => (e.display_status || e.status) === params.display_status);
        }

        // Search query filter
        if (params.q) {
          const qLower = params.q.toLowerCase();
          filtered = filtered.filter((e) => e.title.toLowerCase().includes(qLower));
        }

        // Date range filter
        if (params.date_from) {
          const fromTime = new Date(params.date_from).getTime();
          filtered = filtered.filter((e) => e.start_time && new Date(e.start_time).getTime() >= fromTime);
        }
        if (params.date_to) {
          const toTime = new Date(params.date_to).getTime();
          filtered = filtered.filter((e) => e.start_time && new Date(e.start_time).getTime() <= toTime);
        }

        return Promise.resolve({
          data: {
            items: filtered,
            total: filtered.length,
            page: params.page || 1,
            page_size: params.page_size || 10,
          },
        });
      }

      return Promise.resolve({ data: [] });
    });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("1 & 2: Future Scheduled event appears in Upcoming and is counted; past Published event does not appear", async () => {
    renderEvents("/organization/events?status=upcoming");

    // "manual testing" is scheduled in future -> must be in document
    await table().findByText("manual testing");
    expect(table().getByText("manual testing")).toBeInTheDocument();

    // "webinar" had past start time -> must NOT appear in Upcoming tab
    expect(table().queryByText("webinar")).toBeNull();
  });

  it("3: The metric count equals the number of events returned by the Upcoming tab", async () => {
    renderEvents("/organization/events?status=upcoming");

    await table().findByText("manual testing");

    // Metric Tile: Upcoming displays "01" (two-wide formatted 1)
    const upcomingTile = screen.getByRole("group", { name: "Upcoming" });
    expect(within(upcomingTile).getByText("01")).toBeInTheDocument();

    // Upcoming Tab pill: displays "Upcoming1"
    const upcomingTab = screen.getByRole("tab", { name: /^Upcoming/ });
    expect(upcomingTab).toHaveTextContent(/^Upcoming1$/);

    // List table: displays exactly 1 row
    expect(table().getAllByRole("row")).toHaveLength(2); // 1 header row + 1 data row
  });

  it("4: Search, date filters, and status filters compose correctly", async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    renderEvents("/organization/events?status=upcoming");

    await table().findByText("manual testing");

    // Search for a non-matching query
    const searchInput = screen.getByPlaceholderText("Search events by name…");
    await user.type(searchInput, "nonexistent query");
    await vi.advanceTimersByTimeAsync(400);

    await waitFor(() => {
      expect(table().queryByText("manual testing")).toBeNull();
      expect(screen.getByText("No events match your filters")).toBeInTheDocument();
    });

    // Clear search -> "manual testing" restores
    await user.clear(searchInput);
    await vi.advanceTimersByTimeAsync(400);

    await waitFor(() => {
      expect(table().getByText("manual testing")).toBeInTheDocument();
    });
  });

  it("7: Other tabs (All, Live, Completed, Drafts) retain their intended behavior", async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    renderEvents("/organization/events?status=all");

    // On "All" tab, both future scheduled and past published appear
    await table().findByText("manual testing");
    expect(table().getByText("manual testing")).toBeInTheDocument();
    expect(table().getByText("webinar")).toBeInTheDocument();

    // Switch to Live tab
    const liveTab = screen.getByRole("tab", { name: /^Live/ });
    await user.click(liveTab);

    await waitFor(() => {
      expect(table().getByText("All-Hands Live Broadcast")).toBeInTheDocument();
      expect(table().queryByText("manual testing")).toBeNull();
      expect(table().queryByText("webinar")).toBeNull();
    });

    // Switch to Completed tab
    const completedTab = screen.getByRole("tab", { name: /^Completed/ });
    await user.click(completedTab);

    await waitFor(() => {
      expect(table().getByText("Product Keynote Ended")).toBeInTheDocument();
      expect(table().queryByText("manual testing")).toBeNull();
    });

    // Switch to Drafts tab
    const draftsTab = screen.getByRole("tab", { name: /^Drafts/ });
    await user.click(draftsTab);

    await waitFor(() => {
      expect(table().getByText("Q4 Planning Draft")).toBeInTheDocument();
      expect(table().queryByText("manual testing")).toBeNull();
    });
  });

  it("8: Empty results and loading/error states remain correct", async () => {
    // When an error occurs on fetch
    vi.mocked(api.get).mockRejectedValueOnce(new Error("Database connection error"));

    renderEvents("/organization/events?status=upcoming");

    await waitFor(() => {
      expect(screen.getByText("Couldn't load events")).toBeInTheDocument();
    });
  });
});
