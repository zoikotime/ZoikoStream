// Event statuses on the organization console: the Events list's filter and counters, and the
// lifecycle actions on one event.
//
// Every status offered is one the platform can really produce (data/events.js mirrors
// server/app/models/event.py). The list used to fetch the first 100 events and filter, count
// and page them in the browser; it now asks the server for each of those, so an organization
// with more events still gets correct numbers. The actions shown for an event are exactly the
// moves the backend's transition table allows from its status.
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../api", async (importOriginal) => {
  const real = await importOriginal();
  return { ...real, default: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() } };
});
vi.mock("../../ui/Toast", () => ({ notify: { error: vi.fn(), success: vi.fn(), info: vi.fn(), warning: vi.fn() } }));
vi.mock("../../auth/AuthContext", () => ({
  useAuth: () => ({ user: { id: "u1", full_name: "Ada", email: "ada@example.com", role: "org_admin" }, logout: vi.fn() }),
}));
// The count-up animation starts only when scrolled into view, which jsdom never is.
vi.mock("../../ui/Counter", () => ({ default: ({ value }) => <>{value}</> }));
// Sub-panels with their own data sources are not what this file is about.
vi.mock("../../components/organization/RecordingPanel", () => ({ default: () => null }));
vi.mock("../../components/organization/EventCommercial", () => ({ default: () => null }));

import api from "../../api";
import { notify } from "../../ui/Toast";
import { ThemeProvider } from "../../theme/ThemeContext";
import Events from "./Events";
import EventWorkspaceLayout from "./event/EventWorkspaceLayout";
import EventOverview from "./event/EventOverview";

const COUNTS = { draft: 12, published: 1, scheduled: 8, ready_to_arm: 0, armed: 1, live: 2, degraded: 1, ended: 3, cancelled: 2, archived: 4 };
const past = new Date(Date.now() - 86400000).toISOString();
const future = new Date(Date.now() + 3 * 86400000).toISOString();
const row = (id, status, start_time = future) => ({
  id, title: `Event ${id}`, slug: id, status, start_time, end_time: null, visibility: "public",
  registration_required: false, duration_minutes: 60, public_watch_url: `https://get.zoikostream.com/events/${id}/watch`,
});

// The events table. Titles also appear in the side panels (Next on the schedule), so rows are
// looked up inside the table; when the table is gone (an empty filter) nothing is in it.
const table = () => within(screen.queryByRole("table") || document.createElement("div"));
const listCalls = () => vi.mocked(api.get).mock.calls.filter(([url]) => url === "/events");
const lastListParams = () => listCalls().at(-1)[1].params;

function renderEvents() {
  return render(
    <ThemeProvider>
      <MemoryRouter initialEntries={["/organization/events"]}>
        <Events />
      </MemoryRouter>
    </ThemeProvider>
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.get).mockImplementation((url, cfg) => {
    if (url === "/events/status-counts") return Promise.resolve({ data: { counts: COUNTS } });
    if (url === "/events") {
      // The page filters by DISPLAY status (server/app/crud/event.py).
      const status = cfg?.params?.display_status;
      const items = status === "cancelled" ? [row("c1", "cancelled")]
        : [{ ...row("s1", "scheduled", past), display_status: "missed", start_overdue: true },
           { ...row("p1", "published", past), display_status: "published", start_overdue: true },
           row("d1", "draft", past), row("l1", "live")];
      return Promise.resolve({ data: { items, total: status ? items.length : 25, page: cfg.params.page, page_size: 10 } });
    }
    return Promise.resolve({ data: [] });
  });
});
afterEach(() => vi.useRealTimers());

describe("the Events list", () => {
  it("offers exactly the real statuses, and no retired ones", async () => {
    renderEvents();
    const select = screen.getByRole("combobox", { name: "Filter by status" });
    const options = within(select).getAllByRole("option").map((o) => o.textContent);
    expect(options).toEqual([
      "All except archived", "Live now (live or degraded)", "Upcoming (before go-live)", "Draft", "Published", "Scheduled", "Missed", "Ready to Arm", "Armed",
      "Live", "Degraded", "Ended", "Cancelled", "Archived",
    ]);
    for (const retired of ["Rehearsal", "Ending", "Processing", "Replay Ready", "Blocked"]) {
      expect(options).not.toContain(retired);
    }
  });

  it("takes the counters from the server's org-wide counts, not the loaded page", async () => {
    renderEvents();
    await table().findByText("Event s1");
    // The KPI tile and the status tabs (ORG-02) both read the server's counts.
    const tab = (name) => screen.getByRole("tab", { name: new RegExp(`^${name}`) });
    await waitFor(() => expect(within(screen.getByRole("group", { name: "Live now" })).getByText("3")).toBeInTheDocument()); // live + degraded
    expect(tab("Live")).toHaveTextContent(/^Live3$/);
    expect(tab("Upcoming")).toHaveTextContent(/^Upcoming10$/);   // published + scheduled + armed, before go-live
    expect(tab("Drafts")).toHaveTextContent(/^Drafts12$/);
    expect(tab("Completed")).toHaveTextContent(/^Completed3$/);  // ended — not cancelled, not archived
    expect(tab("All")).toHaveTextContent(/^All30$/);             // everything except archived
  });

  it("filters on the server, from the first page", async () => {
    renderEvents();
    await table().findByText("Event s1");
    expect(lastListParams().display_status).toBeUndefined();  // all except archived
    expect(lastListParams().status).toBeUndefined();
    fireEvent.change(screen.getByRole("combobox", { name: "Filter by status" }), { target: { value: "cancelled" } });
    await table().findByText("Event c1");
    expect(lastListParams()).toMatchObject({ display_status: "cancelled", page: 1 });
    expect(table().queryByText("Event s1")).toBeNull();
  });

  it("pages through the whole organization on the server", async () => {
    renderEvents();
    await table().findByText("Event s1");
    fireEvent.click(screen.getByRole("button", { name: "2" }));          // 25 events, 10 a page
    await waitFor(() => expect(lastListParams().page).toBe(2));
  });

  it("shows a Scheduled event that never went live as Missed — one status, no second warning", async () => {
    renderEvents();
    const missed = (await table().findByText("Event s1")).closest("tr");
    expect(within(missed).getByText("Missed")).toBeInTheDocument();
    expect(within(missed).queryByText("Scheduled")).toBeNull();
    expect(within(missed).queryByText("Start time passed")).toBeNull();
    // Other pre-live statuses keep their own badge and note the passed start, as the API says.
    const published = table().getByText("Event p1").closest("tr");
    expect(within(published).getByText("Published")).toBeInTheDocument();
    expect(within(published).getByText("Start time passed")).toBeInTheDocument();
    // A draft's old tentative time is never a missed start.
    expect(within(table().getByText("Event d1").closest("tr")).queryByText("Start time passed")).toBeNull();
    expect(within(table().getByText("Event d1").closest("tr")).getByText("Draft")).toBeInTheDocument();
  });

  it("re-reads statuses while open, so a host going live shows up without a reload", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    renderEvents();
    await table().findByText("Event s1");
    const before = listCalls().length;
    await act(async () => { await vi.advanceTimersByTimeAsync(15000); });
    expect(listCalls().length).toBeGreaterThan(before);
  });
});

// ── one event ───────────────────────────────────────────────────────────────────────────

function renderEvent(event) {
  vi.mocked(api.get).mockImplementation((url) => {
    if (url === `/events/${event.id}`) return Promise.resolve({ data: event });
    if (url.endsWith("/recordings")) return Promise.resolve({ data: [] });
    return Promise.resolve({ data: [] });
  });
  vi.mocked(api.patch).mockResolvedValue({ data: {} });
  vi.mocked(api.post).mockResolvedValue({ data: {} });
  return render(
    <ThemeProvider>
      <MemoryRouter initialEntries={[`/organization/events/${event.id}`]}>
        <Routes>
          <Route path="/organization/events/:id" element={<EventWorkspaceLayout />}>
            <Route index element={<EventOverview />} />
          </Route>
        </Routes>
      </MemoryRouter>
    </ThemeProvider>
  );
}

const ACTIONS = ["Publish", "Mark Ready to Arm", "Arm Event", "Mark Not Ready", "Disarm", "End Event",
                 "Cancel Event", "Archive", "Unarchive"];
async function actionsFor(status, start_time = future) {
  renderEvent({ ...row("e1", status, start_time), hosts: [] });
  await screen.findByRole("button", { name: "Copy Viewer Link" });
  return ACTIONS.filter((name) => screen.queryByRole("button", { name }));
}

describe("the actions an event offers are exactly its legal moves", () => {
  it.each([
    ["draft", ["Publish", "Cancel Event", "Archive"]],
    ["published", ["Mark Ready to Arm", "Cancel Event"]],
    ["scheduled", ["Mark Ready to Arm", "Cancel Event"]],
    ["ready_to_arm", ["Arm Event", "Mark Not Ready", "Cancel Event"]],
    ["armed", ["Disarm", "Cancel Event"]],
    ["live", ["End Event"]],
    ["degraded", ["End Event"]],
    ["ended", ["Archive"]],
    ["cancelled", ["Archive"]],
    ["archived", ["Unarchive"]],
  ])("%s", async (status, expected) => {
    expect(await actionsFor(status)).toEqual(expected);
  });
});

describe("each action sends the move the server expects", () => {
  it("Publish schedules a draft that already has a future start", async () => {
    await actionsFor("draft", future);
    fireEvent.click(screen.getByRole("button", { name: "Publish" }));
    await waitFor(() => expect(api.patch).toHaveBeenCalledWith("/events/e1", { status: "scheduled" }));
  });

  it("Publish without a future start publishes", async () => {
    await actionsFor("draft", null);
    fireEvent.click(screen.getByRole("button", { name: "Publish" }));
    await waitFor(() => expect(api.patch).toHaveBeenCalledWith("/events/e1", { status: "published" }));
  });

  it("Disarm steps back to ready to arm", async () => {
    await actionsFor("armed");
    fireEvent.click(screen.getByRole("button", { name: "Disarm" }));
    await waitFor(() => expect(api.patch).toHaveBeenCalledWith("/events/e1", { status: "ready_to_arm" }));
  });

  it("Cancel asks first, then cancels", async () => {
    await actionsFor("scheduled");
    fireEvent.click(screen.getByRole("button", { name: "Cancel Event" }));
    // Backing out of the confirmation changes nothing.
    fireEvent.click(await screen.findByRole("button", { name: "Cancel" }));
    expect(api.patch).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Cancel Event" }));
    fireEvent.click(await screen.findByRole("button", { name: "Yes, cancel it" }));
    await waitFor(() => expect(api.patch).toHaveBeenCalledWith("/events/e1", { status: "cancelled" }));
  });

  it("a double click sends the move once", async () => {
    let settle;
    await actionsFor("armed");
    vi.mocked(api.patch).mockImplementation(() => new Promise((resolve) => { settle = resolve; }));
    const disarm = screen.getByRole("button", { name: "Disarm" });
    fireEvent.click(disarm);
    fireEvent.click(disarm);
    expect(api.patch).toHaveBeenCalledTimes(1);
    await act(async () => settle({ data: {}, headers: { "x-request-id": "req_abc" } }));
    await waitFor(() => expect(notify.success).toHaveBeenCalledWith("Event updated · Ref req_abc"));
  });

  it("Archive and Unarchive use their own endpoints", async () => {
    await actionsFor("ended");
    fireEvent.click(screen.getByRole("button", { name: "Archive" }));
    await waitFor(() => expect(api.post).toHaveBeenCalledWith("/events/e1/archive"));
  });

  it("a refused move shows the server's reason and claims nothing", async () => {
    await actionsFor("ready_to_arm");
    vi.mocked(api.patch).mockRejectedValueOnce({ response: { status: 400, data: { detail: "Cannot arm — readiness checks have not passed: capacity not reserved" } } });
    fireEvent.click(screen.getByRole("button", { name: "Arm Event" }));
    // Arming is high-impact: it asks first.
    expect(api.patch).not.toHaveBeenCalled();
    fireEvent.click(await screen.findByRole("button", { name: "Yes, arm it" }));
    await waitFor(() => expect(notify.error).toHaveBeenCalledWith("Cannot arm — readiness checks have not passed: capacity not reserved"));
    expect(notify.success).not.toHaveBeenCalled();
  });
});
