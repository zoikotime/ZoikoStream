// The Event Workspace sections against the real layout (ZST-ENG-DASH-001 EVT-04, EVT-06,
// EVT-07 and the legacy ?tab= links), with the API mocked per URL.
//
// What these pin:
//   * Activity & Audit shows the server's decision in words, filters on the server, never lets
//     a slow answer to an older filter replace a newer one, re-authorizes a receipt on its own
//     request, and never claims integrity it has not established (EVT-07 QA A–I)
//   * Live Control says LIVE / NOT LIVE in words, marks an old reading STALE, and ends an event
//     only after an explicit confirmation (§9.2)
//   * Insights shows an unmeasured figure as "—" with its reason, and a measured zero as 0
//   * an old ?tab= link lands on the section that now holds that content
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../../api", () => ({
  default: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
  errMsg: (e) => e?.response?.data?.detail ?? e?.message ?? "Something went wrong.",
}));
vi.mock("../../../ui/Toast", () => ({ notify: { error: vi.fn(), success: vi.fn(), info: vi.fn() } }));
vi.mock("../../../auth/AuthContext", () => ({
  useAuth: () => ({ user: { id: "u1", full_name: "Ada Admin", email: "ada@example.com", role: "org_admin" }, logout: vi.fn() }),
}));

import api from "../../../api";
import { CONSOLE } from "../../../ui/tokens";
import { ThemeProvider } from "../../../theme/ThemeContext";
import EventWorkspaceLayout from "./EventWorkspaceLayout";
import EventOverview from "./EventOverview";
import EventLiveControl from "./EventLiveControl";
import EventInsights from "./EventInsights";
import EventActivity from "./EventActivity";
import { useHashScroll } from "./workspace";

const NOW = new Date().toISOString();
const EVENT = {
  id: "e1", title: "Quarterly town hall", status: "scheduled", display_status: "scheduled",
  start_time: "2026-11-02T17:00:00Z", timezone: "Europe/London", visibility: "public",
  recording_enabled: true,
};
const READINESS = {
  verdict: "blocked", readiness_state: "in_progress", evaluated_at: NOW,
  gates: [
    { key: "title", label: "Title set", passed: true, required: true },
    { key: "host", label: "Host assigned", passed: false, required: true },
  ],
};
const actor = { kind: "member", id: "u1", name: "Ada Admin", role: "org_admin", role_label: "Organization Owner" };
const entry = (over) => ({
  id: "a1", at: NOW, action: "event.update", title: "Event details edited", category: "production",
  category_label: "Production changes", outcome: "applied", outcome_label: "Applied", changed_state: true,
  high_impact: false, actor, details: [{ label: "Changed", value: "Title" }], reason: null,
  reference: "req_0123456789abcdef01234567", ...over,
});
const LEDGER = (items = [entry({}), entry({
  id: "a2", action: "event.end", title: "End event — rejected", outcome: "rejected", outcome_label: "Rejected",
  changed_state: false, reason: "The event was not live, so there was nothing to end.", details: [],
})]) => ({
  items, total: items.length, page: 1, page_size: 25,
  scope: { kind: "event", event_id: "e1", title: EVENT.title, status: "scheduled", timezone: EVENT.timezone },
  summary: {
    total: items.length, high_impact: 0, refused: 1, failed: 0, pending: 0,
    by_category: { access: 0, production: 2, evidence: 0, engagement: 0, commercial: 0, other: 0 },
    by_outcome: { applied: 1, rejected: 1 }, last_recorded_at: NOW,
  },
  actors: [{ value: "u1", label: "Ada Admin", count: 2 }],
  categories: [
    { value: "access", label: "Access & security" }, { value: "production", label: "Production changes" },
    { value: "evidence", label: "Evidence" }, { value: "engagement", label: "Audience interaction" },
    { value: "commercial", label: "Billing & commercial" }, { value: "other", label: "Other" },
  ],
  integrity: {
    tenant_scope: { state: "explicit", label: "Explicit in each record" },
    hash_verification: { state: "not_established", label: "Not established" },
    immutability: { state: "not_asserted", label: "Not asserted" },
    time_anchoring: { state: "not_verified", label: "Not verified" },
  },
  integrity_note: "A row appearing in this ledger is not proof of tamper-proof storage or independently verified evidence.",
  export: { available: false, state: "not_offered", label: "Not offered", message: "Audit evidence export is not available yet." },
  as_of: NOW,
});

let handlers;
let scrollTo;
beforeEach(() => {
  vi.clearAllMocks();
  scrollTo = vi.fn(function ({ top }) {
    this.scrollTop = top;
  });
  Object.defineProperty(HTMLElement.prototype, "scrollTo", {
    configurable: true,
    value: scrollTo,
  });
  handlers = {
    "/organization/console-state": () => ({ organization: { name: "Northwind" }, user: { role: "org_admin" }, badges: {}, health: { status: "ok", label: "Healthy" } }),
    "/events/e1": () => EVENT,
    "/organization/events/e1/readiness": () => READINESS,
    "/organization/events/e1/activity": () => LEDGER(),
  };
  vi.mocked(api.get).mockImplementation((url, cfg) => {
    const h = handlers[url];
    if (!h) return Promise.reject(Object.assign(new Error("not found"), { response: { status: 404 } }));
    const out = h(cfg?.params || {});
    return out instanceof Promise ? out : Promise.resolve({ data: out });
  });
});
afterEach(() => {
  vi.useRealTimers();
  delete HTMLElement.prototype.scrollTo;
});

function renderAt(path) {
  return render(
    <ThemeProvider>
      <MemoryRouter initialEntries={[path]}>
        <RouteProbe />
        <Routes>
          <Route path="/organization/events/:id" element={<EventWorkspaceLayout />}>
            <Route index element={<EventOverview />} />
            <Route path="live/*" element={<EventLiveControl />} />
            <Route path="insights" element={<EventInsights />} />
            <Route path="activity" element={<EventActivity />} />
            <Route path="access" element={<h1>access section</h1>} />
            <Route path="replay" element={<h1>replay section</h1>} />
            <Route path="preparation" element={<h1>preparation section</h1>} />
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

function HashScrollFallbackProbe() {
  useHashScroll({ supportedHashes: ["missing"], fallbackHash: "monitor" });
  return <div id="monitor" />;
}


const activityCalls = () => vi.mocked(api.get).mock.calls.filter(([u]) => u === "/organization/events/e1/activity");

describe("Event lifecycle navigation", () => {
  const stages = [
    ["Contribute", "/organization/events/e1/preparation#team", "Preparation"],
    ["Ingest", "/organization/events/e1/preparation#sources", "Preparation"],
    ["Produce", "/organization/events/e1/live", "Live Control"],
    ["Secure", "/organization/events/e1/access#diagnostics", "Audience Access"],
    ["Deliver", "/organization/events/e1/insights#quality", "Insights"],
    ["Understand", "/organization/events/e1/insights", "Insights"],
    ["Preserve", "/organization/events/e1/replay#retention", "Replay & Assets"],
  ];

  it("renders all seven stages with completed, current, and upcoming states", async () => {
    renderAt("/organization/events/e1");

    for (const [label] of stages) {
      expect(await screen.findByRole("link", { name: new RegExp(`^${label},`) })).toBeInTheDocument();
    }
    expect(screen.getByRole("link", { name: "Contribute, completed; open contribution setup" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Ingest, completed; open sources and ingest" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Produce, current stage; open Live Control" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Secure, upcoming; open access diagnostics" })).toBeInTheDocument();
    expect(screen.getByRole("link", { current: "step" })).toHaveTextContent("Produce");
    expect(screen.getAllByRole("link", { name: /^(Contribute|Ingest|Produce|Secure|Deliver|Understand|Preserve),/ }))
      .toHaveLength(7);
  });

  it.each(stages)("navigates %s to its existing event section without changing event state", async (label, destination, activeSection) => {
    renderAt("/organization/events/e1");
    const stage = await screen.findByRole("link", { name: new RegExp(`^${label},`) });

    await userEvent.click(stage);

    await waitFor(() => expect(screen.getByTestId("route-location")).toHaveTextContent(destination));
    const active = within(screen.getByRole("navigation", { name: "Event workspace" }))
      .getAllByRole("link")
      .find((link) => link.getAttribute("aria-current") === "page");
    expect(active).toHaveTextContent(activeSection);
    expect(api.post).not.toHaveBeenCalled();
    expect(api.patch).not.toHaveBeenCalled();
    expect(api.delete).not.toHaveBeenCalled();
  });

  it.each([
    ["draft", "Contribute"],
    ["scheduled", "Produce"],
    ["live", "Deliver"],
    ["ended", "Understand"],
    ["archived", "Preserve"],
  ])("reflects %s lifecycle status as the %s stage", async (status, currentStage) => {
    handlers["/events/e1"] = () => ({ ...EVENT, status, display_status: status });
    renderAt("/organization/events/e1");

    expect(await screen.findByRole("link", { current: "step" })).toHaveTextContent(currentStage);
  });

  it("uses a single Tab stop and arrow keys to select stages before Enter activation", async () => {
    renderAt("/organization/events/e1");
    const contribute = await screen.findByRole("link", { name: /^Contribute,/ });
    const ingest = screen.getByRole("link", { name: /^Ingest,/ });
    const stageLinks = screen.getAllByRole("link", { name: /^(Contribute|Ingest|Produce|Secure|Deliver|Understand|Preserve),/ });

    expect(stageLinks.filter((link) => link.tabIndex === 0)).toHaveLength(1);
    act(() => contribute.focus());
    await userEvent.keyboard("{ArrowRight}");
    expect(ingest).toHaveFocus();
    expect(screen.getByText("Selected stage: Ingest. Press Enter or Space to open.")).toBeInTheDocument();
    await userEvent.keyboard("{Enter}");
    await waitFor(() => expect(screen.getByTestId("route-location")).toHaveTextContent("/organization/events/e1/preparation#sources"));
  });

  it("supports Home/End focus movement and Space activation", async () => {
    renderAt("/organization/events/e1");
    const first = await screen.findByRole("link", { name: /^Contribute,/ });
    const preserve = screen.getByRole("link", { name: /^Preserve,/ });
    act(() => first.focus());
    await userEvent.keyboard("{End}");
    expect(preserve).toHaveFocus();
    await userEvent.keyboard("{Home}");
    expect(first).toHaveFocus();
    await userEvent.keyboard("{End}");
    expect(preserve).toHaveFocus();
    await userEvent.keyboard(" ");
    await waitFor(() => expect(screen.getByTestId("route-location")).toHaveTextContent("/organization/events/e1/replay#retention"));
  });

  it("supports browser back and forward after stage navigation", async () => {
    renderAt("/organization/events/e1");
    await userEvent.click(await screen.findByRole("link", { name: /^Secure,/ }));
    await waitFor(() => expect(screen.getByTestId("route-location")).toHaveTextContent("/organization/events/e1/access#diagnostics"));

    await userEvent.click(screen.getByRole("button", { name: "Browser back" }));
    await waitFor(() => expect(screen.getByTestId("route-location")).toHaveTextContent("/organization/events/e1"));
    await userEvent.click(screen.getByRole("button", { name: "Browser forward" }));
    await waitFor(() => expect(screen.getByTestId("route-location")).toHaveTextContent("/organization/events/e1/access#diagnostics"));
  });

  it("keeps Viewer Preview and Review Readiness actions unchanged", async () => {
    renderAt("/organization/events/e1");
    expect(await screen.findByRole("link", { name: "Viewer Preview" }))
      .toHaveAttribute("href", "/organization/events/e1/access#viewer-preview");
    expect(screen.getByRole("link", { name: "Review Readiness" }))
      .toHaveAttribute("href", "/organization/events/e1/preparation#readiness");
  });
});

describe("legacy ?tab= links", () => {
  it.each([
    ["Recording", "replay section"],
    ["Reports", "replay section"],
    ["Hosts", "preparation section"],
  ])("?tab=%s lands on the section that holds it", async (tab, heading) => {
    renderAt(`/organization/events/e1?tab=${tab}`);
    expect(await screen.findByRole("heading", { name: heading })).toBeInTheDocument();
  });
});

describe("Activity & Audit", () => {
  it("shows the server's decision in words, and never titles a refusal as the change", async () => {
    renderAt("/organization/events/e1/activity");
    const refused = await screen.findByRole("button", { name: "End event — rejected" });
    const row = refused.closest("tr");
    expect(within(row).getByText("Rejected")).toBeInTheDocument();
    expect(screen.queryByText("Event ended")).not.toBeInTheDocument();
    expect(screen.getByRole("group", { name: "Denied or rejected" })).toHaveTextContent("1");
  });

  it("the selected row's small print steps up to slate-600 on its violet tint (faint measured 4.49:1)", async () => {
    handlers["/organization/events/e1/activity/a2"] = () => entry({
      id: "a2", title: "End event — rejected", outcome: "rejected", outcome_label: "Rejected", changed_state: false,
      reason: "The event was not live, so there was nothing to end.", action: "event.end",
    });
    renderAt("/organization/events/e1/activity");
    const pick = await screen.findByRole("button", { name: "End event — rejected" });
    await userEvent.click(pick);
    const row = pick.closest("tr");
    await waitFor(() => expect(row).toHaveClass("bg-violet-50/70"));
    const small = [...row.querySelectorAll("p.text-\\[11px\\]")];
    expect(small.length).toBeGreaterThan(0);
    for (const p of small) expect(p).toHaveClass("text-slate-600");
    // Rows that are not selected keep the faint token.
    const other = screen.getByRole("button", { name: "Event details edited" }).closest("tr");
    for (const p of other.querySelectorAll("p.text-\\[11px\\]")) expect(p).toHaveClass(...CONSOLE.faint.split(" "));
  });

  it("the rail's back link names its destination on an AA step (white/40 on navy is ~3.7:1)", async () => {
    renderAt("/organization/events/e1/activity");
    const back = await screen.findByRole("link", { name: /all events/i });
    expect(within(back).getByText("Organization")).toHaveClass("text-white/55");
  });

  it("category counts take their tab's colour (faint on the selected tab's tint measured 2.4:1)", async () => {
    renderAt("/organization/events/e1/activity");
    await screen.findByRole("button", { name: "End event — rejected" });
    const counts = screen.getAllByRole("tab").map((tab) => tab.querySelector(".tabular-nums")).filter(Boolean);
    expect(counts.length).toBeGreaterThan(1);
    for (const count of counts) expect(count.className).not.toMatch(/(^|\s)text-(slate|neutral)-\d/);
  });

  it("filters on the server and starts again from the first page", async () => {
    renderAt("/organization/events/e1/activity");
    await screen.findByRole("button", { name: "Event details edited" });
    await userEvent.click(screen.getByRole("tab", { name: /^Access & security/ }));
    await waitFor(() => expect(activityCalls().at(-1)[1].params).toMatchObject({ category: "access", page: 1 }));
    await userEvent.selectOptions(screen.getByLabelText("Result"), "refused");
    await waitFor(() => expect(activityCalls().at(-1)[1].params).toMatchObject({ category: "access", outcome: "refused" }));
  });

  it("never lets a slow answer to an older filter replace a newer one", async () => {
    const pending = {};
    handlers["/organization/events/e1/activity"] = (params) => {
      if (!params.category) return LEDGER();
      return new Promise((resolve) => { pending[params.category] = resolve; });
    };
    renderAt("/organization/events/e1/activity");
    await screen.findByRole("button", { name: "Event details edited" });

    await userEvent.click(screen.getByRole("tab", { name: /^Access & security/ }));
    await userEvent.click(screen.getByRole("tab", { name: /^Evidence/ }));
    await waitFor(() => expect(pending.evidence).toBeTypeOf("function"));

    await act(async () => pending.evidence({ data: LEDGER([entry({ id: "ev", title: "Report generated", action: "report.generate" })]) }));
    await act(async () => pending.access({ data: LEDGER([entry({ id: "ac", title: "Access link created", action: "event.access_link.create" })]) }));

    expect(await screen.findByRole("button", { name: "Report generated" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Access link created" })).not.toBeInTheDocument();
  });

  it("reads a receipt on its own request, and is refused when the permission is gone", async () => {
    handlers["/organization/events/e1/activity/a2"] = () => entry({
      id: "a2", title: "End event — rejected", outcome: "rejected", outcome_label: "Rejected", changed_state: false,
      reason: "The event was not live, so there was nothing to end.", action: "event.end",
    });
    renderAt("/organization/events/e1/activity");
    await userEvent.click(await screen.findByRole("button", { name: "End event — rejected" }));
    const receipt = (await screen.findByText("Selected action receipt")).closest("div.flex.flex-col");
    expect(await within(receipt).findByText("Rejected — no change was applied")).toBeInTheDocument();
    expect(within(receipt).getByText("req_0123456789abcdef01234567")).toBeInTheDocument();
    expect(within(receipt).getByText(/nothing to end/)).toBeInTheDocument();

    handlers["/organization/events/e1/activity/a1"] = () => Promise.reject({ response: { status: 403 } });
    await userEvent.click(screen.getByRole("button", { name: "Event details edited" }));
    expect(await screen.findByText(/access required: you no longer have permission/i)).toBeInTheDocument();
  });

  it("states integrity as not established and offers no evidence download", async () => {
    renderAt("/organization/events/e1/activity");
    await screen.findByRole("button", { name: "Event details edited" });
    expect(screen.getByText("Not established")).toBeInTheDocument();
    expect(screen.getByText("Not asserted")).toBeInTheDocument();
    expect(screen.getByText("Not verified")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /request evidence export/i })).toBeDisabled();
    expect(screen.queryByRole("link", { name: /download/i })).not.toBeInTheDocument();
  });

  it("before anything has happened, says so instead of inventing activity", async () => {
    handlers["/organization/events/e1/activity"] = () => ({ ...LEDGER([]), summary: { ...LEDGER([]).summary, refused: 0 } });
    renderAt("/organization/events/e1/activity");
    expect(await screen.findByText("No activity recorded yet")).toBeInTheDocument();
  });

  it("explains a refusal to read the ledger as access required", async () => {
    handlers["/organization/events/e1/activity"] = () => Promise.reject({ response: { status: 403 } });
    renderAt("/organization/events/e1/activity");
    expect(await screen.findByText("Access required")).toBeInTheDocument();
  });
});

const LIVE = (over = {}) => ({
  event_id: "e1", status: "scheduled", on_air: false, as_of: new Date().toISOString(),
  current_session: null, sessions: [], session_count: 0, recording: null, recording_enabled: true,
  inputs: [], open_incidents: [], audience_now: null, audience_unavailable: false, ...over,
});

describe("Live Control", () => {
  const liveReady = () => {
    handlers["/organization/events/e1/live-status"] = () => LIVE();
  };
  const sectionLink = (name) => screen.getByRole("link", { name });
  const prepareScrollGeometry = (id) => {
    const target = document.getElementById(id);
    const main = document.querySelector("main");
    target.style.scrollMarginTop = "24px";
    vi.spyOn(target, "getBoundingClientRect").mockReturnValue({ top: 450 });
    vi.spyOn(main, "getBoundingClientRect").mockReturnValue({ top: 100 });
    main.scrollTop = 12;
    return main;
  };

  it.each([
    ["Live monitor", "monitor"],
    ["Stream health & sessions", "sessions"],
    ["Alerts & incidents", "incidents"],
    ["Recovery procedures", "recovery"],
  ])("clicking %s scrolls to the %s section and selects that sidebar item", async (label, id) => {
    liveReady();
    renderAt("/organization/events/e1/live");
    const link = await screen.findByRole("link", { name: label });
    await waitFor(() => expect(document.getElementById(id)).toBeInTheDocument());
    const main = prepareScrollGeometry(id);

    await userEvent.click(link);

    await waitFor(() => expect(screen.getByTestId("route-location")).toHaveTextContent(`/live#${id}`));
    await waitFor(() => expect(scrollTo).toHaveBeenCalled());
    expect(scrollTo.mock.contexts.some((element) => element === main)).toBe(true);
    expect(main.scrollTop).toBe(338);
    expect(link).toHaveAttribute("aria-current", "page");
    expect(api.post).not.toHaveBeenCalled();
    expect(api.patch).not.toHaveBeenCalled();
    expect(api.delete).not.toHaveBeenCalled();
  });

  it.each(["sessions", "incidents", "recovery"])("directly opening #%s scrolls to its section after data mounts", async (id) => {
    liveReady();
    renderAt(`/organization/events/e1/live#${id}`);

    await screen.findByRole("heading", {
      name: id === "sessions" ? "Stream health & sessions" : id === "incidents" ? "Incident & recovery desk" : "Recovery procedures",
    });
    await waitFor(() => expect(scrollTo.mock.contexts.some((element) => element.tagName === "MAIN")).toBe(true));
    expect(sectionLink(
      id === "sessions" ? "Stream health & sessions" : id === "incidents" ? "Alerts & incidents" : "Recovery procedures"
    ))
      .toHaveAttribute("aria-current", "page");
  });

  it("restores section scrolling and sidebar selection on browser back and forward", async () => {
    liveReady();
    renderAt("/organization/events/e1/live#sessions");
    await screen.findByRole("heading", { name: "Stream health & sessions" });
    scrollTo.mockClear();

    await userEvent.click(sectionLink("Alerts & incidents"));
    await waitFor(() => expect(screen.getByTestId("route-location")).toHaveTextContent("/live#incidents"));
    await waitFor(() => expect(scrollTo.mock.contexts.some((element) => element.tagName === "MAIN")).toBe(true));

    scrollTo.mockClear();
    await userEvent.click(screen.getByRole("button", { name: "Browser back" }));
    await waitFor(() => expect(screen.getByTestId("route-location")).toHaveTextContent("/live#sessions"));
    await waitFor(() => expect(scrollTo.mock.contexts.some((element) => element.tagName === "MAIN")).toBe(true));
    expect(sectionLink("Stream health & sessions")).toHaveAttribute("aria-current", "page");

    scrollTo.mockClear();
    await userEvent.click(screen.getByRole("button", { name: "Browser forward" }));
    await waitFor(() => expect(screen.getByTestId("route-location")).toHaveTextContent("/live#incidents"));
    await waitFor(() => expect(scrollTo.mock.contexts.some((element) => element.tagName === "MAIN")).toBe(true));
  });

  it("normalizes unknown hashes to the Live monitor fallback", async () => {
    liveReady();
    renderAt("/organization/events/e1/live#unknown");

    await waitFor(() => expect(screen.getByTestId("route-location")).toHaveTextContent("/live#monitor"));
    expect(await screen.findByRole("link", { name: "Live monitor" })).toHaveAttribute("aria-current", "page");
    await waitFor(() => expect(scrollTo.mock.contexts.some((element) => element.tagName === "MAIN")).toBe(true));
  });

  it("normalizes a valid hash whose section cannot mount to Live monitor", async () => {
    vi.useFakeTimers();
    render(
      <MemoryRouter initialEntries={["/organization/events/e1/live#missing"]}>
        <RouteProbe />
        <HashScrollFallbackProbe />
      </MemoryRouter>
    );
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000);
    });
    expect(screen.getByTestId("route-location")).toHaveTextContent("/live#monitor");
  });

  it("keeps Live Control destinations available for cancelled events without performing actions", async () => {
    handlers["/events/e1"] = () => ({ ...EVENT, status: "cancelled", display_status: "cancelled" });
    liveReady();
    renderAt("/organization/events/e1/live");

    await userEvent.click(await screen.findByRole("link", { name: "Alerts & incidents" }));

    expect(await screen.findByRole("heading", { name: "Incident & recovery desk" })).toBeInTheDocument();
    expect(screen.getByTestId("route-location")).toHaveTextContent("/live#incidents");
    expect(sectionLink("Alerts & incidents")).toHaveAttribute("aria-current", "page");
    expect(api.post).not.toHaveBeenCalled();
    expect(api.patch).not.toHaveBeenCalled();
    expect(api.delete).not.toHaveBeenCalled();
  });

  it("says NOT LIVE in words, links to the Producer Console, and names what blocks go-live", async () => {
    handlers["/organization/events/e1/live-status"] = () => LIVE();
    renderAt("/organization/events/e1/live");
    expect(await screen.findByText("NOT LIVE")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /open producer console/i })).toHaveAttribute("href", "/host/dashboard?event=e1");
    expect(screen.getByText(/cannot go live until/i)).toBeInTheDocument();
    expect(screen.getByText("Host assigned")).toBeInTheDocument();
    expect(await screen.findByText("No broadcast has started for this event.")).toBeInTheDocument();
  });

  it("marks an old reading STALE instead of keeping it current", async () => {
    handlers["/events/e1"] = () => ({ ...EVENT, status: "live", display_status: "live" });
    handlers["/organization/events/e1/live-status"] = () => LIVE({
      status: "live", on_air: true, as_of: new Date(Date.now() - 10 * 60 * 1000).toISOString(),
      current_session: { id: "s1", status: "live", health_level: "ok", on_air_seconds: 600, started_at: NOW, peak_viewers: 4 },
      audience_now: { viewers: 4, speakers: 0, hosts: 1, waiting: 0, poor_connections: 0 },
    });
    renderAt("/organization/events/e1/live");
    expect(await screen.findByText("LIVE", { selector: "p" })).toBeInTheDocument();
    const freshness = await screen.findByRole("group", { name: "Telemetry freshness" });
    expect(await within(freshness).findByText("Stale")).toBeInTheDocument();
  });

  it("ends a live event only after explicit confirmation", async () => {
    handlers["/events/e1"] = () => ({ ...EVENT, status: "live", display_status: "live" });
    handlers["/organization/events/e1/live-status"] = () => LIVE({ status: "live", on_air: true });
    vi.mocked(api.post).mockResolvedValue({ data: {}, headers: { "x-request-id": "req_end" } });
    renderAt("/organization/events/e1/live");
    await userEvent.click(await screen.findByRole("button", { name: "End Event" }));
    expect(api.post).not.toHaveBeenCalled();
    await userEvent.click(await screen.findByRole("button", { name: "Yes, end it now" }));
    await waitFor(() => expect(api.post).toHaveBeenCalledWith("/events/e1/end"));
    expect(api.post).toHaveBeenCalledTimes(1);
  });
});

describe("Insights", () => {
  const INSIGHTS = (over = {}) => ({
    event_id: "e1", as_of: NOW, measured: false,
    broadcast: { sessions: 0, on_air_seconds: null, first_started_at: null, last_ended_at: null },
    audience: { unique_viewers: 0, peak_viewers: null, avg_watch_seconds: null, total_watch_seconds: 0, returning_viewers: null },
    registration: { applicable: false, registered: 0, attended: 0, rate: null },
    geography: [], geography_unknown: 0, device_mix: [],
    blocked_join_attempts: { total: 0, reasons: [] },
    feedback: { responses: 0, ratings: 0, average_rating: null },
    playback: { live: { playback_starts: 0, rebuffer_ratio: null, startup_ms_median: null, failed_sessions: 0 },
                replay: { playback_starts: 0, rebuffer_ratio: null, startup_ms_median: null, failed_sessions: 0 } },
    ...over,
  });
  // A KPI tile (a named group), or a fact row (<dt>label</dt><dd>value</dd>) in the panels.
  const metric = (label) =>
    screen.queryByRole("group", { name: label }) || screen.getByText(label, { selector: "dt" }).parentElement;
  const serveInsights = (payload = INSIGHTS()) => {
    handlers["/organization/events/e1/insights"] = () => payload;
    handlers["/events/e1/feedback"] = () => [];
  };
  const tabLink = (name) => within(screen.getByRole("navigation", { name: "Insights sections" }))
    .getByRole("link", { name });
  const activeRailItem = (label) => within(screen.getByRole("navigation", { name: "Event workspace" }))
    .getByRole("link", { name: label });
  const activeTab = async (label) => {
    const tabs = await screen.findByRole("navigation", { name: "Insights sections" });
    const tab = await within(tabs)
      .findByRole("link", { name: label });
    expect(tab).toHaveAttribute("aria-current", "true");
    return tab;
  };

  it.each([
    ["Insights overview", "overview"],
    ["Viewer activity", "viewers"],
    ["Stream quality", "quality"],
    ["Event outcomes", "outcomes"],
  ])("clicking sidebar %s scrolls to its dashboard section and selects the matching tab", async (label, hash) => {
    serveInsights();
    renderAt("/organization/events/e1/insights");

    await userEvent.click(await screen.findByRole("link", { name: label }));

    await waitFor(() => expect(screen.getByTestId("route-location")).toHaveTextContent(`/insights#${hash}`));
    const tab = await activeTab(hash === "overview" ? "Overview" : label);
    expect(tab).toHaveAttribute("href", `/organization/events/e1/insights#${hash}`);
    expect(activeRailItem(label)).toHaveAttribute("aria-current", "page");
    await waitFor(() => expect(scrollTo.mock.contexts.some((element) => element.tagName === "MAIN")).toBe(true));
    if (hash === "viewers") {
      expect(screen.getByText("No live samples were recorded.")).toBeInTheDocument();
    }
  });

  it.each(["overview", "viewers", "quality", "outcomes"])("direct navigation to #%s restores selection and section", async (hash) => {
    serveInsights();
    renderAt(`/organization/events/e1/insights#${hash}`);

    const label = {
      overview: "Insights overview",
      viewers: "Viewer activity",
      quality: "Stream quality",
      outcomes: "Event outcomes",
    }[hash];
    await activeTab(hash === "overview" ? "Overview" : label);
    expect(activeRailItem(label)).toHaveAttribute("aria-current", "page");
    await waitFor(() => expect(scrollTo.mock.contexts.some((element) => element.tagName === "MAIN")).toBe(true));
  });

  it("restores Insights tab state on browser back and forward", async () => {
    serveInsights();
    renderAt("/organization/events/e1/insights#viewers");
    await activeTab("Viewer activity");

    await userEvent.click(tabLink("Stream quality"));
    await waitFor(() => expect(screen.getByTestId("route-location")).toHaveTextContent("/insights#quality"));
    await activeTab("Stream quality");

    await userEvent.click(screen.getByRole("button", { name: "Browser back" }));
    await waitFor(() => expect(screen.getByTestId("route-location")).toHaveTextContent("/insights#viewers"));
    await activeTab("Viewer activity");
    await userEvent.click(screen.getByRole("button", { name: "Browser forward" }));
    await waitFor(() => expect(screen.getByTestId("route-location")).toHaveTextContent("/insights#quality"));
    await activeTab("Stream quality");
  });

  it("normalizes unknown Insights hashes to #overview", async () => {
    serveInsights();
    renderAt("/organization/events/e1/insights#unknown");

    await waitFor(() => expect(screen.getByTestId("route-location")).toHaveTextContent("/insights#overview"));
    await activeTab("Overview");
    expect(activeRailItem("Insights overview")).toHaveAttribute("aria-current", "page");
  });

  it("shows an unmeasured figure as — with its reason, and a measured zero as 0", async () => {
    serveInsights();
    renderAt("/organization/events/e1/insights");
    await screen.findByText(/has not been broadcast/i);
    expect(metric("Peak viewers")).toHaveTextContent("—");
    expect(metric("Peak viewers")).toHaveTextContent("Not broadcast yet");
    expect(metric("Avg watch time")).toHaveTextContent("No viewers yet");
    expect(metric("Average rating")).toHaveTextContent("No ratings yet");
    expect(metric("Unique viewers")).toHaveTextContent("0");
    expect(metric("Unique viewers")).not.toHaveTextContent("—");
  });

  it("reports measured figures as the server gave them", async () => {
    serveInsights(INSIGHTS({
      measured: true,
      broadcast: { sessions: 1, on_air_seconds: 3600, first_started_at: NOW, last_ended_at: NOW },
      audience: { unique_viewers: 2, peak_viewers: 42, avg_watch_seconds: 900, total_watch_seconds: 1800, returning_viewers: 1 },
      feedback: { responses: 2, ratings: 2, average_rating: 4.5 },
    }));
    renderAt("/organization/events/e1/insights");
    await waitFor(() => expect(metric("Peak viewers")).toHaveTextContent("42"));
    expect(metric("Avg watch time")).toHaveTextContent("15m");
    expect(metric("Time on air")).toHaveTextContent("1h 00m");
    expect(metric("Average rating")).toHaveTextContent("4.5 / 5");
    expect(screen.queryByText(/has not been broadcast/i)).not.toBeInTheDocument();
  });
});
