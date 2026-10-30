// Events › All events / Calendar / Live now (ZST-UX-NAV-001 §4, §5).
//
//   * the status filter is the URL's, so the rail's "Live now", a dashboard card and Back all
//     land on the same filtered list — and "Live now" means live OR degraded
//   * the calendar shows one month from the server, in the URL, with overlapping events marked
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { useEffect } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../api", () => ({
  default: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
  errMsg: (e) => e?.message ?? "Something went wrong.",
  API_BASE: "http://api.test",
  AUTH_EXPIRED_EVENT: "zoiko:auth-expired",
}));
vi.mock("../../ui/Toast", () => ({ notify: { error: vi.fn(), success: vi.fn() } }));
vi.mock("../../auth/AuthContext", () => ({
  useAuth: () => ({ user: { id: "u1", full_name: "Ada", email: "ada@example.com", role: "org_admin" }, logout: vi.fn() }),
}));
vi.mock("../../ui/Counter", () => ({ default: ({ value }) => <>{value}</> }));

import api from "../../api";
import { ThemeProvider } from "../../theme/ThemeContext";
import Events from "./Events";

const row = (id, status, start_time, end_time = null) => ({
  id, title: `Event ${id}`, slug: id, status, display_status: status, start_time, end_time,
  visibility: "public", registration_required: false, duration_minutes: 60,
});

let calendarItems;
beforeEach(() => {
  vi.clearAllMocks();
  calendarItems = [
    row("a", "scheduled", "2026-11-10T10:00:00Z", "2026-11-10T11:00:00Z"),
    row("b", "scheduled", "2026-11-10T10:30:00Z", "2026-11-10T11:30:00Z"),
    row("c", "draft", "2026-11-20T15:00:00Z", "2026-11-20T16:00:00Z"),
  ];
  vi.mocked(api.get).mockImplementation((url, cfg) => {
    if (url === "/events/status-counts") return Promise.resolve({ data: { counts: { live: 1, degraded: 1 } } });
    if (url === "/events") {
      const p = cfg?.params || {};
      if (p.date_from) return Promise.resolve({ data: { items: calendarItems, total: calendarItems.length } });
      const items = p.display_status === "on_air" ? [row("l1", "live", "2026-10-21T10:00:00Z")] : [row("d1", "draft", null)];
      return Promise.resolve({ data: { items, total: items.length, page: 1, page_size: 10 } });
    }
    return Promise.resolve({ data: {} });
  });
});

// The router's current location, recorded after each render, for asserting on the URL.
const seen = { location: null };
function Spy() {
  const here = useLocation();
  useEffect(() => {
    seen.location = here;
  });
  return null;
}

function renderEvents(at) {
  return render(
    <ThemeProvider>
      <MemoryRouter initialEntries={[at]}>
        <Routes>
          <Route path="/organization/events" element={<><Events /><Spy /></>} />
          <Route path="/organization/events/:id" element={<h1>workspace</h1>} />
        </Routes>
      </MemoryRouter>
    </ThemeProvider>
  );
}

// The events table. Titles also appear in the side panels (Next on the schedule), so rows are
// looked up inside the table; when the table is gone (an empty filter) nothing is in it.
const table = () => within(screen.queryByRole("table") || document.createElement("div"));
const listParams = () => vi.mocked(api.get).mock.calls.filter(([u, c]) => u === "/events" && !c?.params?.date_from).at(-1)[1].params;

describe("the Live now filter", () => {
  it("is read from the URL and asks the server for every event on air", async () => {
    renderEvents("/organization/events?status=on_air");
    expect(await table().findByText("Event l1")).toBeInTheDocument();
    expect(listParams().display_status).toBe("on_air");
    expect(screen.getByRole("combobox", { name: "Filter by status" })).toHaveValue("on_air");
    expect(screen.getByRole("tab", { name: /^live/i })).toHaveAttribute("aria-selected", "true");
  });

  it("keeps an unselected tab's count legible: slate-600 on its chip (slate-500 measured 4.35:1)", async () => {
    renderEvents("/organization/events?status=on_air");
    await table().findByText("Event l1");
    expect(screen.getByRole("tab", { name: /^all/i }).querySelector(".tabular-nums")).toHaveClass("bg-slate-100", "text-slate-600");
    // The selected tab's count takes the tab's own violet-700, not the grey chip.
    expect(screen.getByRole("tab", { name: /^live/i }).querySelector(".tabular-nums")).not.toHaveClass("bg-slate-100");
  });

  it("toggles into and out of the URL, keeping nothing stale behind", async () => {
    renderEvents("/organization/events");
    await table().findByText("Event d1");
    await userEvent.click(screen.getByRole("tab", { name: /^live/i }));
    await waitFor(() => expect(seen.location.search).toBe("?status=on_air"));
    await table().findByText("Event l1");
    await userEvent.click(screen.getByRole("tab", { name: /^all/i }));
    await waitFor(() => expect(seen.location.search).toBe(""));
    expect(listParams().display_status).toBeUndefined();
  });

  it("ignores a status the platform does not have", async () => {
    renderEvents("/organization/events?status=rehearsal");
    await table().findByText("Event d1");
    expect(listParams().display_status).toBeUndefined();
  });
});

describe("the calendar", () => {
  it("shows the month in the URL from the server, and marks events that overlap", async () => {
    renderEvents("/organization/events?view=calendar&month=2026-11");
    const cal = await screen.findByRole("region", { name: /event calendar, november 2026/i });
    await within(cal).findByText("Event a");
    const call = vi.mocked(api.get).mock.calls.find(([u, c]) => u === "/events" && c?.params?.date_from);
    expect(new Date(call[1].params.date_from).getMonth()).toBe(10);
    expect(within(cal).getByText(/1 overlapping/)).toBeInTheDocument();
    expect(within(cal).getByRole("button", { name: /event a.*overlaps another event/i })).toBeInTheDocument();
    expect(within(cal).getByRole("button", { name: /event c,/i })).not.toHaveAccessibleName(/overlaps/i);
  });

  it("moves between months through the URL and opens an event in its workspace", async () => {
    renderEvents("/organization/events?view=calendar&month=2026-11");
    await screen.findByText("Event a");
    await userEvent.click(screen.getByRole("button", { name: "Next month" }));
    await waitFor(() => expect(new URLSearchParams(seen.location.search).get("month")).toBe("2026-12"));
    await screen.findByRole("region", { name: /december 2026/i });
    await userEvent.click(screen.getByRole("button", { name: "Previous month" }));
    await userEvent.click(await screen.findByRole("button", { name: /event c,/i }));
    expect(await screen.findByRole("heading", { name: "workspace" })).toBeInTheDocument();
  });

  it("switches back to the list without losing the page", async () => {
    renderEvents("/organization/events?view=calendar");
    await screen.findByRole("region", { name: /event calendar/i });
    await userEvent.click(screen.getByRole("button", { name: /^list view$/i }));
    expect(await table().findByText("Event d1")).toBeInTheDocument();
    expect(seen.location.search).toBe("");
  });
});
