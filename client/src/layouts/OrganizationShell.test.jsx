// ONE shell for every /organization/* route, with the rail ZST-UX-NAV-001 §4 specifies.
//
// What these pin:
//   * the seven primary destinations, in the spec's order, on every route — and Developers as
//     a conditional eighth, shown only where the organization has developer access in use
//   * Profile is an account utility (avatar menu), not a destination; Streaming Sessions is a
//     child of its event, not a top-level item
//   * only the group you are in opens; a child is current by path AND query
//   * what a member sees follows their role: no admin destinations, no Create event CTA
//   * entering an event swaps the rail for the event workspace's seven sections, inside the
//     same shell (§3.2, §6)
//   * the collapse / drawer controls and the Guide's safe area, unchanged
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../api", () => ({
  default: { get: vi.fn(), post: vi.fn() },
  errMsg: (e) => e?.message ?? "Something went wrong.",
}));

let authRole = "org_admin";
vi.mock("../auth/AuthContext", () => ({
  useAuth: () => ({ user: { full_name: "Vihari Owner", email: "owner@example.com", role: authRole }, logout: vi.fn() }),
}));

import api from "../api";
import { ThemeProvider } from "../theme/ThemeContext";
import { consoleChip } from "../ui/tokens";
import OrganizationLayout from "./OrganizationLayout";
import EventWorkspaceLayout from "../pages/organization/event/EventWorkspaceLayout";

const SPEC_RAIL = [
  "Overview", "Events", "Audience & Access", "Media & Recordings", "Analytics", "Organization", "Support",
];

let consoleState;
const baseState = (over = {}) => ({
  organization: { name: "Northwind", plan: "Growth", status: "active" },
  badges: {},
  user: { name: "Vihari Owner", email: "owner@example.com", role: "org_admin", role_label: "Organization Owner" },
  health: { status: "ok", label: "Healthy" },
  features: { developers: false },
  ...over,
});

const EVENT = {
  id: "abc-123", title: "Quarterly town hall", status: "scheduled", display_status: "scheduled",
  start_time: "2026-11-02T17:00:00Z", timezone: "Europe/London", visibility: "public",
};
const READINESS = {
  verdict: "blocked",
  gates: [
    { key: "title", label: "Title set", passed: true, required: true },
    { key: "host", label: "Host assigned", passed: false, required: true },
    { key: "recording", label: "Recording enabled", passed: false, required: true },
    { key: "redundancy", label: "No single-path override", passed: false, required: false },
  ],
};

let eventResponse;
beforeEach(() => {
  authRole = "org_admin";
  consoleState = baseState();
  eventResponse = () => Promise.resolve({ data: EVENT });
  vi.mocked(api.get).mockImplementation((url) => {
    if (url === "/organization/console-state") return Promise.resolve({ data: consoleState });
    if (url === `/events/${EVENT.id}`) return eventResponse();
    if (url === `/organization/events/${EVENT.id}/readiness`) return Promise.resolve({ data: READINESS });
    return Promise.reject(Object.assign(new Error("not found"), { response: { status: 404 } }));
  });
  localStorage.clear();
});

// Every organization route this shell serves. Page bodies are stand-ins on purpose: this is a
// test of the SHELL contract, and a real page would drag its own fetches in with it.
const PAGES = [
  ["/organization/dashboard", "Dashboard"],
  ["/organization/events", "Events"],
  ["/organization/sessions", "Streaming Sessions"],
  ["/organization/audience", "Audience"],
  ["/organization/audience/access", "Playback & Access"],
  ["/organization/playback", "Playback & Access"],
  ["/organization/recordings", "Recordings"],
  ["/organization/analytics", "Analytics"],
  ["/organization/manage", "Organization"],
  ["/organization/users", "Members"],
  ["/organization/security", "Security"],
  ["/organization/billing", "Billing"],
  ["/organization/settings", "Settings"],
  ["/organization/profile", "Profile"],
  ["/organization/support", "Support"],
  ["/organization/developers", "Developers"],
  ["/organization/credentials", "Credentials"],
];

function renderShell(at = "/organization/dashboard") {
  return render(
    <ThemeProvider>
      <MemoryRouter initialEntries={[at]}>
        <RouteProbe />
        <Routes>
          <Route element={<OrganizationLayout />}>
            {PAGES.map(([path, label]) => (
              <Route key={path} path={path} element={<h1>{label} page</h1>} />
            ))}
          </Route>
          <Route path="/status" element={<h1>Public status page</h1>} />
          <Route path="/organization/events/:id" element={<EventWorkspaceLayout />}>
            <Route index element={<h1>overview section</h1>} />
            <Route path="preparation" element={<h1>preparation section</h1>} />
            <Route path="activity" element={<h1>activity section</h1>} />
          </Route>
        </Routes>
      </MemoryRouter>
    </ThemeProvider>
  );
}

function RouteProbe() {
  const location = useLocation();
  const navigate = useNavigate();
  return (
    <>
      <output data-testid="route-location">{location.pathname + location.search + location.hash}</output>
      <button onClick={() => navigate(-1)}>Browser back</button>
      <button onClick={() => navigate(1)}>Browser forward</button>
    </>
  );
}

const rail = () => screen.getByRole("navigation", { name: "Organization" });
// The top-level destinations, by their visible label.
const primary = () =>
  [...rail().querySelector("ul").children].map((li) => li.querySelector("a").textContent.trim());
const current = () =>
  within(rail()).getAllByRole("link").filter((a) => a.getAttribute("aria-current") === "page");

describe("the organization rail follows ZST-UX-NAV-001 §4", () => {
  it.each(PAGES.filter(([p]) => !p.includes("developers") && !p.includes("credentials")))(
    "%s renders the seven destinations, in order",
    async (path) => {
      renderShell(path);
      await screen.findByRole("heading", { level: 1 });
      await waitFor(() => expect(primary()).toEqual(SPEC_RAIL));
    }
  );

  it("adds Developers as the eighth item only where developer access is in use", async () => {
    consoleState = baseState({ features: { developers: true } });
    renderShell();
    await waitFor(() => expect(primary()).toEqual([...SPEC_RAIL, "Developers"]));
  });

  it("still shows Developers while you are on a developer page, so a deep link is never orphaned", async () => {
    renderShell("/organization/credentials");
    await screen.findByText("Credentials page");
    await waitFor(() => expect(primary().at(-1)).toBe("Developers"));
    expect(current()[0]).toHaveAccessibleName("API credentials");
  });

  it("keeps Profile out of the rail — it lives in the account menu", async () => {
    renderShell("/organization/profile");
    await screen.findByText("Profile page");
    expect(within(rail()).queryByRole("link", { name: /profile/i })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Account menu" }));
    expect(screen.getByRole("menuitem", { name: /profile/i })).toBeInTheDocument();
  });

  it("keeps Streaming Sessions out of the rail and files the page under Events", async () => {
    renderShell("/organization/sessions");
    await screen.findByText("Streaming Sessions page");
    expect(within(rail()).queryByText(/streaming sessions/i)).not.toBeInTheDocument();
    // The active-group pill (the navy rail's gradient), with no child claiming the page.
    expect(within(rail()).getByRole("link", { name: "Events" }).className).toContain("from-violet-600");
  });

  it("never renders the old grouped headings or a Members / Billing / Settings top level", async () => {
    renderShell();
    await screen.findByRole("heading", { level: 1 });
    for (const old of ["Members", "Billing", "Settings", "Recordings", "Support & Status", "Dashboard"]) {
      expect(primary()).not.toContain(old);
    }
  });
});

describe("groups and their children", () => {
  it("opens only the group you are in", async () => {
    renderShell("/organization/events");
    await screen.findByText("Events page");
    expect(within(rail()).getByRole("link", { name: "Calendar" })).toBeInTheDocument();
    expect(within(rail()).queryByRole("link", { name: "Team & roles" })).not.toBeInTheDocument();
    expect(within(rail()).getByRole("button", { name: "Collapse Events" })).toHaveAttribute("aria-expanded", "true");
    expect(within(rail()).getByRole("button", { name: "Expand Organization" })).toHaveAttribute("aria-expanded", "false");
  });

  it("opens another group on request without navigating", async () => {
    renderShell("/organization/events");
    await screen.findByText("Events page");
    await userEvent.click(within(rail()).getByRole("button", { name: "Expand Organization" }));
    expect(within(rail()).getByRole("link", { name: "Security & audit" })).toHaveAttribute("href", "/organization/security");
    expect(screen.getByText("Events page")).toBeInTheDocument();
  });

  it.each([
    ["/organization/events", "All events"],
    ["/organization/events?view=calendar", "Calendar"],
    ["/organization/events?status=on_air", "Live now"],
    ["/organization/users", "Team & roles"],
    ["/organization/settings?tab=branding", "Branding"],
    ["/organization/settings", "Organization settings"],
    ["/organization/security", "Security & audit"],
    ["/organization/billing", "Plan, usage & billing"],
    ["/organization/audience/access", "Event access"],
    ["/organization/playback", "Event access"],
    ["/organization/recordings", "All recordings"],
    ["/organization/recordings?state=ready", "Replay-ready"],
    ["/organization/analytics", "Analytics overview"],
    ["/organization/analytics#quality", "Stream quality"],
    ["/organization/manage", "Organization overview"],
  ])("%s marks %s — and only it — current", async (path, label) => {
    renderShell(path);
    await screen.findByRole("heading", { level: 1 });
    expect(current()).toHaveLength(1);
    expect(current()[0]).toHaveAccessibleName(label);
  });

  it.each([
    ["/organization/dashboard", "Overview"],
    ["/organization/support", "Support"],
  ])("%s marks the %s item itself current", async (path, label) => {
    renderShell(path);
    await screen.findByRole("heading", { level: 1 });
    expect(current().map((a) => a.textContent.trim())).toEqual([label]);
  });

  it("shows a live count on Events and on its Live now child", async () => {
    consoleState = baseState({ badges: { live_events: 2 } });
    renderShell("/organization/events");
    await screen.findByText("Events page");
    await waitFor(() => expect(within(rail()).getByRole("link", { name: /^Events/ })).toHaveTextContent("2"));
    expect(within(rail()).getByRole("link", { name: /^Live now/ })).toHaveTextContent("2");
  });
});

describe("role-aware rail", () => {
  it("offers an organization admin the Create event action, once, in the rail", async () => {
    renderShell();
    await screen.findByRole("heading", { level: 1 });
    expect(screen.getByRole("link", { name: "Create event" })).toHaveAttribute("href", "/organization/events?create=true");
  });

  it("shows a member without admin rights only what they may open, and no create action", async () => {
    authRole = "host";
    consoleState = baseState({ user: { name: "Hana", email: "h@example.com", role: "host", role_label: "Host" } });
    renderShell();
    await screen.findByRole("heading", { level: 1 });
    await waitFor(() => expect(primary()).toEqual(["Overview"]));
    expect(screen.queryByRole("link", { name: "Create event" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /request live event/i })).not.toBeInTheDocument();
  });

  it("names the organization and your role, so the active tenancy is always explicit", async () => {
    renderShell();
    await screen.findByText("Northwind");
    expect(screen.getByText(/organization owner · growth/i)).toBeInTheDocument();
  });
});

describe("utility footer", () => {
  it.each([
    ["/organization/support", "Service status: Healthy", "/organization/support#service-health", "Support page"],
    ["/organization/support", "View service status details", "/status", "Public status page"],
    ["/organization/developers", "Service status: Healthy", "/organization/support#service-health", "Support page"],
    ["/organization/developers", "View service status details", "/status", "Public status page"],
    ["/organization/events", "Service status: Healthy", "/organization/support#service-health", "Support page"],
    ["/organization/events", "View service status details", "/status", "Public status page"],
  ])("from %s, %s opens its intended destination", async (path, linkName, destination, destinationPage) => {
    renderShell(path);
    await screen.findByRole("heading", { level: 1 });
    const user = userEvent.setup();
    const status = await screen.findByRole("link", { name: linkName });
    expect(status).toHaveAttribute("href", destination);

    await user.click(status);
    await screen.findByText(destinationPage);
    await waitFor(() => expect(screen.getByTestId("route-location")).toHaveTextContent(destination));
    if (destination.startsWith("/organization/support")) {
      expect(within(rail()).getByRole("link", { name: "Support" })).toHaveAttribute("aria-current", "page");
    }

    await user.click(screen.getByRole("button", { name: "Browser back" }));
    await waitFor(() => expect(screen.getByTestId("route-location")).toHaveTextContent(path));
    await user.click(screen.getByRole("button", { name: "Browser forward" }));
    await waitFor(() => expect(screen.getByTestId("route-location")).toHaveTextContent(destination));
  });

  it("keeps the organization health indicator and public status details as separate links", async () => {
    renderShell("/organization/support");
    await screen.findByText("Support page");
    const status = await screen.findByRole("link", { name: "Service status: Healthy" });
    const viewStatus = screen.getByRole("link", { name: "View service status details" });
    expect(status).toHaveAttribute("href", "/organization/support#service-health");
    expect(viewStatus).toHaveAttribute("href", "/status");
    expect(status).not.toHaveAttribute("href", viewStatus.getAttribute("href"));
  });

  it("keeps the status link unavailable message when health cannot be read", async () => {
    vi.mocked(api.get).mockImplementation(() => Promise.reject(new Error("down")));
    renderShell();
    expect(await screen.findByRole("link", { name: "Service status: Status unavailable" })).toBeInTheDocument();
  });

  it("keeps both links distinct and does not nest anchors", async () => {
    renderShell();
    await screen.findByRole("heading", { level: 1 });
    const status = await screen.findByRole("link", { name: "Service status: Healthy" });
    const viewStatus = screen.getByRole("link", { name: "View service status details" });
    expect(status).not.toContainElement(viewStatus);
  });
});

describe("the event workspace rail", () => {
  const eventRail = () => screen.getByRole("navigation", { name: "Event workspace" });
  const sections = () =>
    [...eventRail().querySelector("ul").children].map((li) => li.querySelector("a").textContent.trim());

  it("replaces the organization rail with the event's seven sections, in order", async () => {
    renderShell(`/organization/events/${EVENT.id}`);
    await screen.findByText("overview section");
    expect(screen.queryByRole("navigation", { name: "Organization" })).not.toBeInTheDocument();
    await waitFor(() =>
      expect(sections()).toEqual([
        "Event Overview", "Preparation2", "Audience AccessPublic", "Live Control", "Replay & Assets", "Insights", "Activity & Audit",
      ])
    );
    expect(within(eventRail()).getByRole("link", { name: "All events" })).toHaveAttribute("href", "/organization/events");
    expect(within(eventRail()).getByText("Quarterly town hall")).toBeInTheDocument();
  });

  it("counts only the REQUIRED checks that fail as Preparation's blockers", async () => {
    renderShell(`/organization/events/${EVENT.id}`);
    await screen.findByText("overview section");
    const prep = await within(eventRail()).findByRole("link", { name: /^Preparation/ });
    await waitFor(() => expect(prep).toHaveTextContent("2"));
  });

  it("moves the current section with the route, and reads nothing but the event to do it", async () => {
    renderShell(`/organization/events/${EVENT.id}`);
    await screen.findByText("overview section");
    await userEvent.click(within(eventRail()).getByRole("link", { name: "Activity & Audit" }));
    await screen.findByText("activity section");
    expect(within(eventRail()).getByRole("link", { name: "Activity & Audit" })).toHaveAttribute("aria-current", "page");
    expect(vi.mocked(api.post)).not.toHaveBeenCalled();
  });

  it("answers an event it cannot read with a non-disclosing not-found, inside the shell", async () => {
    eventResponse = () => Promise.reject(Object.assign(new Error("Event not found"), { response: { status: 404 } }));
    renderShell(`/organization/events/${EVENT.id}`);
    expect(await screen.findByRole("heading", { name: "Event not found" })).toBeInTheDocument();
    expect(screen.queryByText("overview section")).not.toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "Event workspace" })).toBeInTheDocument();
  });

  it("keeps the shell's identity and service status around the event rail", async () => {
    renderShell(`/organization/events/${EVENT.id}`);
    await screen.findByText("overview section");
    expect(await screen.findByRole("link", { name: "Service status: Healthy" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Account menu" })).toBeInTheDocument();
  });
});

// White initials and the workspace icon sit on these chips. The console chip ramp holds AA in
// both themes (ui/contrast.test.js); the violet-500 → fuchsia-500 it replaced measured ~3.5:1.
describe("identity chips in the rail", () => {
  it("draw the member's initials and the workspace badge on the AA chip ramp", async () => {
    renderShell();
    await screen.findByRole("heading", { level: 1 });
    const aside = rail().closest("aside");
    const account = within(aside).getByRole("button", { name: "Vihari Owner, Organization Owner — account" });
    const avatar = within(account).getByText("VO").parentElement;
    expect(avatar).toHaveClass(...consoleChip.split(" "));
    const badge = within(aside).getByText("Northwind").closest("div.flex").querySelector("span[aria-hidden='true']");
    expect(badge).toHaveClass(...consoleChip.split(" "));
    expect(aside.innerHTML).not.toMatch(/from-violet-500|to-fuchsia-500/);
  });
});

describe("the sidebar collapse control", () => {
  const toggle = () => screen.getByRole("button", { name: /(collapse|expand) sidebar/i });

  it("lives in the header, not as a row at the foot of the rail", async () => {
    renderShell();
    await screen.findByRole("heading", { level: 1 });
    expect(toggle().closest("header")).not.toBeNull();
  });

  it("names the action it will perform, flips with the state, and narrows the rail", async () => {
    renderShell();
    await screen.findByRole("heading", { level: 1 });
    const aside = rail().closest("aside");
    expect(aside.className).toContain("lg:w-[16.25rem]");
    const collapse = screen.getByRole("button", { name: "Collapse sidebar" });
    expect(collapse).toHaveAttribute("aria-expanded", "true");
    await userEvent.click(collapse);
    expect(screen.getByRole("button", { name: "Expand sidebar" })).toHaveAttribute("aria-expanded", "false");
    expect(aside.className).toContain("lg:w-[4.5rem]");
  });

  it("keeps every destination's name when collapsed", async () => {
    renderShell("/organization/analytics");
    await screen.findByRole("heading", { level: 1 });
    await userEvent.click(toggle());
    const analytics = within(rail()).getByRole("link", { name: "Analytics" });
    expect(analytics).toHaveAttribute("aria-label", "Analytics");
    expect(analytics).toHaveAttribute("aria-current", "page");
  });

  it("stays separate from the mobile drawer control, which opens the drawer", async () => {
    renderShell();
    await screen.findByRole("heading", { level: 1 });
    // Two controls, one per breakpoint — neither doubles as the other.
    expect(screen.getByRole("button", { name: /navigation menu/i })).toBeInTheDocument();
    expect(toggle()).not.toBe(screen.getByRole("button", { name: /navigation menu/i }));
    await userEvent.click(screen.getByRole("button", { name: /navigation menu/i }));
    expect(rail().closest("aside").className).toContain("translate-x-0");
  });

  // Measured at 390px in Chrome: with the drawer closed, Tab walked through ~30 rail controls
  // that were translated off-screen. Below lg the closed drawer is now invisible as well.
  it("takes the closed mobile drawer out of the Tab order, and restores it when opened", async () => {
    renderShell();
    await screen.findByRole("heading", { level: 1 });
    const aside = rail().closest("aside");
    expect(aside).toHaveClass("-translate-x-full", "max-lg:invisible");
    await userEvent.click(screen.getByRole("button", { name: /navigation menu/i }));
    expect(aside).not.toHaveClass("-translate-x-full", "max-lg:invisible");
    expect(aside).toHaveClass("translate-x-0");
    // Desktop is untouched: nothing unprefixed hides the rail.
    expect(aside.className).not.toMatch(/(^|\s)invisible(\s|$)/);
  });
});

describe("what the shell must never do", () => {
  it("leaves no link to /organization/webhooks anywhere", async () => {
    renderShell();
    await screen.findByRole("heading", { level: 1 });
    const hrefs = [...document.querySelectorAll("a[href]")].map((a) => a.getAttribute("href"));
    expect(hrefs.filter((h) => h && h.includes("/organization/webhooks"))).toEqual([]);
  });

  it("reserves the floating Guide's footprint after the last row", async () => {
    renderShell("/organization/dashboard");
    await screen.findByRole("heading", { name: /dashboard page/i });
    expect(document.querySelector("main").classList.contains("zk-guide-safe")).toBe(true);
  });
});
