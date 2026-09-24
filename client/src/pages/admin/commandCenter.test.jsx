// The Command Center, pinned as an OVERVIEW rather than a second admin portal.
//
// It had accumulated a copy of six other consoles: a lifecycle rail and an 8x4 stage matrix
// (System Status), a full incidents feed (System Status / Trust & Safety), four action
// queues (Support Operations), five rows of governance exposure (Governance), and a live
// audit feed (Audit). On a quiet platform that is three screens of panels whose entire
// content is a sentence saying there is nothing to do.
//
// Two classes of assertion below, and the second is the one that will actually catch a
// regression. The first says the five questions this page exists to answer still get
// answered. The second says the detail pages' work has NOT crept back in — written as
// negative assertions against the specific strings those panels rendered, because "we
// removed a section" is only true until someone helpfully adds it again.
import { render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";

vi.mock("../../api", () => ({
  default: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
  errMsg: (e) => e?.message ?? "failed",
  diagnoseLoadError: (e) => e?.message ?? "failed to load",
}));

import api from "../../api";
import { ThemeProvider } from "../../theme/ThemeContext";
import AdminDashboard from "./Dashboard";

// A payload shaped exactly like services/ops.command_center returns, including the keys the
// page no longer renders — the server still sends them and other pages still use them, so
// the test feeds them in and asserts they are NOT displayed here.
const payload = (over = {}) => ({
  generated_at: new Date().toISOString(),
  regions: [
    { code: "na", label: "NA" },
    { code: "eu", label: "EU" },
  ],
  lifecycle: [
    { stage: "contribute", label: "Contribute", status: "ok", availability: 100, regions: { na: 100, eu: 100 }, services: [], open_incidents: 0 },
    { stage: "deliver", label: "Deliver", status: "ok", availability: 100, regions: { na: 100, eu: 100 }, services: [], open_incidents: 0 },
  ],
  kpis: {
    platform_health: { status: "ok", ok: 4, total: 8, unavailable: 0, series: [] },
    live_sessions: { value: 2, starting_soon: 1, unattended: 0, series: [] },
    at_risk_sessions: { total: 0, critical: 0, high: 0, monitoring: 0 },
    concurrent_audience: { value: 40, peak: 55, series: [] },
    playback_quality: { value: null, note: "Playback QoE needs a player beacon writing platform_metrics (no ingest yet)." },
    api_health: { value: 0, p95_ms: 558, requests: 88, series: [] },
  },
  attention: [],
  incidents: [],
  action_queues: [
    { key: "operational", label: "Operational", count: 0, items: [] },
    { key: "security", label: "Security", count: 0, items: [] },
  ],
  governance: {
    usage_export_on_time_pct: null,
    usage_export_total: 0,
    legal_holds: 0,
    entitlement_overrides_pending: 0,
    break_glass_under_review: 0,
    single_path_overrides_quarter: 0,
  },
  privileged_activity: [],
  upcoming_events: [],
  elevation: null,
  ...over,
});

const serve = (data) =>
  vi.mocked(api.get).mockImplementation((url) =>
    url === "/admin/command-center" ? Promise.resolve({ data }) : Promise.resolve({ data: {} })
  );

const show = () =>
  render(
    <ThemeProvider>
      <MemoryRouter>
        <AdminDashboard />
      </MemoryRouter>
    </ThemeProvider>
  );

beforeEach(() => {
  vi.clearAllMocks();
  serve(payload());
});

// ── the five questions still get answered ──────────────────────────────────────────────

describe("the overview still answers what it is for", () => {
  it("keeps the five measured KPI cards", async () => {
    show();
    for (const label of [
      "Platform health",
      "Live sessions",
      "At-risk sessions",
      "Concurrent audience",
      "API health",
    ]) {
      expect(await screen.findByText(label), label).toBeInTheDocument();
    }
  });

  it("offers Open Live Operations", async () => {
    show();
    // Two links lead there: this header CTA and the attention panel's "→" action. Exact
    // name picks the CTA; the panel one is asserted in its own test.
    const cta = await screen.findByRole("link", { name: "Open Live Operations" });
    expect(cta).toHaveAttribute("href", "/admin/live-events");
  });

  it("keeps the freshness receipt, so nobody reads a stale page as current", async () => {
    show();
    expect(await screen.findByText(/Refreshed/)).toBeInTheDocument();
    expect(screen.getByText(/within SLO/)).toBeInTheDocument();
  });

  it("keeps the window filters, which genuinely re-scope the request", async () => {
    show();
    await screen.findByText("Live sessions");
    expect(screen.getByRole("group", { name: /Time range/i })).toBeInTheDocument();
    expect(screen.getByLabelText("Region")).toBeInTheDocument();
    expect(screen.getByLabelText("Scope")).toBeInTheDocument();
    // The request carried them, rather than the page filtering an already-fetched blob.
    expect(api.get).toHaveBeenCalledWith(
      "/admin/command-center",
      expect.objectContaining({ params: expect.objectContaining({ range: "live", scope: "core_live" }) })
    );
  });
});

// ── the operational row ────────────────────────────────────────────────────────────────

describe("live sessions requiring attention", () => {
  it("lists a session that needs intervention, with a way into it", async () => {
    serve(
      payload({
        attention: [
          {
            id: "a1",
            event_id: "e1",
            event: "Quarterly All Hands",
            organization: "Northwind",
            severity: "critical",
            issue: "Publishing stopped",
            stage: "ingest",
            started_at: new Date(Date.now() - 120_000).toISOString(),
            owner: null,
          },
        ],
      })
    );
    show();

    expect(await screen.findByText("Quarterly All Hands")).toBeInTheDocument();
    expect(screen.getByText("Publishing stopped")).toBeInTheDocument();
    expect(screen.getByText("Critical")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Open Live Operations →/ })).toHaveAttribute(
      "href",
      "/admin/live-events"
    );
  });

  it("says nothing needs attention in one line, not a screenful", async () => {
    show();
    expect(await screen.findByText(/No live session needs attention/i)).toBeInTheDocument();
    // The old full-height empty state is gone.
    expect(screen.queryByText(/publishing, attended and capturing as configured/i)).not.toBeInTheDocument();
  });
});

// ── incidents: summary, not a second incident console ──────────────────────────────────

describe("incident summary", () => {
  it("reports the active count and the worst severity", async () => {
    serve(
      payload({
        incidents: [
          { id: "i1", title: "Edge latency in EU", severity: "sev3", status: "open", kind: "operational", started_at: new Date().toISOString() },
          { id: "i2", title: "Ingest failure", severity: "sev1", status: "investigating", kind: "operational", started_at: new Date().toISOString() },
          { id: "i3", title: "Old thing", severity: "sev2", status: "resolved", kind: "operational", started_at: new Date().toISOString() },
        ],
      })
    );
    show();

    // 2 active (the resolved one is not counted), worst is sev1.
    expect(await screen.findByText(/2 active/)).toBeInTheDocument();
    expect(screen.getByText("Critical")).toBeInTheDocument();
    expect(screen.getByText("Ingest failure")).toBeInTheDocument();
  });

  it("collapses to one honest line when nothing is active", async () => {
    show();
    // "recorded" on purpose: the console knows what was filed, not that the platform is perfect.
    expect(await screen.findByText(/No active incidents recorded/i)).toBeInTheDocument();
    expect(screen.queryByText(/No incidents in this window/i)).not.toBeInTheDocument();
  });

  it("links to System Status instead of reproducing the incident table", async () => {
    serve(
      payload({
        incidents: [
          { id: "i1", title: "Ingest failure", severity: "sev1", status: "open", kind: "operational", started_at: new Date().toISOString(), region: "eu", stage: "ingest", ref: "INC-41", commander: "Ada" },
        ],
      })
    );
    show();

    await screen.findByText("Ingest failure");
    expect(screen.getByRole("link", { name: /System Status →/ })).toHaveAttribute("href", "/admin/status");
    // The feed's per-incident detail line is not rendered here any more.
    expect(screen.queryByText(/INC-41/)).not.toBeInTheDocument();
    expect(screen.queryByText(/commander Ada/)).not.toBeInTheDocument();
  });
});

// ── upcoming ───────────────────────────────────────────────────────────────────────────

describe("upcoming high-impact events", () => {
  it("renders a scheduled high-impact event with its readiness verdict", async () => {
    serve(
      payload({
        upcoming_events: [
          {
            id: "ev1",
            title: "Investor Briefing",
            organization: "Northwind",
            impact: "unrepeatable",
            verdict: "blocked",
            failing: ["No host assigned"],
            start_time: new Date(Date.now() + 3600_000).toISOString(),
            timezone: "UTC",
          },
        ],
      })
    );
    show();

    expect(await screen.findByText("Investor Briefing")).toBeInTheDocument();
    expect(screen.getByText("Blocked")).toBeInTheDocument();
    expect(screen.getByText("Unrepeatable")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Event Readiness →/ })).toHaveAttribute(
      "href",
      "/admin/event-readiness"
    );
  });

  it("uses one compact line when nothing is scheduled", async () => {
    show();
    expect(await screen.findByText(/No high-impact events scheduled\./i)).toBeInTheDocument();
    expect(screen.queryByText(/appear here once scheduled/i)).not.toBeInTheDocument();
  });
});

// ── the duplication is gone, and must stay gone ────────────────────────────────────────

describe("detail that belongs to another console is not reproduced here", () => {
  it("does not render the Audit feed", async () => {
    serve(
      payload({
        privileged_activity: [
          { id: "p1", actor: "ZoikoStream Admin", department: "Super Admin", action: "live_session.force_end", target: "Event A", at: new Date().toISOString() },
          { id: "p2", actor: "ZoikoStream Admin", department: "Super Admin", action: "elevation.start", target: null, at: new Date().toISOString() },
        ],
      })
    );
    show();

    await screen.findByText("Live sessions");
    expect(screen.queryByText("Recent privileged activity")).not.toBeInTheDocument();
    expect(screen.queryByText(/Ended live session/i)).not.toBeInTheDocument();
    // Only the count survives, as a link into Audit.
    const audit = screen.getByRole("link", { name: /2 recent privileged actions/i });
    expect(audit).toHaveAttribute("href", "/admin/audit");
  });

  it("does not render the governance exposure breakdown", async () => {
    serve(
      payload({
        governance: {
          usage_export_on_time_pct: null,
          usage_export_total: 0,
          legal_holds: 2,
          entitlement_overrides_pending: 1,
          break_glass_under_review: 0,
          single_path_overrides_quarter: 0,
        },
      })
    );
    show();

    await screen.findByText("Live sessions");
    for (const row of [
      "Governance & commercial exposure",
      "Active legal holds",
      "Entitlement overrides pending approval",
      "Break-glass grants under 72h review",
      "Single-path overrides this quarter",
      "Usage export delivery",
    ]) {
      expect(screen.queryByText(row), row).not.toBeInTheDocument();
    }
    // One compact count, linking to the console that owns it.
    expect(screen.getByRole("link", { name: /3 governance actions/i })).toHaveAttribute(
      "href",
      "/admin/governance"
    );
  });

  it("does not render the lifecycle rail or the stage-health matrix", async () => {
    show();
    await screen.findByText("Live sessions");

    expect(screen.queryByText("Service health by lifecycle stage")).not.toBeInTheDocument();
    expect(screen.queryByText("Table view")).not.toBeInTheDocument();
    // The 100.00% wall, which services/ops.availability derives from "no incident was
    // filed", must not reappear on an overview as if it were measured uptime.
    expect(screen.queryByText("100.00%")).not.toBeInTheDocument();
    expect(screen.queryByText("Contribute")).not.toBeInTheDocument();
  });

  it("does not render the action-queue tabs", async () => {
    show();
    await screen.findByText("Live sessions");
    expect(screen.queryByText("Action queues")).not.toBeInTheDocument();
    expect(screen.queryByRole("tablist", { name: /Action queues/i })).not.toBeInTheDocument();
  });
});

// ── empty states cost nothing ──────────────────────────────────────────────────────────

describe("a quiet platform shows a quiet page", () => {
  it("renders no panel at all for queues that are clear", async () => {
    show();
    await screen.findByText("Live sessions");
    expect(screen.queryByText(/Queue is clear/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/Nothing is waiting on the/i)).not.toBeInTheDocument();
  });

  it("hides the zero counts entirely rather than printing 0", async () => {
    show();
    await screen.findByText("Live sessions");
    expect(screen.queryByRole("link", { name: /queued actions/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /governance actions/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /recent privileged actions/i })).not.toBeInTheDocument();
  });

  it("surfaces a count as soon as there is one", async () => {
    serve(
      payload({
        action_queues: [
          { key: "operational", label: "Operational", count: 3, items: [] },
          { key: "security", label: "Security", count: 1, items: [] },
        ],
      })
    );
    show();

    const link = await screen.findByRole("link", { name: /4 queued actions/i });
    expect(link).toHaveAttribute("href", "/admin/support");
  });

  it("drops the static operating-rules prose, which measured nothing", async () => {
    show();
    await screen.findByText("Live sessions");
    expect(screen.queryByText(/Live mode is the default/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/separate governed axes/i)).not.toBeInTheDocument();
  });
});

// ── honesty ────────────────────────────────────────────────────────────────────────────

describe("what remains is still honest", () => {
  it("drops Playback quality rather than showing a permanently unmeasured tile", async () => {
    show();
    await screen.findByText("Live sessions");
    expect(screen.queryByText("Playback quality")).not.toBeInTheDocument();
    expect(screen.queryByText(/no ingest yet/i)).not.toBeInTheDocument();
  });

  it("prints an em dash, never a fabricated number, when a KPI has no data", async () => {
    serve(
      payload({
        kpis: {
          platform_health: { status: null, ok: 0, total: 0, series: [] },
          live_sessions: { value: null, series: [] },
          at_risk_sessions: { total: null },
          concurrent_audience: { value: null, series: [] },
          api_health: { value: null, series: [] },
        },
      })
    );
    show();

    await screen.findByText("Platform health");
    // Every KPI with no data prints the em dash rather than 0 / "Healthy" / "100%".
    const tile = screen.getByText("Platform health").closest("section, article, div[class*='rounded']");
    expect(within(tile).getByText("—")).toBeInTheDocument();
    expect(screen.getByText(/No requests measured yet/i)).toBeInTheDocument();
  });

  it("still names the real failure when the endpoint does not load", async () => {
    vi.mocked(api.get).mockRejectedValue(new Error("Request failed with status code 403"));
    show();

    expect(await screen.findByText(/Couldn’t load the Command Center/i)).toBeInTheDocument();
    expect(screen.getByText(/Nothing on this page is safe to read/i)).toBeInTheDocument();
  });
});

// ── the pages that own the detail are still reachable ──────────────────────────────────

describe("the consoles that own the removed detail are one click away", () => {
  it("offers a shortcut to each", async () => {
    show();
    await screen.findByText("Live sessions");
    for (const [name, href] of [
      ["System Status", "/admin/status"],
      ["Audit", "/admin/audit"],
      ["Event Readiness", "/admin/event-readiness"],
      ["Live Operations", "/admin/live-events"],
    ]) {
      const links = screen.getAllByRole("link", { name });
      expect(links.some((l) => l.getAttribute("href") === href), name).toBe(true);
    }
  });
});
