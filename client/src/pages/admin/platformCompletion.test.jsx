// Super Admin -> PLATFORM, completion pass. Each page is pinned to its authoritative source:
// filters reach the SERVER, unknown reads as unknown, and a control is offered only when the
// server would accept it from this operator.
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Outlet, Route, Routes } from "react-router-dom";

vi.mock("../../api", () => ({
  default: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
  errMsg: (e) => e?.message ?? "failed",
  diagnoseLoadError: (e) => e?.message ?? "failed",
  AUTH_EXPIRED_EVENT: "zoiko:auth-expired",
}));

vi.mock("../../auth/AuthContext", () => ({
  useAuth: () => ({ user: { id: "u-me", full_name: "Ada Ops", role: "super_admin" }, logout: vi.fn() }),
}));

import api from "../../api";
import { ThemeProvider } from "../../theme/ThemeContext";
import UsageEntitlements from "./UsageEntitlements";
import Analytics from "./Analytics";
import SupportAccessPanel from "./SupportAccessPanel";
import RetentionPolicyPanel from "./RetentionPolicyPanel";
import Media from "./Media";
import Commerce from "./Commerce";

// Pages that read elevation get it the way the admin shell provides it: Outlet context.
const ACTIVE_ELEVATION = {
  state: { elevation: { id: "e1", scope: "platform", granted_scopes: ["platform"], seconds_remaining: 600 } },
  unknown: false,
  reload: vi.fn(),
};

const wrap = (node, outletContext) =>
  render(
    <ThemeProvider>
      <MemoryRouter>
        <Routes>
          <Route element={<Outlet context={outletContext} />}>
            <Route path="/" element={node} />
          </Route>
        </Routes>
      </MemoryRouter>
    </ThemeProvider>
  );

const callsTo = (url) => vi.mocked(api.get).mock.calls.filter(([u]) => u === url);
const lastParams = (url) => callsTo(url).at(-1)?.[1]?.params;

beforeEach(() => vi.clearAllMocks());

// ── Usage & Entitlements ───────────────────────────────────────────────────────────────

const quota = (over) => ({
  key: "storage", label: "Storage", used: 4, limit: null, unit: "GB", percent: null, remaining: null,
  unlimited: false, quota_state: "not_applicable", usage_state: "measured", enforced: true,
  enforced_limit: null, ...over,
});

const USAGE = {
  total: 60, page: 1, page_size: 25, sort: "name",
  items: [
    {
      org_id: "o1", organization: "Acme", is_test: false, plan: null, plan_slug: null,
      subscription_status: null, entitlement_source: "none", current_period_end: null, trial_ends_at: null,
      quotas: [
        quota({ enforced_limit: 100 }),
        quota({ key: "streaming_hours", label: "Streaming hours", unit: "hrs", enforced: false }),
      ],
      metrics: [
        { key: "events_created", label: "Events", value: 3, unit: "events", period: "lifetime", state: "measured" },
        { key: "recording_bytes", label: "Recording storage (known sizes)", value: 2048, unit: "bytes", period: "lifetime", state: "measured", unknown_count: 2 },
        { key: "delivery_windowed", label: "Delivery volume (windowed)", value: null, unit: "GB", period: null, state: "unavailable" },
      ],
    },
    {
      org_id: "o2", organization: "Globex", is_test: false, plan: "Pro", plan_slug: "pro",
      subscription_status: "active", entitlement_source: "subscription", current_period_end: null, trial_ends_at: null,
      quotas: [quota({ key: "members", label: "Members", used: 3, limit: 2, unit: "seats", percent: 150, remaining: -1, quota_state: "exceeded", enforced_limit: 2 })],
      metrics: [],
    },
  ],
};

describe("Usage & Entitlements", () => {
  beforeEach(() => vi.mocked(api.get).mockResolvedValue({ data: USAGE }));

  it("renders the server's quota states without inventing numbers", async () => {
    wrap(<UsageEntitlements plans={[{ id: "p1", slug: "pro", name: "Pro" }]} />);
    const acme = (await screen.findByText("Acme")).closest("li");
    // No plan is "not applicable", and the platform ceiling that still applies is stated.
    expect(within(acme).getAllByText("No entitled plan").length).toBeGreaterThan(0);
    expect(within(acme).getByText(/Enforced ceiling 100 GB/)).toBeInTheDocument();
    expect(within(acme).getByText(/Not enforced — shown against the plan only/)).toBeInTheDocument();
    // Unavailable is said, never rendered as 0.
    expect(within(within(acme).getByTestId("metric-delivery_windowed")).getByText("Not measured")).toBeInTheDocument();
    expect(within(acme).getByText(/\+ 2 with unknown size/)).toBeInTheDocument();
    const globex = screen.getByText("Globex").closest("li");
    expect(within(globex).getByText("Over quota")).toBeInTheDocument();
    expect(within(globex).getByText(/Over by 1 seats/)).toBeInTheDocument();
    expect(screen.getByTestId("usage-total")).toHaveTextContent("60 organizations · page 1 of 3");
  });

  it("sends plan, status, sort and page to the server", async () => {
    wrap(<UsageEntitlements plans={[{ id: "p1", slug: "pro", name: "Pro" }]} />);
    await screen.findByText("Acme");
    fireEvent.change(screen.getByLabelText("Filter by plan"), { target: { value: "pro" } });
    await waitFor(() => expect(lastParams("/admin/usage")).toMatchObject({ plan: "pro", page: 1 }));
    fireEvent.change(screen.getByLabelText("Filter by subscription status"), { target: { value: "trialing" } });
    await waitFor(() => expect(lastParams("/admin/usage")).toMatchObject({ status: "trialing" }));
    fireEvent.change(screen.getByLabelText("Sort organizations"), { target: { value: "storage" } });
    await waitFor(() => expect(lastParams("/admin/usage")).toMatchObject({ sort: "storage" }));
    fireEvent.click(screen.getByRole("button", { name: /Next/ }));
    await waitFor(() => expect(lastParams("/admin/usage")).toMatchObject({ page: 2 }));
  });
});

// ── Analytics ──────────────────────────────────────────────────────────────────────────

const ANALYTICS = {
  window: { range: "30d", since: "2026-08-26T00:00:00Z", until: "2026-09-25T00:00:00Z", org_id: null, bucket: "day" },
  mrr: 50, mrr_basis: "contracted_list_price", trials: { count: 1, list_value: 30 }, streaming_hours: 2.5,
  totals: {
    events_created: { value: 3, measurement: "measured" },
    broadcasts_completed: { value: 1, measurement: "measured" },
    recordings: { value: 1, measurement: "measured" },
    new_users: { value: 4, measurement: "measured" },
  },
  revenue: [], organizations: [], users: [], events: [],
  measurement: { mrr: "derived", revenue: "derived", streaming_hours: "measured" },
  unmeasured: ["Playback quality (QoE)", "CDN latency"],
  note: "Delivery telemetry is not collected on this platform.",
};

describe("Analytics", () => {
  beforeEach(() => {
    vi.mocked(api.get).mockImplementation((url) =>
      Promise.resolve({ data: url === "/admin/organizations" ? { items: [{ id: "org-9", name: "Initech" }] } : ANALYTICS }));
  });

  it("labels how each figure is known and lists what is not measured", async () => {
    wrap(<Analytics />);
    expect(await screen.findByTestId("unmeasured")).toHaveTextContent("Playback quality (QoE)");
    expect(screen.getAllByText("Measured").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Derived").length).toBeGreaterThan(0);
    expect(screen.getByTestId("trial-note")).toHaveTextContent(/not included in\s+Contracted MRR/);
    expect(screen.queryByRole("button", { name: /export/i })).not.toBeInTheDocument();
    expect(lastParams("/admin/analytics")).toMatchObject({ range: "30d" });
  });

  it("sends the window and the organization to the server", async () => {
    wrap(<Analytics />);
    await screen.findByTestId("unmeasured");
    fireEvent.click(screen.getByRole("button", { name: "7 days" }));
    await waitFor(() => expect(lastParams("/admin/analytics")).toMatchObject({ range: "7d" }));

    fireEvent.focus(screen.getByLabelText("Filter by organization"));
    fireEvent.change(screen.getByLabelText("Filter by organization"), { target: { value: "ini" } });
    fireEvent.click(await screen.findByRole("option", { name: "Initech" }));
    await waitFor(() => expect(lastParams("/admin/analytics")).toMatchObject({ range: "7d", org_id: "org-9" }));
    expect(lastParams("/admin/organizations")).toMatchObject({ q: "ini" });
  });

  it("does not request a custom window until it has a start date", async () => {
    wrap(<Analytics />);
    await screen.findByTestId("unmeasured");
    const before = callsTo("/admin/analytics").length;
    fireEvent.click(screen.getByRole("button", { name: "Custom" }));
    const apply = screen.getByRole("button", { name: "Apply" });
    expect(apply).toBeDisabled();
    fireEvent.change(screen.getByLabelText("From date"), { target: { value: "2026-09-01" } });
    fireEvent.change(screen.getByLabelText("To date"), { target: { value: "2026-09-10" } });
    fireEvent.click(apply);
    await waitFor(() => expect(lastParams("/admin/analytics")).toMatchObject({
      range: "custom", from: "2026-09-01T00:00:00Z", to: "2026-09-10T23:59:59Z" }));
    // Switching to Custom alone issued no request that the server would refuse.
    expect(callsTo("/admin/analytics").filter(([, o]) => o.params.range === "custom" && !o.params.from)).toHaveLength(0);
    expect(callsTo("/admin/analytics").length).toBeGreaterThan(before);
  });
});

// ── Support Operations: ORG-009 ────────────────────────────────────────────────────────

const req = (over) => ({
  id: "r", org_id: "o1", organization_name: "Acme", case_reference: "SUP-1", reason_category: "customer_reported_issue",
  engineer_display: "Someone", requested_scope: "Look at a report", requested_minutes: 30, status: "requested",
  requested_at: "2026-09-24T10:00:00Z", approved_at: null, approved_by_email: null, expires_at: null, ended_at: null,
  emergency: false, allowed_action_list: ["tenant.read"], is_mine: false, countersigned: false, ...over,
});

describe("Support access (ORG-009)", () => {
  beforeEach(() => {
    vi.mocked(api.get).mockResolvedValue({ data: {
      total: 4, page: 1, page_size: 100,
      summary: { requested: 7, approved: 1, active: 1, ended: 0, expired: 0, denied: 2 },
      items: [
        req({ id: "mine", case_reference: "SUP-MINE", status: "approved", is_mine: true, approved_by_email: "owner@acme.test" }),
        req({ id: "theirs", case_reference: "SUP-THEIRS", status: "approved", is_mine: false }),
        req({ id: "eg", case_reference: "SUP-EG", emergency: true, status: "requested" }),
        req({ id: "live", case_reference: "SUP-LIVE", status: "active", expires_at: "2026-09-25T12:00:00Z" }),
      ],
    } });
    vi.mocked(api.post).mockResolvedValue({ data: {} });
  });

  it("offers only the platform's own steps, and never approval", async () => {
    wrap(<SupportAccessPanel />);
    await screen.findByText("SUP-MINE");
    expect(screen.getAllByRole("button", { name: "Start session" })).toHaveLength(1);
    expect(within(screen.getByText("SUP-MINE").closest("tr")).getByRole("button", { name: "Start session" })).toBeInTheDocument();
    expect(within(screen.getByText("SUP-THEIRS").closest("tr")).queryByRole("button", { name: "Start session" })).toBeNull();
    expect(screen.getAllByRole("button", { name: "Countersign" })).toHaveLength(1);
    expect(screen.getAllByRole("button", { name: "End" })).toHaveLength(1);
    // Exact action names: the status chips ("Approved — not started: 1") are filters, not actions.
    expect(screen.queryByRole("button", { name: /^(approve|approve request|reject|deny|decline)$/i })).toBeNull();
    // Dataset-wide counts from the server, not the four rows on the page.
    expect(screen.getByTestId("support-access-summary")).toHaveTextContent("Awaiting organization approval: 7");

    fireEvent.click(screen.getByRole("button", { name: "Start session" }));
    await waitFor(() => expect(api.post).toHaveBeenCalledWith("/admin/support-access/mine/start"));
  });

  it("filters by status on the server", async () => {
    wrap(<SupportAccessPanel />);
    await screen.findByText("SUP-MINE");
    fireEvent.click(screen.getByRole("button", { name: /Denied: 2/ }));
    await waitFor(() => expect(lastParams("/admin/support-access")).toMatchObject({ status: "denied" }));
  });
});

// ── Platform Configuration: governed retention ─────────────────────────────────────────

const RETENTION = (proposal = null) => ({
  effective: { version: "default-v1", retention_days: 365, warning_days: 14, effective_at: null, approved_by_email: null },
  proposal,
  bounds: { min_retention_days: 30, max_retention_days: 3650, min_warning_days: 1 },
  impact: "Applies to recordings finalized after approval.",
});

describe("Retention policy panel", () => {
  it("refuses invalid values before they are sent and proposes valid ones", async () => {
    vi.mocked(api.get).mockResolvedValue({ data: RETENTION() });
    vi.mocked(api.post).mockResolvedValue({ data: RETENTION() });
    wrap(<RetentionPolicyPanel />, ACTIVE_ELEVATION);
    expect(await screen.findByTestId("retention-effective")).toHaveTextContent("365 days");
    const [days, warn] = screen.getAllByRole("spinbutton");
    const propose = screen.getByRole("button", { name: "Propose change" });

    fireEvent.change(days, { target: { value: "0" } });
    fireEvent.change(warn, { target: { value: "7" } });
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "Cost reduction request" } });
    expect(screen.getByTestId("retention-problems")).toHaveTextContent("between 30 and 3650");
    expect(propose).toBeDisabled();

    fireEvent.change(days, { target: { value: "" } });
    expect(propose).toBeDisabled();       // empty is not zero, and is not sent

    fireEvent.change(days, { target: { value: "400" } });
    expect(propose).toBeEnabled();
    fireEvent.click(propose);
    await waitFor(() => expect(api.post).toHaveBeenCalledWith("/admin/retention-policy/proposals",
      { retention_days: 400, warning_days: 7, reason: "Cost reduction request" }));
  });

  it("never offers approval to the person who proposed the change", async () => {
    vi.mocked(api.get).mockResolvedValue({ data: RETENTION({
      retention_days: 400, warning_days: 21, reason: "Contract", requested_by: "u-me",
      requested_by_email: "me@zoiko.test", requested_at: "2026-09-24T10:00:00Z",
      previous: { version: "default-v1", retention_days: 365, warning_days: 14 } }) });
    wrap(<RetentionPolicyPanel />, ACTIVE_ELEVATION);
    expect(await screen.findByTestId("retention-own-proposal")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Approve change" })).toBeNull();
    expect(screen.getByRole("button", { name: "Reject" })).toBeEnabled();
    expect(screen.getByTestId("retention-effective")).toHaveTextContent("365 days");
  });

  it("offers approval to a different operator, through the server", async () => {
    vi.mocked(api.get).mockResolvedValue({ data: RETENTION({
      retention_days: 400, warning_days: 21, reason: "Contract", requested_by: "u-other",
      requested_by_email: "other@zoiko.test", requested_at: "2026-09-24T10:00:00Z", previous: null }) });
    vi.mocked(api.post).mockResolvedValue({ data: RETENTION() });
    wrap(<RetentionPolicyPanel />, ACTIVE_ELEVATION);
    fireEvent.click(await screen.findByRole("button", { name: "Approve change" }));
    await waitFor(() => expect(api.post).toHaveBeenCalledWith("/admin/retention-policy/proposals/approve"));
  });

  it("blocks changes when elevation state is unknown", async () => {
    vi.mocked(api.get).mockResolvedValue({ data: RETENTION() });
    wrap(<RetentionPolicyPanel />);
    await screen.findByTestId("retention-effective");
    expect(screen.getByText(/elevation state could not be read/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Propose change" })).toBeDisabled();
  });
});

// ── Media ──────────────────────────────────────────────────────────────────────────────

describe("Media", () => {
  beforeEach(() => {
    vi.mocked(api.get).mockImplementation((url) => {
      if (url === "/admin/recordings/summary") {
        return Promise.resolve({ data: { total: 250, by_state: { ready: 120, unverified: 80, processing: 0, in_progress: 2, failed: 48 }, by_status: {} } });
      }
      return Promise.resolve({ data: [
        { id: "a", event_title: "Recital", organization: "Acme", status: "stopped", state: "ready", size_bytes: 2048, enforced: true, has_file_reference: true },
        { id: "b", event_title: "Gala", organization: "Acme", status: "stopped", state: "unverified", size_bytes: null, enforced: true, has_file_reference: true },
      ] });
    });
  });

  it("shows the server's verdict — a file reference alone is not 'ready'", async () => {
    wrap(<Media />);
    const gala = (await screen.findByText("Gala")).closest("tr");
    expect(within(gala).getByText("Unverified")).toBeInTheDocument();
    expect(within(screen.getByText("Recital").closest("tr")).getByText("Ready")).toBeInTheDocument();
    expect(screen.queryByText("Playable")).toBeNull();
    expect(screen.getByTestId("media-list-cap")).toHaveTextContent("newest 2 of 250");
  });

  it("searches on the server", async () => {
    wrap(<Media />);
    await screen.findByText("Gala");
    fireEvent.change(screen.getByLabelText("Search recordings"), { target: { value: "gala" } });
    await waitFor(() => expect(lastParams("/admin/recordings")).toMatchObject({ q: "gala" }));
    await waitFor(() => expect(lastParams("/admin/recordings/summary")).toMatchObject({ q: "gala" }));
  });
});

// ── Commerce: the historical catalog anomaly is flagged, not presented as normal ──────

describe("Commerce catalog", () => {
  it("flags a published version with no recorded publisher", async () => {
    vi.mocked(api.get).mockImplementation((url) => {
      if (url === "/commercial/catalog-versions") {
        return Promise.resolve({ data: [{
          id: "cv1", vertical: "events", version_label: "v1", status: "published", lines: [],
          published_by: null, published_at: null,
          integrity_issue: "Published without a recorded publisher or publish time — not produced by the governed publish path.",
        }] });
      }
      return Promise.resolve({ data: { mode: "not_configured" } });
    });
    wrap(<Commerce />);
    expect(await screen.findByTestId("catalog-integrity-issue")).toHaveTextContent(/not produced by the governed publish path/);
    expect(screen.getByText("Anomaly")).toBeInTheDocument();
  });
});
