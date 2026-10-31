// Event Workspace navigation: every sidebar sub-heading in all six sections, judged by where the
// page actually scrolls, not only by the URL.
//
// jsdom lays nothing out, so this harness supplies the geometry: each element gets a distinct
// position the first time it is measured, and every scroll is recorded with the scroller it
// moved (the window, or the shell's <main>), that scroller's position before it, and the URL the
// app had rendered at that moment. A scroll computed from the wrong element (another section's
// #overview, a stale target) lands on a different offset; one made before navigating carries
// the old URL.
//
// Two shells, as AppShell renders them: "window" (the organization console: <main> grows with
// its content and the document scrolls under the sticky top bar) and "main" (a locked-viewport
// shell: <main> is the scroller and the top bar sits outside it).
import { Component, useEffect, useState } from "react";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { ThemeProvider } from "../../../theme/ThemeContext";

vi.mock("../../../api", () => ({
  default: {
    get: vi.fn(),
    post: vi.fn(),
    patch: vi.fn(),
    delete: vi.fn(),
  },
  errMsg: (e) => e?.message ?? "Error",
}));

vi.mock("../../../ui/Toast", () => ({
  notify: { error: vi.fn(), success: vi.fn(), info: vi.fn() },
}));

vi.mock("../../../auth/AuthContext", () => ({
  useAuth: () => ({ user: { id: "u1", role: "org_admin" }, logout: vi.fn() }),
}));

import api from "../../../api";
import EventWorkspaceLayout from "./EventWorkspaceLayout";
import EventOverview from "./EventOverview";
import EventPreparation from "./EventPreparation";
import EventAudienceAccess from "./EventAudienceAccess";
import EventLiveControl from "./EventLiveControl";
import EventReplay from "./EventReplay";
import EventInsights from "./EventInsights";
import EventActivity from "./EventActivity";
import { resolveTargetElement, scrollToSection, HASH_ALIASES, useHashScroll } from "./workspace";

const NOW = "2026-10-30T10:00:00Z";
const EVENT = {
  id: "e1",
  title: "Annual Global Broadcast",
  status: "scheduled",
  display_status: "scheduled",
  start_time: "2026-11-05T14:00:00Z",
  end_time: "2026-11-05T16:00:00Z",
  timezone: "UTC",
  visibility: "public",
  registration_required: true,
  category: "Keynote",
  recording_enabled: true,
  chat_enabled: true,
  qa_enabled: true,
};

const READINESS = {
  verdict: "passed",
  readiness_state: "ready",
  evaluated_at: NOW,
  impact: "tier_1",
  gates: [
    { key: "title", label: "Title set", passed: true, required: true },
    { key: "schedule", label: "Schedule confirmed", passed: true, required: true },
  ],
};

const LEDGER = () => ({
  items: [
    {
      id: "a1", at: NOW, action: "event.update", title: "Event details edited", category: "production",
      category_label: "Production changes", outcome: "applied", outcome_label: "Applied", changed_state: true,
      high_impact: false, actor: { id: "u1", name: "Ada Admin" }, details: [{ label: "Changed", value: "Title" }], reason: null,
      reference: "req_0123456789abcdef01234567",
    },
    {
      id: "a2", action: "event.end", title: "End event — rejected", outcome: "rejected", outcome_label: "Rejected",
      changed_state: false, reason: "The event was not live.", details: [], category: "production",
    },
  ],
  total: 2, page: 1, page_size: 25,
  scope: { kind: "event", event_id: "e1", title: EVENT.title, status: "scheduled", timezone: EVENT.timezone },
  summary: {
    total: 2, high_impact: 0, refused: 1, failed: 0, pending: 0,
    by_category: { access: 0, production: 2, evidence: 0, engagement: 0, commercial: 0, other: 0 },
    by_outcome: { applied: 1, rejected: 1 }, last_recorded_at: NOW,
  },
  actors: [{ value: "u1", label: "Ada Admin", count: 2 }],
  categories: [
    { value: "access", label: "Access & security" },
    { value: "production", label: "Production changes" },
    { value: "evidence", label: "Evidence" },
    { value: "engagement", label: "Audience interaction" },
    { value: "commercial", label: "Billing & commercial" },
    { value: "other", label: "Other" },
  ],
  integrity: {
    tenant_scope: { state: "explicit", label: "Explicit in each record" },
    hash_verification: { state: "not_established", label: "Not established" },
    immutability: { state: "not_asserted", label: "Not asserted" },
    time_anchoring: { state: "not_verified", label: "Not verified" },
  },
  export: { available: false, state: "not_offered", label: "Not offered", message: "" },
});

// ── geometry and scroll recording ───────────────────────────────────────────────────────────
const MAIN_TOP = 100;        // <main> starts below the 64px top bar
const HEADER_HEIGHT = 64;    // the shell's sticky top bar
const GAP = 16;              // breathing room under whatever is cleared

let shell;          // "window" | "main"
let tops;           // element -> top, assigned on first measurement
let nextTop;
let scrolls;        // { by: "window" | <element>, top, before, at }
let windowY;
let renderedAt;     // the URL the app last rendered
let goTo;           // navigate(), as an in-app link elsewhere in the console would

const rect = (top, height) => ({
  top, bottom: top + height, height, left: 0, right: 0, width: 0, x: 0, y: top, toJSON: () => ({}),
});
const topOf = (el) => tops.get(el);
const isScroller = (el) => (shell === "main" ? el.tagName === "MAIN" : el === document.documentElement);

let rectSpy;
beforeEach(() => {
  shell = "window";
  tops = new Map();
  nextTop = 1000;
  scrolls = [];
  windowY = 0;
  renderedAt = "";
  rectSpy = vi.spyOn(Element.prototype, "getBoundingClientRect").mockImplementation(function () {
    if (this.tagName === "MAIN") return rect(MAIN_TOP, 800);
    if (this.tagName === "HEADER" && this.classList.contains("sticky")) return rect(0, HEADER_HEIGHT);
    if (!tops.has(this)) {
      tops.set(this, nextTop);
      nextTop += 1000;
    }
    return rect(tops.get(this), 40);
  });
  Object.defineProperty(HTMLElement.prototype, "scrollHeight", {
    configurable: true, get() { return isScroller(this) ? 5000 : 0; },
  });
  Object.defineProperty(HTMLElement.prototype, "clientHeight", {
    configurable: true, get() { return this.tagName === "MAIN" ? 800 : 0; },
  });
  Object.defineProperty(HTMLElement.prototype, "scrollTo", {
    configurable: true,
    writable: true,
    value: function scrollTo({ top }) {
      scrolls.push({ by: this, top, before: this.scrollTop, at: renderedAt });
      this.scrollTop = top;
    },
  });
  Object.defineProperty(window, "pageYOffset", { configurable: true, get: () => windowY });
  window.scrollTo = ({ top }) => {
    scrolls.push({ by: "window", top, before: windowY, at: renderedAt });
    windowY = top;
  };
});

afterEach(() => {
  rectSpy.mockRestore();
  delete HTMLElement.prototype.scrollHeight;
  delete HTMLElement.prototype.clientHeight;
  delete HTMLElement.prototype.scrollTo;
  Object.defineProperty(window, "pageYOffset", { configurable: true, writable: true, value: 0 });
});

// The one scroll a navigation should produce: to the element now rendered with `id`, on the
// shell's scroller, clearing the sticky top bar (window) or not (it sits outside <main>).
async function expectScrolledTo(id, { scroller = "window" } = {}) {
  await waitFor(() => expect(scrolls.length).toBeGreaterThan(0), { timeout: 3000 });
  await act(async () => {});            // anything that would scroll a second time runs now
  expect(scrolls).toHaveLength(1);
  const [call] = scrolls;
  const target = document.getElementById(id);
  expect(target).not.toBeNull();
  expect(topOf(target)).toBeDefined();  // the scroll was computed from THIS element
  if (scroller === "window") {
    expect(call.by).toBe("window");
    expect(call.top).toBe(call.before + topOf(target) - (HEADER_HEIGHT + GAP));
  } else {
    expect(call.by).toBe(document.querySelector("main"));
    expect(call.top).toBe(call.before + topOf(target) - MAIN_TOP - GAP);
  }
  return call;
}

// A page without a hash: back to the top, and nothing scrolled anywhere else.
async function expectScrolledToTop() {
  await waitFor(() => expect(scrolls.some((c) => c.by === "window")).toBe(true), { timeout: 3000 });
  await act(async () => {});
  expect(scrolls.every((c) => c.top === 0)).toBe(true);
}

function LocationDisplay() {
  const loc = useLocation();
  const nav = useNavigate();
  const url = loc.pathname + loc.search + loc.hash;
  useEffect(() => {
    renderedAt = url;
    goTo = nav;
  });
  return (
    <div>
      <div data-testid="route-location">{url}</div>
      <button data-testid="btn-back" type="button" onClick={() => nav(-1)}>Back</button>
      <button data-testid="btn-forward" type="button" onClick={() => nav(1)}>Forward</button>
    </div>
  );
}

class CrashBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { error: null };
  }

  static getDerivedStateFromError(error) {
    return { error };
  }

  render() {
    return this.state.error ? <p>Workspace crashed: {String(this.state.error.message)}</p> : this.props.children;
  }
}

// EventWorkspaceLayout renders the real AppShell: its sticky top bar and its <main> are the ones
// measured and scrolled here.
function renderWorkspace(initialEntries = ["/organization/events/e1"], { scroller = "window" } = {}) {
  shell = scroller;
  return render(
    <ThemeProvider>
      <MemoryRouter initialEntries={initialEntries}>
        <LocationDisplay />
        <CrashBoundary>
          <Routes>
            <Route path="/organization/events/:id" element={<EventWorkspaceLayout />}>
              <Route index element={<EventOverview />} />
              <Route path="preparation" element={<EventPreparation />} />
              <Route path="access" element={<EventAudienceAccess />} />
              <Route path="live" element={<EventLiveControl />} />
              <Route path="replay" element={<EventReplay />} />
              <Route path="insights" element={<EventInsights />} />
              <Route path="activity" element={<EventActivity />} />
            </Route>
          </Routes>
        </CrashBoundary>
      </MemoryRouter>
    </ThemeProvider>
  );
}

const route = () => screen.getByTestId("route-location").textContent;
const workspaceRail = () => screen.findByRole("navigation", { name: "Event workspace" });
const mounted = (id) => waitFor(() => expect(document.getElementById(id)).not.toBeNull(), { timeout: 3000 });
// A page opened without a hash scrolls to its top once, on mount. Wait for that before a test
// starts counting scrolls, or under load it lands after the count was reset.
async function mountedAtTop(id) {
  await mounted(id);
  await waitFor(() => expect(scrolls.some((c) => c.by === "window")).toBe(true), { timeout: 3000 });
  await act(async () => {});
}
const BASE = "/organization/events/e1";

// Clicks a rail sub-heading and checks the URL, the selected item and the one scroll it made.
async function clickSubheading(user, rail, name, { url, target }) {
  const link = within(rail).getByRole("link", { name });
  scrolls = [];
  await user.click(link);
  await waitFor(() => expect(route()).toBe(url));
  if (target) await expectScrolledTo(target);
  else await expectScrolledToTop();
  expect(link).toHaveAttribute("aria-current", "page");
  return link;
}

// Each section test walks every sub-heading of a fully rendered workspace; like AnalyticsNav's
// suite, allow for a loaded machine rather than failing on the 5s default.
describe("Event Workspace Sidebar Sub-heading Navigation (All 6 Sections)", { timeout: 20000 }, () => {
  beforeEach(() => {
    vi.clearAllMocks();

    api.get.mockImplementation((url) => {
      if (url === "/organization/console-state") {
        return Promise.resolve({
          data: {
            organization: { name: "Northwind" },
            user: { role: "org_admin" },
            badges: {},
            health: { status: "ok", label: "Healthy" },
          },
        });
      }
      if (url === "/events/e1") return Promise.resolve({ data: EVENT });
      if (url === "/organization/events/e1/readiness") return Promise.resolve({ data: READINESS });
      if (url === "/organization/events/e1/live-status" || url === "/organization/events/e1/live-state") {
        // The live-status shape EventLiveControl reads (as EventWorkspace.test.jsx's LIVE()).
        return Promise.resolve({
          data: {
            event_id: "e1", status: "scheduled", on_air: false, as_of: NOW,
            current_session: null, sessions: [], session_count: 0, recording: null, recording_enabled: true,
            inputs: [], open_incidents: [], audience_now: null, audience_unavailable: false,
          },
        });
      }
      if (url === "/events/e1/planning") return Promise.resolve({ data: [] });
      if (url === "/events/e1/rehearsals") return Promise.resolve({ data: [] });
      if (url === "/events/e1/hosts") return Promise.resolve({ data: [{ id: "h1", full_name: "Jane Host" }] });
      if (url === "/events/e1/speakers") return Promise.resolve({ data: [] });

      if (url.includes("/commercial/events/e1/orders/current")) {
        return Promise.reject(Object.assign(new Error("not found"), { response: { status: 404 } }));
      }
      if (url === "/commercial/events/e1/quotes") return Promise.resolve({ data: [] });
      if (url === "/commercial/events/e1/capacity") return Promise.resolve({ data: {} });
      if (url === "/commercial/events/e1/readiness") return Promise.resolve({ data: { ready: true, blocking_reasons: [], required_checks: [] } });
      if (url === "/commercial/events/e1/incidents") return Promise.resolve({ data: [] });
      if (url === "/commercial/events/e1/schedule") return Promise.resolve({ data: {} });
      if (url === "/commercial/events/e1/payments") return Promise.resolve({ data: [] });
      if (url === "/commercial/events/e1/invoices") return Promise.resolve({ data: [] });
      if (url === "/commercial/events/e1/refunds") return Promise.resolve({ data: [] });
      if (url === "/commercial/events/e1/lifecycle") return Promise.resolve({ data: {} });
      if (url === "/commercial/events/e1/change-orders") return Promise.resolve({ data: [] });

      if (url.includes("/links") || url.includes("/registrations")) return Promise.resolve({ data: [] });
      if (url.includes("/recordings")) return Promise.resolve({ data: [] });
      if (url.includes("/replay-entitlements")) return Promise.resolve({ data: [] });

      if (url === "/organization/events/e1/insights") {
        return Promise.resolve({
          data: {
            as_of: NOW,
            measured: true,
            total_sessions: 10,
            live_presence_timeline: [],
            viewers: 10,
            audience: { peak_viewers: 10, unique_viewers: 10 },
            broadcast: { on_air_seconds: 120 },
            registration: { applicable: false },
            feedback: { average_rating: 4.5 },
            blocked_join_attempts: { total: 0 },
            sessions: { playback_starts: 10, failed_sessions: 0 },
            live: { rebuffer_ratio: 0.01, startup_ms_median: 200 },
            replay: { playback_starts: 5 },
            outcomes: { viewers: 10, watched_10_min: 8, reached_half: 7, reached_ninety: 6 },
          },
        });
      }
      if (url.includes("/feedback")) return Promise.resolve({ data: [] });
      if (url.includes("/activity")) {
        return Promise.resolve({
          data: LEDGER(),
        });
      }
      return Promise.resolve({ data: [] });
    });
  });

  describe("1. Target resolution and alias handling", () => {
    afterEach(() => {
      document.body.innerHTML = "";
    });

    it("resolves canonical IDs and aliases correctly", () => {
      expect(HASH_ALIASES["invitations"]).toBe("registrations");
      expect(HASH_ALIASES["links"]).toBe("access-links");
      expect(HASH_ALIASES["live-monitor"]).toBe("monitor");
      expect(HASH_ALIASES["stream-health"]).toBe("sessions");
      expect(HASH_ALIASES["details-schedule"]).toBe("details");
      expect(HASH_ALIASES["sources-ingest"]).toBe("sources");
      expect(HASH_ALIASES["readiness-checks"]).toBe("readiness");
      expect(HASH_ALIASES["event-team"]).toBe("team");
      expect(HASH_ALIASES["assets"]).toBe("recordings");
      expect(HASH_ALIASES["preservation"]).toBe("retention");
      expect(HASH_ALIASES["viewer-activity"]).toBe("viewers");
      expect(HASH_ALIASES["stream-quality"]).toBe("quality");
      expect(HASH_ALIASES["outcomes-viewing"]).toBe("outcomes");
    });

    it("resolves DOM elements by ID, canonical ID, and data-section attributes", () => {
      const div = document.createElement("div");
      div.id = "registrations";
      div.setAttribute("data-section", "registrations");
      document.body.appendChild(div);

      expect(resolveTargetElement("registrations")).toBe(div);
      expect(resolveTargetElement("invitations")).toBe(div);
    });

    it("finds a section by its data-section alias when it has no matching id", () => {
      document.body.innerHTML = `<div data-section="live-monitor"></div><div id="x" data-section="stream-health"></div>`;
      expect(resolveTargetElement("monitor")).toBe(document.querySelector("[data-section='live-monitor']"));
      expect(resolveTargetElement("sessions")).toBe(document.getElementById("x"));
    });

    it("compares URL input instead of building selectors from it: quotes, brackets and CSS syntax neither throw nor match", () => {
      document.body.innerHTML = `<section id="details"></section><div data-section="hub"></div>`;
      const hostile = ['a"b]', "a'b", "]", "[", "a\\b", ".x>y", ":hover", "#details", "a b", "*", '"]', "',[data-section]"];
      for (const id of hostile) {
        expect(() => resolveTargetElement(id)).not.toThrow();
        expect(resolveTargetElement(id)).toBeNull();
      }
      // Object.prototype members are not aliases.
      for (const id of ["constructor", "__proto__", "toString", "hasOwnProperty"]) {
        expect(resolveTargetElement(id)).toBeNull();
      }
      expect(resolveTargetElement("")).toBeNull();
      expect(resolveTargetElement(undefined)).toBeNull();
    });

    it("still finds an element whose id or data-section holds CSS-special characters", () => {
      const odd = document.createElement("div");
      odd.id = 'a"b]';
      const section = document.createElement("div");
      section.setAttribute("data-section", "x'[y]");
      document.body.append(odd, section);
      expect(resolveTargetElement('a"b]')).toBe(odd);
      expect(resolveTargetElement("x'[y]")).toBe(section);
    });

    it("scrollToSection('top') scrolls both container and window smoothly to 0", () => {
      document.body.innerHTML = `<main class="zk-guide-safe"></main>`;
      document.querySelector("main").scrollTop = 300;
      windowY = 500;

      expect(scrollToSection("top")).toBe(true);
      expect(scrolls.map((c) => [c.by === "window" ? "window" : c.by.tagName, c.top])).toEqual([["MAIN", 0], ["window", 0]]);
    });

    it("window scroller: clears the sticky top bar, or uses the target's own scroll-margin", () => {
      shell = "window";
      document.body.innerHTML = `<header class="sticky top-0"></header><main class="zk-guide-safe"><section id="s1"></section><section id="s2"></section></main>`;
      document.getElementById("s2").style.scrollMarginTop = "96px";
      windowY = 300;

      expect(scrollToSection("s1")).toBe(true);
      expect(scrolls).toEqual([expect.objectContaining({ by: "window", before: 300, top: 300 + topOf(document.getElementById("s1")) - 80 })]);

      scrolls = [];
      scrollToSection("s2");
      expect(scrolls).toHaveLength(1);
      expect(scrolls[0].by).toBe("window");
      expect(scrolls[0].top).toBe(scrolls[0].before + topOf(document.getElementById("s2")) - 96);
    });

    it("<main> scroller: scrolls only <main>, and does not clear a top bar that sits outside it", () => {
      shell = "main";
      document.body.innerHTML = `<header class="sticky top-0"></header><main class="zk-guide-safe"><section id="s1"></section></main>`;
      const main = document.querySelector("main");
      main.scrollTop = 50;

      scrollToSection("s1");
      expect(scrolls).toHaveLength(1);
      expect(scrolls[0]).toEqual(expect.objectContaining({ by: main, before: 50, top: 50 + topOf(document.getElementById("s1")) - MAIN_TOP - GAP }));
    });

    it("<main> scroller: clears a sticky header that is inside <main>", () => {
      shell = "main";
      document.body.innerHTML = `<main class="zk-guide-safe"><header class="sticky top-0"></header><section id="s1"></section></main>`;
      scrollToSection("s1");
      expect(scrolls).toEqual([expect.objectContaining({ top: topOf(document.getElementById("s1")) - MAIN_TOP - (HEADER_HEIGHT + GAP) })]);
    });

    it("returns false and scrolls nothing for a missing target", () => {
      document.body.innerHTML = `<main class="zk-guide-safe"></main>`;
      expect(scrollToSection("nowhere")).toBe(false);
      expect(scrolls).toEqual([]);
    });
  });

  describe("2. Preparation section sub-heading navigation", () => {
    it("scrolls to each Preparation sub-heading's own element, and back to the top for the hub", async () => {
      const user = userEvent.setup();
      renderWorkspace([`${BASE}/preparation`]);
      const rail = await workspaceRail();
      await mountedAtTop("details");

      await clickSubheading(user, rail, /Details & Schedule/i, { url: `${BASE}/preparation#details`, target: "details" });
      await clickSubheading(user, rail, /Sources & Ingest/i, { url: `${BASE}/preparation#sources`, target: "sources" });
      await clickSubheading(user, rail, /Readiness Checks/i, { url: `${BASE}/preparation#readiness`, target: "readiness" });
      await clickSubheading(user, rail, /Event team/i, { url: `${BASE}/preparation#team`, target: "team" });
      await clickSubheading(user, rail, /Preparation hub/i, { url: `${BASE}/preparation` });
    });
  });

  describe("3. Audience & Access section sub-heading navigation", () => {
    it("scrolls to each Audience Access sub-heading's own element, and back to the top for the overview", async () => {
      const user = userEvent.setup();
      renderWorkspace([`${BASE}/access`]);
      const rail = await workspaceRail();
      await mountedAtTop("viewer-preview");

      await clickSubheading(user, rail, /Viewer experience preview/i, { url: `${BASE}/access#viewer-preview`, target: "viewer-preview" });
      await clickSubheading(user, rail, /Access diagnostics/i, { url: `${BASE}/access#diagnostics`, target: "diagnostics" });
      await clickSubheading(user, rail, /Invitations/i, { url: `${BASE}/access#registrations`, target: "registrations" });
      await clickSubheading(user, rail, /Links & revocation/i, { url: `${BASE}/access#access-links`, target: "access-links" });
      await clickSubheading(user, rail, /Access overview/i, { url: `${BASE}/access` });
    });
  });

  describe("4. Live Control section sub-heading navigation", () => {
    it("scrolls to each Live Control sub-heading's own element", async () => {
      const user = userEvent.setup();
      renderWorkspace([`${BASE}/live`]);
      const rail = await workspaceRail();
      await mountedAtTop("sessions");

      await clickSubheading(user, rail, /Live monitor/i, { url: `${BASE}/live#monitor`, target: "monitor" });
      await clickSubheading(user, rail, /Stream health & sessions/i, { url: `${BASE}/live#sessions`, target: "sessions" });
      await clickSubheading(user, rail, /Alerts & incidents/i, { url: `${BASE}/live#incidents`, target: "incidents" });
      await clickSubheading(user, rail, /Recovery procedures/i, { url: `${BASE}/live#recovery`, target: "recovery" });
    });

    it("an alias in the URL (#stream-health) scrolls to the canonical section", async () => {
      renderWorkspace([`${BASE}/live#stream-health`]);
      await workspaceRail();
      await expectScrolledTo("sessions");
      expect(route()).toBe(`${BASE}/live#stream-health`);
    });
  });

  describe("5. Replay & Assets section sub-heading navigation", () => {
    it("scrolls to each Replay sub-heading's own element, and back to the top for the overview", async () => {
      const user = userEvent.setup();
      renderWorkspace([`${BASE}/replay`]);
      const rail = await workspaceRail();
      await mountedAtTop("recordings");

      await clickSubheading(user, rail, /Recordings & processing/i, { url: `${BASE}/replay#recordings`, target: "recordings" });
      await clickSubheading(user, rail, /^Reports$/i, { url: `${BASE}/replay#reports`, target: "reports" });
      await clickSubheading(user, rail, /Retention & export/i, { url: `${BASE}/replay#retention`, target: "retention" });
      await clickSubheading(user, rail, /Replay overview/i, { url: `${BASE}/replay` });
    });
  });

  describe("6. Insights section sub-heading navigation", () => {
    it("scrolls to each Insights sub-heading's own element, Viewer feedback included", async () => {
      const user = userEvent.setup();
      renderWorkspace([`${BASE}/insights`]);
      const rail = await workspaceRail();
      await mountedAtTop("feedback");

      await clickSubheading(user, rail, /Insights overview/i, { url: `${BASE}/insights#overview`, target: "overview" });
      await clickSubheading(user, rail, /Viewer activity/i, { url: `${BASE}/insights#viewers`, target: "viewers" });
      await clickSubheading(user, rail, /Stream quality/i, { url: `${BASE}/insights#quality`, target: "quality" });
      await clickSubheading(user, rail, /Event outcomes/i, { url: `${BASE}/insights#outcomes`, target: "outcomes" });
      // #feedback is a supported hash: it stays, and is not normalized away to #overview.
      await clickSubheading(user, rail, /Viewer feedback/i, { url: `${BASE}/insights#feedback`, target: "feedback" });
    });
  });

  describe("7. Activity & Audit section sub-heading navigation", () => {
    it("applies each filter at the top of the timeline, and the timeline item drops the filter", async () => {
      const user = userEvent.setup();
      renderWorkspace([`${BASE}/activity`]);
      const rail = await workspaceRail();
      await mountedAtTop("timeline");

      await clickSubheading(user, rail, /Audit decisions/i, { url: `${BASE}/activity?outcome=refused` });
      await clickSubheading(user, rail, /Evidence records/i, { url: `${BASE}/activity?category=evidence` });
      await clickSubheading(user, rail, /Activity timeline/i, { url: `${BASE}/activity` });
    });
  });

  describe("8. Browser history", () => {
    it("Back restores and scrolls the section left through a page without a hash; Forward returns to that page's top", async () => {
      const user = userEvent.setup();
      renderWorkspace([`${BASE}/preparation#details`]);
      const rail = await workspaceRail();
      await expectScrolledTo("details");                                  // 1. opened with a valid hash

      scrolls = [];
      await user.click(within(rail).getByRole("link", { name: /^Live Control/ }));
      await waitFor(() => expect(route()).toBe(`${BASE}/live`));          // 2. a page without a hash
      await expectScrolledToTop();

      scrolls = [];
      await user.click(screen.getByTestId("btn-back"));                    // 3. browser Back
      await waitFor(() => expect(route()).toBe(`${BASE}/preparation#details`));
      await expectScrolledTo("details");                                  // 4. restored AND scrolled

      scrolls = [];
      await user.click(screen.getByTestId("btn-forward"));                 // 5. browser Forward
      await waitFor(() => expect(route()).toBe(`${BASE}/live`));
      await expectScrolledToTop();                                        // 6. destination restored
    });

    // Audience & Access scrolls as soon as a target exists (no `ready` gate), so a stale
    // "already scrolled" memory cannot be used up by a not-yet-ready run, as it can on Preparation.
    it("walks Back and Forward across several hashed entries, scrolling each one", async () => {
      const user = userEvent.setup();
      renderWorkspace([`${BASE}/access#diagnostics`]);
      const rail = await workspaceRail();
      await expectScrolledTo("diagnostics");

      await clickSubheading(user, rail, /Invitations/i, { url: `${BASE}/access#registrations`, target: "registrations" });
      scrolls = [];
      await user.click(within(rail).getByRole("link", { name: /^Live Control/ }));
      await waitFor(() => expect(route()).toBe(`${BASE}/live`));
      await expectScrolledToTop();

      for (const [button, url, target] of [
        ["btn-back", `${BASE}/access#registrations`, "registrations"],
        ["btn-back", `${BASE}/access#diagnostics`, "diagnostics"],
        ["btn-forward", `${BASE}/access#registrations`, "registrations"],
        ["btn-forward", `${BASE}/live`, null],
      ]) {
        scrolls = [];
        await user.click(screen.getByTestId(button));
        await waitFor(() => expect(route()).toBe(url));
        if (target) await expectScrolledTo(target);
        else await expectScrolledToTop();
      }
    });
  });

  describe("9. Repeated clicks", () => {
    it("a second click on the current sub-heading scrolls there again, once", async () => {
      const user = userEvent.setup();
      renderWorkspace([`${BASE}/preparation`]);
      const rail = await workspaceRail();
      await mountedAtTop("readiness");

      await clickSubheading(user, rail, /Readiness Checks/i, { url: `${BASE}/preparation#readiness`, target: "readiness" });
      // Same URL: no hash change follows, so the click itself scrolled (still on #readiness).
      await clickSubheading(user, rail, /Readiness Checks/i, { url: `${BASE}/preparation#readiness`, target: "readiness" });
      expect(scrolls[0].at).toBe(`${BASE}/preparation#readiness`);
    });

    it("a second click on a top-of-section item goes back to the top again", async () => {
      const user = userEvent.setup();
      renderWorkspace([`${BASE}/preparation#team`]);
      const rail = await workspaceRail();
      await expectScrolledTo("team");

      await clickSubheading(user, rail, /Preparation hub/i, { url: `${BASE}/preparation` });
      windowY = 900;                         // the user scrolls down again
      await clickSubheading(user, rail, /Preparation hub/i, { url: `${BASE}/preparation` });
    });
  });

  describe("10. Cross-section navigation", () => {
    it("a rail link to another section scrolls only the destination page, after navigating", async () => {
      const user = userEvent.setup();
      renderWorkspace([`${BASE}/replay`]);
      const rail = await workspaceRail();
      await mountedAtTop("recordings");

      scrolls = [];
      await user.click(within(rail).getByRole("link", { name: /^Viewer preview$/ }));
      await waitFor(() => expect(route()).toBe(`${BASE}/access#viewer-preview`));
      const call = await expectScrolledTo("viewer-preview");
      expect(call.at).toBe(`${BASE}/access#viewer-preview`);   // not while Replay was showing
    });

    it("a section link from a page without a hash moves only the destination to its top", async () => {
      const user = userEvent.setup();
      renderWorkspace([`${BASE}/replay`]);
      const rail = await workspaceRail();
      await mountedAtTop("recordings");

      scrolls = [];
      await user.click(within(rail).getByRole("link", { name: /^Insights/ }));
      await waitFor(() => expect(route()).toBe(`${BASE}/insights`));
      await expectScrolledToTop();
      expect(scrolls.every((c) => c.at === `${BASE}/insights`)).toBe(true);   // nothing while on Replay
    });

    it("a link to another section's #overview scrolls that section's #overview, not the one being left", async () => {
      const user = userEvent.setup();
      renderWorkspace([`${BASE}/replay`]);
      await workspaceRail();
      await mountedAtTop("overview");
      const replayOverview = document.getElementById("overview");

      scrolls = [];
      await act(async () => goTo(`${BASE}/insights#overview`));
      await waitFor(() => expect(route()).toBe(`${BASE}/insights#overview`));
      const call = await expectScrolledTo("overview");          // waits for Insights' data to mount it
      expect(document.getElementById("overview")).not.toBe(replayOverview);
      expect(call.at).toBe(`${BASE}/insights#overview`);

      // Back to Replay (no hash) and Forward again: the same hash is scrolled again, not skipped.
      scrolls = [];
      await user.click(screen.getByTestId("btn-back"));
      await waitFor(() => expect(route()).toBe(`${BASE}/replay`));
      await expectScrolledToTop();
      scrolls = [];
      await user.click(screen.getByTestId("btn-forward"));
      await waitFor(() => expect(route()).toBe(`${BASE}/insights#overview`));
      await expectScrolledTo("overview");
    });
  });

  describe("11. A shell whose <main> is the scroller", () => {
    it("scrolls <main>, never the window as well, and does not clear the top bar outside it", async () => {
      const user = userEvent.setup();
      renderWorkspace([`${BASE}/live#sessions`], { scroller: "main" });
      const rail = await workspaceRail();
      await expectScrolledTo("sessions", { scroller: "main" });

      scrolls = [];
      await user.click(within(rail).getByRole("link", { name: /Alerts & incidents/i }));
      await waitFor(() => expect(route()).toBe(`${BASE}/live#incidents`));
      await expectScrolledTo("incidents", { scroller: "main" });
    });
  });

  describe("12. Malformed and unknown hashes", () => {
    const HOSTILE = [
      "#a%22b%5D", "#%", "#section%ZZ", "#unknown-section", "#a'b", "#a%5Bb%5D",
      "#.x%3Ey", "#:hover", "#a%20b", "#constructor",
    ];

    it.each([
      ["preparation", "hub"],
      ["access", "overview"],
      ["replay", "overview"],
      ["activity", "timeline"],
    ])("%s keeps rendering through every one, scrolling nowhere", async (slug, rootId) => {
      renderWorkspace([`${BASE}/${slug}`]);
      await workspaceRail();
      await mountedAtTop(rootId);

      for (const hash of HOSTILE) {
        scrolls = [];
        await act(async () => goTo(`${BASE}/${slug}${hash}`));
        await waitFor(() => expect(route()).toBe(`${BASE}/${slug}${hash}`));
        await act(async () => {});
        expect(screen.queryByText(/Workspace crashed/)).toBeNull();
        expect(document.getElementById(rootId)).not.toBeNull();
        expect(scrolls).toEqual([]);
      }
    });

    it.each([
      ["live", "monitor", "sessions"],
      ["insights", "overview", "feedback"],
    ])("%s normalizes each one to #%s", async (slug, fallback, probeId) => {
      renderWorkspace([`${BASE}/${slug}`]);
      await workspaceRail();
      await mountedAtTop(probeId);

      for (const hash of HOSTILE) {
        await act(async () => goTo(`${BASE}/${slug}${hash}`));
        await waitFor(() => expect(route()).toBe(`${BASE}/${slug}#${fallback}`));
        expect(screen.queryByText(/Workspace crashed/)).toBeNull();
      }
    });
  });

  describe("13. Targets that are missing or render late", () => {
    function LatePage() {
      const [shown, setShown] = useState(false);
      useHashScroll();
      return (
        <div>
          <button type="button" onClick={() => setShown(true)}>Show section</button>
          {shown && <section id="late">Late section</section>}
        </div>
      );
    }

    it("waits for a target that mounts later, then scrolls to it once", async () => {
      const user = userEvent.setup();
      render(
        <MemoryRouter initialEntries={["/probe#late"]}>
          <LocationDisplay />
          <header className="sticky top-0">Top bar</header>
          <main className="zk-guide-safe">
            <Routes>
              <Route path="/probe" element={<LatePage />} />
            </Routes>
          </main>
        </MemoryRouter>
      );
      await act(async () => {});
      expect(scrolls).toEqual([]);           // missing for now: nothing scrolled, nothing thrown

      await user.click(screen.getByRole("button", { name: "Show section" }));
      await expectScrolledTo("late");
    });
  });

  // Viewer feedback renders at once; every Insights panel above it mounts only when the insights
  // request answers. Here layout follows document order, so those panels really push #feedback
  // down, and positions are viewport positions (the page position minus the window's scroll).
  describe("14. Insights #feedback with the insights data arriving late", () => {
    let pending;
    const releaseInsights = () => act(async () => { pending.splice(0).forEach((release) => release()); });
    const feedbackTop = () => document.getElementById("feedback").getBoundingClientRect().top;
    const measured = () => screen.findByText(/Measured from this event's own sessions/);

    beforeEach(() => {
      pending = [];
      const base = api.get.getMockImplementation();
      api.get.mockImplementation((url, ...rest) => {
        if (url !== "/organization/events/e1/insights") return base(url, ...rest);
        return new Promise((resolve) => { pending.push(() => resolve(base(url))); });
      });
      rectSpy.mockImplementation(function () {
        if (this.tagName === "HEADER" && this.classList.contains("sticky")) return rect(0, HEADER_HEIGHT);
        const pageTop = 30 * Array.prototype.indexOf.call(document.body.getElementsByTagName("*"), this);
        return rect(pageTop - windowY, 30);
      });
    });

    // scroll-mt-24 in the browser; jsdom applies no Tailwind CSS.
    async function openFeedbackFirst() {
      await mounted("feedback");
      document.getElementById("feedback").style.scrollMarginTop = "96px";
      await act(async () => {});
    }

    it("direct #feedback: nothing scrolls until the panels above mount, then one scroll leaves it at 96px", async () => {
      renderWorkspace([`${BASE}/insights#feedback`]);
      await workspaceRail();
      await openFeedbackFirst();
      expect(scrolls).toEqual([]);                      // not toward a place about to move
      const pageTopBefore = feedbackTop() + windowY;

      await releaseInsights();
      await measured();
      await waitFor(() => expect(scrolls).toHaveLength(1));
      expect(feedbackTop() + windowY).toBeGreaterThan(pageTopBefore);   // the panels did push it down
      expect(scrolls[0].by).toBe("window");
      expect(feedbackTop()).toBe(96);                    // clear of the 64px bar, at its scroll margin

      // Settled: later renders (its own list, the rest of the page) neither scroll nor move it.
      await act(async () => {});
      await act(async () => {});
      expect(scrolls).toHaveLength(1);
      expect(feedbackTop()).toBe(96);
      expect(route()).toBe(`${BASE}/insights#feedback`);
    });

    it("Back to #feedback re-fetches, waits again, and lands at 96px; Forward returns to the other page's top", async () => {
      const user = userEvent.setup();
      renderWorkspace([`${BASE}/insights#feedback`]);
      const rail = await workspaceRail();
      await openFeedbackFirst();
      await releaseInsights();
      await measured();
      await waitFor(() => expect(feedbackTop()).toBe(96));

      scrolls = [];
      await user.click(within(rail).getByRole("link", { name: /^Replay & Assets/ }));
      await waitFor(() => expect(route()).toBe(`${BASE}/replay`));
      await expectScrolledToTop();

      scrolls = [];
      await user.click(screen.getByTestId("btn-back"));
      await waitFor(() => expect(route()).toBe(`${BASE}/insights#feedback`));
      await openFeedbackFirst();
      expect(scrolls).toEqual([]);
      await releaseInsights();
      await measured();
      await waitFor(() => expect(scrolls).toHaveLength(1));
      expect(feedbackTop()).toBe(96);

      scrolls = [];
      await user.click(screen.getByTestId("btn-forward"));
      await waitFor(() => expect(route()).toBe(`${BASE}/replay`));
      await expectScrolledToTop();
    });

    it("without a hash: the page opens at its top and the late panels scroll nothing", async () => {
      renderWorkspace([`${BASE}/insights`]);
      await workspaceRail();
      await mounted("feedback");
      await expectScrolledToTop();
      scrolls = [];

      await releaseInsights();
      await measured();
      await act(async () => {});
      expect(scrolls).toEqual([]);
    });
  });
});
