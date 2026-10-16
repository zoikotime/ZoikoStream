// "No sessions in this window" on /organization/sessions.
//
// The Recent sessions table keeps a 720px minimum width, so on a phone it scrolls sideways, and
// its empty state was centred across the FULL table width: on a 360px screen the icon sat off to
// the right, the message was clipped, and the visible column was blank space between the header
// row and a cut-off sentence. The page now passes `pinEmpty`, which sizes the empty state to the
// scroller's visible width. jsdom has no layout; the geometry was measured in Chromium (mobile
// build 320–768px: fully visible, same vertical spacing; web 1280/1440: identical positions).
import { configure, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { FiActivity } from "react-icons/fi";

configure({ asyncUtilTimeout: 8000 });
vi.setConfig({ testTimeout: 30000 });

vi.mock("../../api", () => ({
  default: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
  errMsg: (e) => e?.message ?? "Something went wrong.",
}));

vi.mock("../../auth/AuthContext", () => ({
  useAuth: () => ({ user: { full_name: "Vihari Owner", email: "owner@example.com" }, logout: vi.fn() }),
}));

import api from "../../api";
import { ThemeProvider } from "../../theme/ThemeContext";
import DataTable from "../../components/admin/DataTable";
import StreamingSessions from "./StreamingSessions";

const SESSIONS = [
  { id: "s-1", event_id: "e-1", title: "Bravo keynote", mode: "live", state: "Healthy",
    started_at: new Date(Date.now() - 30 * 60_000).toISOString(), ended_at: null, peak_viewers: 40 },
];
const overview = (items) => ({ organization: { name: "Northwind" }, sessions: { items } });

let items;
beforeEach(() => {
  vi.clearAllMocks();
  items = [];
  vi.mocked(api.get).mockImplementation((url) =>
    Promise.resolve({ data: url === "/organization/overview" ? overview(items) : {} })
  );
});

const renderPage = () =>
  render(
    <ThemeProvider>
      <MemoryRouter>
        <StreamingSessions />
      </MemoryRouter>
    </ThemeProvider>
  );

const classesOf = (el) => (el.getAttribute("class") || "").split(/\s+/);
const scrollerOf = (el) => el.closest("table").parentElement;

describe("Recent sessions with no sessions", () => {
  it("shows the whole empty state: icon, title, description and the Live Events action", async () => {
    renderPage();
    const title = await screen.findByText("No sessions in this window");
    const block = title.parentElement;
    expect(block.querySelector("svg")).not.toBeNull();
    expect(within(block).getByText(/Nothing has been broadcast in the selected range/)).toBeInTheDocument();
    expect(within(block).getByRole("link", { name: "Live Events" })).toHaveAttribute("href", "/organization/events");
  });

  it("keeps its spacing: py-12 cell, mb-5 icon, mt-1.5 description, mt-5 action", async () => {
    renderPage();
    const block = (await screen.findByText("No sessions in this window")).parentElement;
    expect(classesOf(block.closest("td"))).toEqual(expect.arrayContaining(["px-4", "py-12"]));
    expect(classesOf(block)).toEqual(expect.arrayContaining(["mx-auto", "max-w-sm"]));
    const [icon, , description, action] = block.children;
    expect(classesOf(icon)).toContain("mb-5");
    expect(classesOf(description)).toContain("mt-1.5");
    expect(classesOf(action)).toContain("mt-5");
  });

  it("is pinned to the visible width of the sideways-scrolling table", async () => {
    renderPage();
    const block = (await screen.findByText("No sessions in this window")).parentElement;
    expect(classesOf(block.parentElement)).toEqual(
      expect.arrayContaining(["sticky", "left-4", "w-[calc(100cqw-2rem)]"])
    );
    expect(classesOf(scrollerOf(block))).toEqual(expect.arrayContaining(["overflow-auto", "@container"]));
  });
});

describe("Recent sessions with sessions", () => {
  it("renders the rows with the table's original markup: no pin, no container", async () => {
    items = SESSIONS;
    renderPage();
    const cell = await screen.findByText("Bravo keynote");
    expect(screen.queryByText("No sessions in this window")).not.toBeInTheDocument();
    expect(scrollerOf(cell).getAttribute("class")).toBe("overflow-auto");
    expect(cell.closest("table").querySelector("div.sticky")).toBeNull();
  });
});

describe("other tables (pinEmpty not passed)", () => {
  it("render their empty state exactly as before", () => {
    render(
      <DataTable
        columns={[{ key: "name", header: "Name" }]}
        rows={[]}
        rowKey={(r) => r.id}
        empty={{ icon: FiActivity, title: "Nothing here", description: "Still nothing." }}
      />
    );
    const block = screen.getByText("Nothing here").parentElement;
    expect(block.parentElement.tagName).toBe("TD");          // no wrapper between cell and block
    expect(scrollerOf(block).getAttribute("class")).toBe("overflow-auto");
  });
});
