// Recent sessions sorting on /organization/sessions. An empty table cannot be sorted at all; with
// rows, at most one column is ever active and every indicator (arrow, highlight, aria-sort)
// reads the same state; and that state goes back to neutral whenever the list empties.
//
// The bug this pins: over "No sessions in this window" the headers still toggled, and the
// arrows could disagree with each other and with the order actually shown.
import { configure, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

// The first render of the full page is slow when the whole suite runs in parallel; the same
// allowance AudienceAccess.test.jsx makes, so a cold start is not reported as a failure.
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
import StreamingSessions from "./StreamingSessions";

const ago = (mins) => new Date(Date.now() - mins * 60_000).toISOString();

// Delivered newest first, an order that matches no column's sort in either direction, so a
// table showing it is provably unsorted.
const SESSIONS = [
  {
    id: "s-bravo", event_id: "e-bravo", title: "Bravo keynote", mode: "live", state: "Healthy",
    started_at: ago(30), ended_at: null, peak_viewers: 40,
  },
  {
    id: "s-alpha", event_id: "e-alpha", title: "Alpha workshop", mode: "paused", state: "Paused",
    started_at: ago(600), ended_at: null, peak_viewers: 0,
  },
  // Started first but ran for an hour: sorting by duration and by start time disagree here.
  {
    id: "s-charlie", event_id: "e-charlie", title: "Charlie panel", mode: "ended", state: "Archived",
    started_at: ago(720), ended_at: ago(660), peak_viewers: 120,
  },
];
const SERVER_ORDER = ["Bravo keynote", "Alpha workshop", "Charlie panel"];
const COLUMNS = ["Session", "Mode", "State", "Duration", "Peak audience"];

const overview = (items) => ({
  organization: { name: "Northwind" },
  sessions: {
    active: 0, live: 0, paused: 0, starting_soon: 0, current_audience: null, peak_audience: null,
    items, breakdown_note: "Self-service vs managed classification is not modelled.",
  },
});

let byRange;
beforeEach(() => {
  vi.clearAllMocks();
  byRange = { "24h": SESSIONS, "7d": [], "30d": SESSIONS };
  vi.mocked(api.get).mockImplementation((url, config) =>
    Promise.resolve({ data: url === "/organization/overview" ? overview(byRange[config?.params?.range]) : {} })
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

const header = (name) => screen.getByRole("button", { name });
const th = (name) => header(name).closest("th");
const titles = () =>
  [...document.querySelectorAll("tbody tr")].map((tr) => tr.querySelector("td p")?.textContent).filter(Boolean);
const ariaSorted = () => [...document.querySelectorAll("th[aria-sort]")];
const highlighted = () => COLUMNS.filter((c) => header(c).className.includes("text-violet-700"));
// The resting chevron is the faint slate one; an active arrow is full contrast.
const activeArrows = () =>
  COLUMNS.filter((c) => !(header(c).querySelector("svg").getAttribute("class") || "").includes("text-slate-300"));
// Feather's chevrons differ only in their polyline; the up chevron is the ascending arrow.
const arrowDir = (c) =>
  header(c).querySelector("polyline").getAttribute("points") === "18 15 12 9 6 15" ? "asc" : "desc";

const showsRows = () => waitFor(() => expect(titles()).toHaveLength(SESSIONS.length));
const showsEmpty = () => screen.findByText("No sessions in this window");

// Exactly one column reads as sorted, by every indicator, in the given direction.
function expectOnlyActive(column, dir) {
  expect(ariaSorted()).toEqual([th(column)]);
  expect(th(column)).toHaveAttribute("aria-sort", dir === "asc" ? "ascending" : "descending");
  expect(highlighted()).toEqual([column]);
  expect(activeArrows()).toEqual([column]);
  expect(arrowDir(column)).toBe(dir);
}

function expectNeutral() {
  expect(ariaSorted()).toEqual([]);
  expect(highlighted()).toEqual([]);
  expect(activeArrows()).toEqual([]);
}

describe("Recent sessions with zero rows", () => {
  beforeEach(() => {
    byRange["24h"] = [];
  });

  it("clicking Session does nothing", async () => {
    renderPage();
    await showsEmpty();
    fireEvent.click(header("Session"));
    expectNeutral();
  });

  it("clicking Mode does nothing", async () => {
    renderPage();
    await showsEmpty();
    fireEvent.click(header("Mode"));
    expectNeutral();
  });

  it("keeps every header visible but disabled, with no active indicator", async () => {
    renderPage();
    await showsEmpty();
    for (const column of COLUMNS) {
      const button = header(column);
      expect(button).toBeVisible();
      expect(button).toBeDisabled();
      expect(button).toHaveAttribute("aria-disabled", "true");
      expect(button).not.toHaveAttribute("title");
      expect(button.className).toContain("cursor-default");
      expect(button.className).not.toMatch(/hover:|group\/sort/);
    }
    expectNeutral();
    expect(screen.getByText(/nothing has been broadcast in the selected range/i)).toBeInTheDocument();
  });

  it("repeated clicks never toggle, not even a hidden state that shows once rows arrive", async () => {
    const user = userEvent.setup();
    renderPage();
    await showsEmpty();
    for (let i = 0; i < 3; i += 1) for (const column of COLUMNS) fireEvent.click(header(column));
    expectNeutral();

    await user.click(screen.getByRole("button", { name: "30 days" }));
    await showsRows();
    expect(titles()).toEqual(SERVER_ORDER);
    expectNeutral();
  });

  it("the keyboard can neither reach nor activate a sort header", async () => {
    const user = userEvent.setup();
    renderPage();
    await showsEmpty();
    const sortHeaders = COLUMNS.map(header);

    header("Session").focus();
    expect(header("Session")).not.toHaveFocus();
    for (let i = 0; i < 25; i += 1) {
      await user.tab();
      expect(sortHeaders).not.toContain(document.activeElement);
    }
    fireEvent.keyDown(header("Session"), { key: "Enter" });
    fireEvent.keyUp(header("Session"), { key: " " });
    expectNeutral();
  });
});

describe("Recent sessions with rows", () => {
  it("starts neutral, in the server's order, with enabled headers", async () => {
    renderPage();
    await showsRows();
    expect(titles()).toEqual(SERVER_ORDER);
    expectNeutral();
    for (const column of COLUMNS) {
      expect(header(column)).toBeEnabled();
      expect(header(column)).toHaveAttribute("aria-disabled", "false");
      expect(header(column)).toHaveAttribute("title", `Sort by ${column}`);
    }
  });

  it("sorts by the clicked column, ascending first", async () => {
    const user = userEvent.setup();
    renderPage();
    await showsRows();
    await user.click(header("Session"));
    expect(titles()).toEqual(["Alpha workshop", "Bravo keynote", "Charlie panel"]);
    expectOnlyActive("Session", "asc");
  });

  it("the same column toggles between ascending and descending", async () => {
    const user = userEvent.setup();
    renderPage();
    await showsRows();
    await user.click(header("Session"));
    expectOnlyActive("Session", "asc");
    await user.click(header("Session"));
    expect(titles()).toEqual(["Charlie panel", "Bravo keynote", "Alpha workshop"]);
    expectOnlyActive("Session", "desc");
    await user.click(header("Session"));
    expect(titles()).toEqual(["Alpha workshop", "Bravo keynote", "Charlie panel"]);
    expectOnlyActive("Session", "asc");
  });

  it("another column takes over and clears the previous one", async () => {
    const user = userEvent.setup();
    renderPage();
    await showsRows();
    await user.click(header("Session"));
    await user.click(header("Session"));
    await user.click(header("Peak audience"));
    expect(titles()).toEqual(["Alpha workshop", "Bravo keynote", "Charlie panel"]);
    expectOnlyActive("Peak audience", "asc");
    expect(th("Session")).not.toHaveAttribute("aria-sort");
    expect(arrowDir("Session")).toBe("desc"); // the resting chevron, not an ascending arrow
  });

  it("never shows more than one active header", async () => {
    const user = userEvent.setup();
    renderPage();
    await showsRows();
    const clicks = ["Mode", "State", "State", "Duration", "Session", "Peak audience", "Peak audience", "Mode"];
    const expected = ["asc", "asc", "desc", "asc", "asc", "asc", "desc", "asc"];
    for (const [i, column] of clicks.entries()) {
      await user.click(header(column));
      expectOnlyActive(column, expected[i]);
    }
  });

  it("orders Duration by length, not by start time", async () => {
    const user = userEvent.setup();
    renderPage();
    await showsRows();
    await user.click(header("Duration"));
    expect(titles()).toEqual(["Bravo keynote", "Charlie panel", "Alpha workshop"]);
    await user.click(header("Duration"));
    expect(titles()).toEqual(["Alpha workshop", "Charlie panel", "Bravo keynote"]);
  });

  it("orders Mode and State by their labels", async () => {
    const user = userEvent.setup();
    renderPage();
    await showsRows();
    await user.click(header("Mode"));
    expect(titles()).toEqual(["Charlie panel", "Bravo keynote", "Alpha workshop"]);
    await user.click(header("State"));
    expect(titles()).toEqual(["Charlie panel", "Bravo keynote", "Alpha workshop"]);
    await user.click(header("State"));
    expect(titles()).toEqual(["Alpha workshop", "Bravo keynote", "Charlie panel"]);
  });

  it("Enter and Space activate a header", async () => {
    const user = userEvent.setup();
    renderPage();
    await showsRows();
    header("Session").focus();
    expect(header("Session")).toHaveFocus();
    await user.keyboard("{Enter}");
    expectOnlyActive("Session", "asc");
    await user.keyboard(" ");
    expectOnlyActive("Session", "desc");
  });
});

describe("Recent sessions when the rows go away", () => {
  it("a range with no sessions resets the sort, and the next rows start neutral", async () => {
    const user = userEvent.setup();
    renderPage();
    await showsRows();
    await user.click(header("Session"));
    await user.click(header("Session"));
    expectOnlyActive("Session", "desc");

    await user.click(screen.getByRole("button", { name: "7 days" }));
    await showsEmpty();
    expectNeutral();
    expect(header("Session")).toBeDisabled();

    await user.click(screen.getByRole("button", { name: "30 days" }));
    await showsRows();
    expect(titles()).toEqual(SERVER_ORDER);
    expectNeutral();
  });

  it("a refresh that comes back empty resets it too", async () => {
    const user = userEvent.setup();
    renderPage();
    await showsRows();
    await user.click(header("Mode"));
    expectOnlyActive("Mode", "asc");

    byRange["24h"] = [];
    await user.click(screen.getByRole("button", { name: "Refresh" }));
    await showsEmpty();
    expectNeutral();

    byRange["24h"] = SESSIONS;
    await waitFor(() => expect(screen.getByRole("button", { name: "Refresh" })).toBeEnabled());
    await user.click(screen.getByRole("button", { name: "Refresh" }));
    await showsRows();
    expect(titles()).toEqual(SERVER_ORDER);
    expectNeutral();
  });

  it("a refresh that still has rows keeps the chosen sort", async () => {
    const user = userEvent.setup();
    renderPage();
    await showsRows();
    await user.click(header("Duration"));
    await user.click(screen.getByRole("button", { name: "Refresh" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Refresh" })).toBeEnabled());
    await showsRows();
    expect(titles()).toEqual(["Bravo keynote", "Charlie panel", "Alpha workshop"]);
    expectOnlyActive("Duration", "asc");
  });

  it("reloading the page starts neutral", async () => {
    const user = userEvent.setup();
    const { unmount } = renderPage();
    await showsRows();
    await user.click(header("Peak audience"));
    expectOnlyActive("Peak audience", "asc");
    unmount();

    renderPage();
    await showsRows();
    expect(titles()).toEqual(SERVER_ORDER);
    expectNeutral();
  });
});
