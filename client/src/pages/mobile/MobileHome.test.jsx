// The mobile home for a console role, after it stopped being an assignments list.
//
// Pinned, in the order it would hurt if it regressed:
//   1. the pulse figures come from /organization/overview — none invented on the client
//   2. a missing producer and a real zero stay visibly different ("—" vs 0)
//   3. a failing /events greys one section; it never takes the whole page down
//   4. the web handoff opens the BROWSER (openExternal), never a WebView — the payments
//      policy reasoning that removed the console routes depends on it
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../api", () => ({
  default: { get: vi.fn() },
  errMsg: (e) => e?.message ?? "Something went wrong.",
}));

vi.mock("../../auth/AuthContext", () => ({
  useAuth: vi.fn(() => ({ user: { full_name: "Ajay Kumar", role: "org_admin" } })),
}));

const openExternal = vi.fn();
vi.mock("../../native/bridge", () => ({ openExternal: (...a) => openExternal(...a) }));

vi.mock("../../platform", () => ({
  IS_NATIVE: true,
  HAS_CONSOLES: false,
  WEB_APP_URL: "https://get.zoikostream.com",
}));

import api from "../../api";
import { ThemeProvider } from "../../theme/ThemeContext";
import MobileHome from "./MobileHome";

// Shapes from services/org.py::overview and the /events Page envelope, as the web
// dashboard's test copies them.
const OVERVIEW = {
  organization: { name: "Northwind" },
  sessions: { active: 1, live: 1, paused: 0, starting_soon: 2, current_audience: 40 },
  attention: [],
};

const UPCOMING = {
  total: 2,
  items: [
    { id: "e1", title: "Tech Summit", start_time: "2026-10-20T10:00:00Z", status: "live" },
    { id: "e2", title: "Product Launch", start_time: "2026-10-25T14:00:00Z", status: "published" },
  ],
};

let world;

function route(url, config = {}) {
  const params = config.params || {};
  if (url === "/organization/overview") return world.overview();
  if (url === "/events") return world.events(params);
  return Promise.resolve({ data: {} });
}

beforeEach(() => {
  world = {
    overview: () => Promise.resolve({ data: OVERVIEW }),
    events: () => Promise.resolve({ data: UPCOMING }),
  };
  vi.mocked(api.get).mockImplementation(route);
  openExternal.mockClear();
});

const renderHome = () =>
  render(
    <ThemeProvider>
      <MemoryRouter initialEntries={["/home"]}>
        <MobileHome />
      </MemoryRouter>
    </ThemeProvider>,
  );

describe("MobileHome", () => {
  it("renders the pulse from /organization/overview", async () => {
    renderHome();

    expect(await screen.findByText("Live now")).toBeInTheDocument();
    expect(screen.getByText("Starting soon")).toBeInTheDocument();
    expect(screen.getByText("Watching")).toBeInTheDocument();
    // Three real values from the payload — a pulse invented on the client is worse than
    // none, because it looks authoritative.
    expect(screen.getByText("1")).toBeInTheDocument();
    expect(screen.getByText("2")).toBeInTheDocument();
    expect(screen.getByText("40")).toBeInTheDocument();
    expect(api.get).toHaveBeenCalledWith(
      "/organization/overview",
      expect.objectContaining({ params: expect.objectContaining({ range: "24h" }) }),
    );
  });

  it("lists the next events with their status", async () => {
    renderHome();

    expect(await screen.findByText("Tech Summit")).toBeInTheDocument();
    expect(screen.getByText("Product Launch")).toBeInTheDocument();
    expect(screen.getByText("Live")).toBeInTheDocument(); // statusMeta("live")
    expect(screen.getByText("Published")).toBeInTheDocument();
  });

  it("shows a missing producer as — and keeps it distinct from zero", async () => {
    // Same rule the org dashboard pins: no figure is not a zero. sessions is null here —
    // the overview resolved without one — so every card reads "—".
    world.overview = () => Promise.resolve({ data: { organization: { name: "Northwind" } } });

    renderHome();

    await waitFor(() => expect(screen.getByText("At a glance")).toBeInTheDocument());
    expect(screen.getAllByText("—")).toHaveLength(3);
    expect(screen.queryByText("40")).not.toBeInTheDocument();
  });

  it("keeps the page up when /events fails — only the list is lost", async () => {
    // The pulse is the page; the schedule is a supporting figure. This mirrors the
    // allSettled contract useDashboardData.js established on the web dashboard.
    world.events = () => Promise.reject(new Error("network down"));

    renderHome();

    expect(await screen.findByText("40")).toBeInTheDocument();
    expect(screen.getByText(/Nothing scheduled/)).toBeInTheDocument();
  });

  it("offers Try again when the overview itself fails", async () => {
    world.overview = () => Promise.reject(new Error("network down"));

    renderHome();

    // errMsg surfaces the API error's own message; the fallback only covers a message-less
    // failure. Assert on what actually renders, plus the recovery path.
    expect(await screen.findByText("network down")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /try again/i })).toBeInTheDocument();
  });

  it("hands off to the web console through the browser bridge, not a WebView", async () => {
    renderHome();

    await userEvent.click(await screen.findByRole("button", { name: /open in your browser/i }));

    expect(openExternal).toHaveBeenCalledTimes(1);
    expect(openExternal).toHaveBeenCalledWith("https://get.zoikostream.com/organization/dashboard");
  });

  it("still links the contributor's own assignment list", async () => {
    renderHome();

    const link = await screen.findByRole("link", { name: /your events/i });
    expect(link).toHaveAttribute("href", "/events/mine");
  });
});
