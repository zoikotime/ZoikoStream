// "Missed": a Scheduled event whose start passed (plus a grace period) without it ever going
// live. The API derives it (server/app/crud/event.py) and returns it as `display_status`; the
// console shows that, and never re-derives it from the browser clock. The canonical status
// stays "scheduled", so every action still applies.
import { act, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
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
  useAuth: () => ({ user: { id: "u1", full_name: "Ada", email: "ada@example.com", role: "org_admin" }, logout: vi.fn() }),
}));
// The count-up animation starts only when scrolled into view, which jsdom never is.
vi.mock("../../ui/Counter", () => ({ default: ({ value }) => <>{value}</> }));
vi.mock("../../components/organization/RecordingPanel", () => ({ default: () => null }));
vi.mock("../../components/organization/EventCommercial", () => ({ default: () => null }));

import api from "../../api";
import { EVENT_STATUS, STATUS_FILTER_OPTIONS, displayStatusOf, statusMeta } from "../../data/events";
import { ThemeProvider } from "../../theme/ThemeContext";
import Events from "./Events";
import EventWorkspaceLayout from "./event/EventWorkspaceLayout";
import EventOverview from "./event/EventOverview";

const past = new Date(Date.now() - 3 * 3600_000).toISOString();
const future = new Date(Date.now() + 3 * 86400_000).toISOString();
const row = (id, status, extra = {}) => ({
  id, title: `Event ${id}`, slug: id, status, start_time: future, end_time: null, visibility: "public",
  registration_required: false, duration_minutes: 60, public_watch_url: `https://example.test/${id}`,
  display_status: status, start_overdue: false, ...extra,
});
const MISSED = row("m1", "scheduled", { start_time: past, display_status: "missed", start_overdue: true });

let listed;
const listCalls = () => vi.mocked(api.get).mock.calls.filter(([url]) => url === "/events");
const lastParams = () => listCalls().at(-1)[1].params;

beforeEach(() => {
  vi.clearAllMocks();
  listed = [row("s1", "scheduled"), MISSED, row("e1", "ended", { start_time: past })];
  vi.mocked(api.get).mockImplementation((url, cfg) => {
    if (url === "/events/status-counts") {
      return Promise.resolve({ data: {
        counts: { draft: 0, published: 0, scheduled: 9, ready_to_arm: 0, armed: 0, live: 0, degraded: 0, ended: 2, cancelled: 0, archived: 0 },
        display_counts: { draft: 0, published: 0, scheduled: 4, missed: 5, ready_to_arm: 0, armed: 0, live: 0, degraded: 0, ended: 2, cancelled: 0, archived: 0 },
      } });
    }
    if (url === "/events") {
      const want = cfg?.params?.display_status;
      const items = want ? listed.filter((e) => displayStatusOf(e) === want) : listed;
      return Promise.resolve({ data: { items, total: items.length, page: 1, page_size: 10 } });
    }
    return Promise.resolve({ data: [] });
  });
});
afterEach(() => vi.useRealTimers());

const renderEvents = () => render(
  <ThemeProvider><MemoryRouter initialEntries={["/organization/events"]}><Events /></MemoryRouter></ThemeProvider>,
);
// The events table. Titles also appear in the side panels (Next on the schedule), so rows are
// looked up inside the table; when the table is gone (an empty filter) nothing is in it.
const table = () => within(screen.queryByRole("table") || document.createElement("div"));
const badgeIn = (title) => table().getByText(title).closest("tr");

describe("the Events table", () => {
  it("shows Missed for a past, never-started Scheduled event, Scheduled for a future one", async () => {
    renderEvents();
    await table().findByText("Event m1");
    expect(within(badgeIn("Event m1")).getByText("Missed")).toBeInTheDocument();
    expect(within(badgeIn("Event m1")).queryByText("Start time passed")).toBeNull();
    expect(within(badgeIn("Event s1")).getByText("Scheduled")).toBeInTheDocument();
    // An event that actually ran keeps its real outcome.
    expect(within(badgeIn("Event e1")).getByText("Ended")).toBeInTheDocument();
  });

  it("counts only upcoming events as Scheduled", async () => {
    renderEvents();
    await table().findByText("Event m1");
    // 9 canonical scheduled = 4 upcoming + 5 missed; the Upcoming tab counts the upcoming 4.
    await waitFor(() =>
      expect(screen.getByRole("tab", { name: /^Upcoming/ })).toHaveTextContent(/^Upcoming4$/));
  });

  it("offers a Missed filter, right after Scheduled, that asks the server for missed events", async () => {
    const user = userEvent.setup();
    renderEvents();
    await table().findByText("Event m1");
    const select = screen.getByRole("combobox", { name: "Filter by status" });
    const options = within(select).getAllByRole("option").map((o) => o.value);
    expect(options.indexOf("missed")).toBe(options.indexOf("scheduled") + 1);
    await user.selectOptions(select, "missed");
    await waitFor(() => expect(lastParams()).toMatchObject({ display_status: "missed", page: 1 }));
    await waitFor(() => expect(table().queryByText("Event s1")).toBeNull());
    expect(table().getByText("Event m1")).toBeInTheDocument();
    await user.selectOptions(select, "scheduled");
    await waitFor(() => expect(lastParams()).toMatchObject({ display_status: "scheduled" }));
  });

  it("moves an event from Scheduled to Missed on the next refresh, with no reload", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    listed = [row("t1", "scheduled", { start_time: new Date(Date.now() - 60_000).toISOString() })];
    renderEvents();
    await table().findByText("Event t1");
    expect(within(badgeIn("Event t1")).getByText("Scheduled")).toBeInTheDocument();
    // The grace period ran out; the server now reports it Missed.
    listed = [{ ...listed[0], display_status: "missed", start_overdue: true }];
    await act(async () => { await vi.advanceTimersByTimeAsync(15_000); });
    await waitFor(() => expect(within(badgeIn("Event t1")).getByText("Missed")).toBeInTheDocument());
  });
});

describe("the event page", () => {
  it("shows Missed in its badge while every action still works from the real status", async () => {
    vi.mocked(api.get).mockImplementation((url) => {
      if (url === `/events/${MISSED.id}`) return Promise.resolve({ data: MISSED });
      if (url.endsWith("/recordings")) return Promise.resolve({ data: [] });
      return Promise.resolve({ data: [] });
    });
    render(
      <ThemeProvider>
        <MemoryRouter initialEntries={[`/organization/events/${MISSED.id}`]}>
          <Routes>
            <Route path="/organization/events/:id" element={<EventWorkspaceLayout />}>
              <Route index element={<EventOverview />} />
            </Route>
          </Routes>
        </MemoryRouter>
      </ThemeProvider>,
    );
    expect(await screen.findAllByText("Missed")).not.toHaveLength(0);
  });
});

describe("the status vocabulary", () => {
  it("never offers Missed as a status anyone can set", () => {
    expect(EVENT_STATUS).not.toHaveProperty("missed");
    expect(statusMeta("missed")).toMatchObject({ label: "Missed", tone: "warning" });
    expect(STATUS_FILTER_OPTIONS.map(([k]) => k)).toContain("missed");
  });

  it("falls back to the canonical status when the API sends no display status", () => {
    expect(displayStatusOf({ status: "scheduled" })).toBe("scheduled");
    expect(displayStatusOf({ status: "scheduled", display_status: "missed" })).toBe("missed");
  });
});
