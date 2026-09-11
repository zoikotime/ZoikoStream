// ONE shell for every /organization/* route.
//
// The regression this exists to prevent is the one that was reported: the rail and topbar
// changed structure when you navigated out of the dashboard, because the simplified shell
// was something a single page opted into. These assert that the shell is now a property of
// the layout, identical on every route, and that only the active item and the page content
// move.
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../api", () => ({
  default: { get: vi.fn(), post: vi.fn() },
  errMsg: (e) => e?.message ?? "Something went wrong.",
}));

vi.mock("../auth/AuthContext", () => ({
  useAuth: () => ({ user: { full_name: "Vihari Owner", email: "owner@example.com" }, logout: vi.fn() }),
}));

import api from "../api";
import { ThemeProvider } from "../theme/ThemeContext";
import OrganizationLayout from "./OrganizationLayout";

const CONSOLE_STATE = {
  organization: { name: "Northwind", plan: "Growth" },
  workspace: { label: "production", slug: "production" },
  workspaces: [{ label: "production", slug: "production" }],
  badges: {},
  user: { name: "Vihari Owner", email: "owner@example.com", role: "Organization Owner" },
  health: { status: "ok" },
};

// Every organization route this shell serves. Page bodies are stand-ins on purpose: this is
// a test of the SHELL contract, and a real page would drag its own fetches in with it.
const ROUTES = [
  ["/organization/dashboard", "Dashboard"],
  ["/organization/events", "Events"],
  ["/organization/audience", "Audience"],
  ["/organization/users", "Members"],
  ["/organization/recordings", "Recordings"],
  ["/organization/sessions", "Streaming Sessions"],
  ["/organization/playback", "Playback & Access"],
  ["/organization/profile", "Organization"],
  ["/organization/analytics", "Analytics"],
  ["/organization/billing", "Billing"],
  ["/organization/settings", "Settings"],
  ["/organization/support", "Support & Status"],
];

beforeEach(() => {
  vi.mocked(api.get).mockResolvedValue({ data: CONSOLE_STATE });
  // The rail's collapsed state is a persisted preference; start every test expanded so the
  // toggle assertions below do not depend on the order tests happen to run in.
  localStorage.clear();
});

function renderShell(at = "/organization/dashboard") {
  return render(
    <ThemeProvider>
      <MemoryRouter initialEntries={[at]}>
        <Routes>
          <Route element={<OrganizationLayout />}>
            {ROUTES.map(([path, label]) => (
              <Route key={path} path={path} element={<h1>{label} page</h1>} />
            ))}
            <Route path="/organization/events/:id" element={<h1>event detail page</h1>} />
          </Route>
        </Routes>
      </MemoryRouter>
    </ThemeProvider>
  );
}

const rail = () => screen.getAllByRole("navigation")[0];
const railLinks = () =>
  within(rail())
    .getAllByRole("link")
    .map((a) => a.textContent.trim());
const activeLink = () =>
  within(rail())
    .getAllByRole("link")
    .find((a) => a.getAttribute("aria-current") === "page");

describe("the shell is the same on every organization route", () => {
  it.each(ROUTES)("%s renders the one rail, in one order", async (path) => {
    renderShell(path);
    await screen.findByRole("heading", { level: 1 });
    expect(railLinks()).toEqual(ROUTES.map(([, label]) => label));
  });

  it.each(ROUTES)("%s never renders the old grouped headings", async (path) => {
    renderShell(path);
    await screen.findByRole("heading", { level: 1 });
    for (const heading of [/^home$/i, /^build$/i, /^operate$/i, /^manage$/i]) {
      expect(within(rail()).queryByText(heading)).not.toBeInTheDocument();
    }
  });

  it.each(ROUTES)("%s never renders a permanent health verdict", async (path) => {
    renderShell(path);
    await screen.findByRole("heading", { level: 1 });
    expect(screen.queryByText(/^healthy$/i)).not.toBeInTheDocument();
  });

  it.each(ROUTES)("%s never renders the dead workspace / range controls", async (path) => {
    renderShell(path);
    await screen.findByRole("heading", { level: 1 });
    expect(screen.queryByText(/all workspaces/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/last 24 hours/i)).not.toBeInTheDocument();
  });

  it("keeps the primary action on every route", async () => {
    for (const [path] of ROUTES.slice(0, 4)) {
      const view = renderShell(path);
      await screen.findByRole("heading", { level: 1 });
      expect(screen.getByRole("button", { name: /request live event/i })).toBeInTheDocument();
      view.unmount();
    }
  });
});

describe("management utilities sit at the bottom", () => {
  it("orders Analytics, Billing, Settings, Support & Status last", async () => {
    renderShell();
    await screen.findByRole("heading", { level: 1 });
    const labels = railLinks();
    expect(labels.slice(-4)).toEqual(["Analytics", "Billing", "Settings", "Support & Status"]);
    // …and specifically below the everyday event work, not mixed into it.
    for (const workflow of ["Events", "Audience", "Recordings", "Streaming Sessions", "Playback & Access"]) {
      expect(labels.indexOf(workflow)).toBeLessThan(labels.indexOf("Analytics"));
    }
  });

  it("puts Support & Status below Settings, not up with the integration items", async () => {
    renderShell();
    await screen.findByRole("heading", { level: 1 });
    const labels = railLinks();
    expect(labels.indexOf("Support & Status")).toBeGreaterThan(labels.indexOf("Settings"));
    expect(labels.indexOf("Support & Status")).toBeGreaterThan(labels.indexOf("Live Inputs"));
  });

  it("makes Support & Status the last navigation item before Collapse", async () => {
    renderShell();
    await screen.findByRole("heading", { level: 1 });
    expect(railLinks().at(-1)).toBe("Support & Status");
    expect(screen.getByRole("button", { name: /collapse sidebar/i })).toBeInTheDocument();
  });
});

describe("active navigation", () => {
  it.each([
    ["/organization/dashboard", "Dashboard"],
    ["/organization/events", "Events"],
    ["/organization/playback", "Playback & Access"],
    ["/organization/analytics", "Analytics"],
    ["/organization/billing", "Billing"],
    ["/organization/settings", "Settings"],
    ["/organization/support", "Support & Status"],
  ])("%s marks %s as current", async (path, label) => {
    renderShell(path);
    await screen.findByRole("heading", { level: 1 });
    expect(activeLink()).toHaveTextContent(label);
  });

  it("resolves a nested event route to its parent item", async () => {
    renderShell("/organization/events/abc-123");
    await screen.findByText("event detail page");
    expect(activeLink()).toHaveTextContent("Events");
  });

  it("does not leave Dashboard active on every route", async () => {
    renderShell("/organization/billing");
    await screen.findByRole("heading", { level: 1 });
    expect(activeLink()).not.toHaveTextContent("Dashboard");
  });
});

describe("navigating between routes", () => {
  it("changes only the active item and the content, never the rail itself", async () => {
    renderShell();
    await screen.findByText("Dashboard page");
    const before = railLinks();

    for (const label of ["Events", "Playback & Access", "Analytics", "Billing", "Settings"]) {
      // Sequential on purpose: this asserts what happens ACROSS a real navigation path,
      // so each click has to land before the next one is made.
      await userEvent.click(within(rail()).getByRole("link", { name: label }));
      await screen.findByText(`${label} page`);

      expect(railLinks()).toEqual(before);
      expect(activeLink()).toHaveTextContent(label);
      expect(screen.queryByText(/^healthy$/i)).not.toBeInTheDocument();
      expect(within(rail()).queryByText(/^operate$/i)).not.toBeInTheDocument();
    }
  });
});

describe("the sidebar collapse control", () => {
  const toggle = () => screen.getByRole("button", { name: /(collapse|expand) sidebar/i });

  it("lives in the header, not as a row at the foot of the rail", async () => {
    renderShell();
    await screen.findByRole("heading", { level: 1 });
    expect(toggle().closest("header")).not.toBeNull();
    expect(within(rail()).queryByRole("button", { name: /(collapse|expand) sidebar/i }))
      .not.toBeInTheDocument();
  });

  it("is icon-only — no Collapse or Expand text anywhere on screen", async () => {
    renderShell();
    await screen.findByRole("heading", { level: 1 });
    expect(toggle()).toHaveTextContent("");
    expect(screen.queryByText(/^collapse$/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/^expand$/i)).not.toBeInTheDocument();
  });

  it("names the action it will perform, and flips when the state does", async () => {
    renderShell();
    await screen.findByRole("heading", { level: 1 });

    const collapse = screen.getByRole("button", { name: "Collapse sidebar" });
    expect(collapse).toHaveAttribute("aria-expanded", "true");
    expect(collapse).toHaveAttribute("title", "Collapse sidebar");

    await userEvent.click(collapse);

    const expand = screen.getByRole("button", { name: "Expand sidebar" });
    expect(expand).toHaveAttribute("aria-expanded", "false");
    expect(expand).toHaveAttribute("title", "Expand sidebar");

    // …and back, so the control is a real toggle rather than a one-way door.
    await userEvent.click(expand);
    expect(screen.getByRole("button", { name: "Collapse sidebar" })).toBeInTheDocument();
  });

  it("swaps the icon with the state", async () => {
    renderShell();
    await screen.findByRole("heading", { level: 1 });
    const iconOf = (btn) => btn.querySelector("svg")?.getAttribute("class") || "";

    const expanded = iconOf(toggle());
    await userEvent.click(toggle());
    // lucide stamps the icon name into the class list, so this reads the actual glyph
    // rather than trusting that a conditional branch was taken.
    expect(iconOf(toggle())).not.toBe(expanded);
    expect(expanded).toMatch(/panel-left-close/);
    expect(iconOf(toggle())).toMatch(/panel-left-open/);
  });

  it("actually narrows the rail", async () => {
    renderShell();
    await screen.findByRole("heading", { level: 1 });
    const aside = rail().closest("aside");
    expect(aside.className).toContain("lg:w-64");

    await userEvent.click(toggle());
    expect(aside.className).toContain("lg:w-[4.75rem]");
  });

  it("stays separate from the mobile drawer control", async () => {
    renderShell();
    await screen.findByRole("heading", { level: 1 });
    // Two controls, one per breakpoint — neither doubles as the other.
    expect(screen.getByRole("button", { name: /toggle menu/i })).toBeInTheDocument();
    expect(toggle()).not.toBe(screen.getByRole("button", { name: /toggle menu/i }));
  });

  it.each(ROUTES)("%s carries the same control", async (path) => {
    renderShell(path);
    await screen.findByRole("heading", { level: 1 });
    expect(toggle()).toBeInTheDocument();
  });
});

describe("technical surfaces are not primary navigation", () => {
  it("keeps Developer Platform, Credentials, Live Inputs and Webhooks out of the rail", async () => {
    renderShell();
    await screen.findByRole("heading", { level: 1 });
    const labels = railLinks();
    for (const gone of ["Developer Platform", "Credentials", "Live Inputs", "Webhooks"]) {
      expect(labels).not.toContain(gone);
    }
  });

  it("still shows the twelve destinations an organizer actually works in", async () => {
    renderShell();
    await screen.findByRole("heading", { level: 1 });
    expect(railLinks()).toEqual([
      "Dashboard", "Events", "Audience", "Members", "Recordings",
      "Streaming Sessions", "Playback & Access",
      "Organization",
      "Analytics", "Billing", "Settings", "Support & Status",
    ]);
  });

  it("keeps Settings, which is where the technical surfaces now live", async () => {
    renderShell();
    await screen.findByRole("heading", { level: 1 });
    expect(within(rail()).getByRole("link", { name: "Settings" })).toBeInTheDocument();
  });

  it("leaves no link to /organization/webhooks anywhere in the shell", async () => {
    renderShell();
    await screen.findByRole("heading", { level: 1 });
    const hrefs = [...document.querySelectorAll("a[href]")].map((a) => a.getAttribute("href"));
    expect(hrefs.filter((h) => h && h.includes("/organization/webhooks"))).toEqual([]);
  });

  it("matches no route at all, so nothing of the page renders", () => {
    // Stronger than "the page is blank": with the route gone, the path matches nothing —
    // not even the layout — so the shell never mounts here. In the real App.jsx that same
    // miss is caught by `<Route path="*" element={<RootRedirect />} />`, which sends a
    // signed-in member to their role home. No custom redirect was added for this.
    const { container } = renderShell("/organization/webhooks");
    expect(container).toBeEmptyDOMElement();
  });
});
