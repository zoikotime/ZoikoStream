// Organization › Events › All Events: the "Events" crumb goes to the canonical listing.
//
// Production report: on Organisation → Events → All Events, clicking the underlined "Events"
// crumb did nothing. Its link is /organization/events — the URL already open — and a
// navigation to the open URL changes nothing a page holds in its own state: the search box,
// the page of results and the sort stayed exactly as they were. The crumb now also resets
// those, and refetches, so it lands on the listing as the route defines it from anywhere.
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useEffect } from "react";
import { MemoryRouter, Route, Routes, useLocation, useNavigate, useNavigationType } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

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
vi.mock("../../ui/Counter", () => ({ default: ({ value }) => <>{value}</> }));

import api from "../../api";
import { ThemeProvider } from "../../theme/ThemeContext";
import Events from "./Events";

const row = (i) => ({
  id: `e${i}`, title: `Event ${i}`, slug: `e${i}`, status: "draft", display_status: "draft",
  start_time: null, end_time: null, visibility: "public", registration_required: false, duration_minutes: 60,
});

beforeEach(() => {
  vi.clearAllMocks();
  Element.prototype.scrollIntoView = vi.fn();
  vi.mocked(api.get).mockImplementation((url, cfg) => {
    if (url === "/events/status-counts") return Promise.resolve({ data: { counts: { draft: 25 } } });
    if (url === "/events") {
      const p = cfg?.params || {};
      if (p.date_from) return Promise.resolve({ data: { items: [], total: 0 } });
      const page = p.page || 1;
      return Promise.resolve({ data: { items: Array.from({ length: 10 }, (_, k) => row((page - 1) * 10 + k + 1)), total: 25, page, page_size: 10 } });
    }
    return Promise.resolve({ data: { items: [], total: 0 } });
  });
});

// The router's view of things, for asserting on the URL and on how it got there.
const seen = { location: null, type: null };
function Spy() {
  const location = useLocation();
  const type = useNavigationType();
  useEffect(() => {
    seen.location = location;
    seen.type = type;
  });
  return null;
}
function BackButton() {
  const navigate = useNavigate();
  return <button type="button" onClick={() => navigate(-1)}>test: browser back</button>;
}

function renderAt(...entries) {
  return render(
    <ThemeProvider>
      <MemoryRouter initialEntries={entries} initialIndex={entries.length - 1}>
        <Routes>
          <Route path="/organization/events" element={<><Events /><Spy /><BackButton /></>} />
          <Route path="/organization/manage" element={<h1>organization hub</h1>} />
        </Routes>
      </MemoryRouter>
    </ThemeProvider>
  );
}

const crumbs = () => screen.getByRole("navigation", { name: "Breadcrumb" });
const eventsCrumb = () => within(crumbs()).getByRole("link", { name: "Events" });
const listCalls = () => vi.mocked(api.get).mock.calls.filter(([u, c]) => u === "/events" && !c?.params?.date_from);
const lastListParams = () => listCalls().at(-1)[1].params;
const search = () => screen.getByPlaceholderText("Search events by name…");

describe("the trail", () => {
  it("links Events and Organization to their routes; All Events is the current page, not a link", async () => {
    renderAt("/organization/events");
    await screen.findByText("Event 1");
    expect(eventsCrumb()).toHaveAttribute("href", "/organization/events");
    expect(within(crumbs()).getByRole("link", { name: "Organization" })).toHaveAttribute("href", "/organization/manage");
    expect(within(crumbs()).queryByRole("link", { name: "All Events" })).toBeNull();
    expect(within(crumbs()).getByText("All Events").closest("li")).toHaveAttribute("aria-current", "page");
  });

  it("Organization still goes to the organization hub", async () => {
    const u = userEvent.setup();
    renderAt("/organization/events");
    await screen.findByText("Event 1");
    await u.click(within(crumbs()).getByRole("link", { name: "Organization" }));
    expect(await screen.findByRole("heading", { name: "organization hub" })).toBeInTheDocument();
  });
});

describe("Events, from the listing itself", () => {
  it("returns a searched, paged, re-sorted listing to the canonical one", async () => {
    const u = userEvent.setup();
    renderAt("/organization/events");
    await screen.findByText("Event 1");

    await u.type(search(), "town");
    await waitFor(() => expect(lastListParams()).toMatchObject({ q: "town" }));
    fireEvent.change(screen.getByRole("combobox", { name: "Sort" }), { target: { value: "title:asc" } });
    await waitFor(() => expect(lastListParams()).toMatchObject({ sort_by: "title", order: "asc" }));
    await u.click(screen.getByRole("button", { name: "Next page" }));
    await waitFor(() => expect(lastListParams()).toMatchObject({ page: 2 }));

    const before = listCalls().length;
    await u.click(eventsCrumb());

    expect(search()).toHaveValue("");
    await waitFor(() => expect(listCalls().length).toBeGreaterThan(before));
    const p = lastListParams();
    expect(p).toMatchObject({ page: 1, sort_by: "start_time", order: "desc" });
    expect(p.q).toBeUndefined();
    expect(p.display_status).toBeUndefined();
    expect(screen.getByRole("combobox", { name: "Sort" })).toHaveValue("start_time:desc");
    expect(seen.location.pathname).toBe("/organization/events");
    expect(seen.location.search).toBe("");
    // The URL was already this one: replaced, not pushed, so Back is not cluttered with a copy.
    expect(seen.type).toBe("REPLACE");
    // Several real round trips plus the page's own 300ms search debounce: under a parallel run
    // this legitimately takes longer than the 5s default.
  }, 15_000);

  it("on the untouched listing, fetches it fresh rather than doing nothing", async () => {
    const u = userEvent.setup();
    renderAt("/organization/events");
    await screen.findByText("Event 1");
    const before = listCalls().length;
    await u.click(eventsCrumb());
    await waitFor(() => expect(listCalls().length).toBe(before + 1));
    expect(lastListParams()).toMatchObject({ page: 1, sort_by: "start_time", order: "desc" });
  });

  it("leaves a modified click (new tab or window) to the browser", async () => {
    const u = userEvent.setup();
    renderAt("/organization/events");
    await screen.findByText("Event 1");
    await u.type(search(), "town");
    act(() => {
      fireEvent.click(eventsCrumb(), { ctrlKey: true });
    });
    expect(search()).toHaveValue("town");
  });
});

describe("Events, from a narrowed URL", () => {
  it("from Calendar shows All Events, and Back returns to the calendar", async () => {
    const u = userEvent.setup();
    renderAt("/organization/events?view=calendar");
    await within(crumbs()).findByText("Calendar");

    await u.click(eventsCrumb());
    expect(await within(crumbs()).findByText("All Events")).toBeInTheDocument();
    expect(seen.location.search).toBe("");
    expect(seen.type).toBe("PUSH");

    await u.click(screen.getByRole("button", { name: "test: browser back" }));
    expect(await within(crumbs()).findByText("Calendar")).toBeInTheDocument();
    expect(seen.location.search).toBe("?view=calendar");
  });

  it("from Live now clears the status filter", async () => {
    const u = userEvent.setup();
    renderAt("/organization/events?status=on_air");
    await waitFor(() => expect(screen.getByRole("combobox", { name: "Filter by status" })).toHaveValue("on_air"));
    await u.click(eventsCrumb());
    await waitFor(() => expect(screen.getByRole("combobox", { name: "Filter by status" })).toHaveValue("all"));
    expect(lastListParams().display_status).toBeUndefined();
  });

  it("after Create Event is closed, Events does not bring the dialog back", async () => {
    const u = userEvent.setup();
    renderAt("/organization/events?create=true");
    const dialog = await screen.findByRole("dialog", { name: "Create Event" });
    await u.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());

    await u.click(eventsCrumb());
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(seen.location.search).toBe("");
  });
});
