// Being over an allowance and being an active subscription are two different facts.
//
// The console reported the breach per metric — a red "Over quota" badge and "Over by 112.8
// hrs" on the streaming-hours tile — but the organization header showed only "Business" and
// "active". An operator scanning the list saw a healthy row and had to open the tiles to find
// the overage. The warning now sits at the account level too, derived from the quotas the
// server already sends; there is no second quota system and no state of its own.
//
// What must NOT happen: the subscription badge moving because of usage. "active" is a
// statement about billing and the subscription lifecycle; exceeding an allowance is a
// separate fact, and the row has to say both at once.
//
// Streaming hours is monitoring-only. `max_streaming_hours` appears in the Plan column, the
// admin schema, and services/org.py's report (enforced=False) — and nowhere else. Nothing
// reads it when an event is created, when a broadcast goes live, or while one runs. The
// notice says so, rather than letting a red badge imply a control that does not exist.
import { render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";

vi.mock("../../api", () => ({
  default: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
  errMsg: (e) => e?.message ?? "failed",
  diagnoseLoadError: (e) => e?.message ?? "failed to load",
  AUTH_EXPIRED_EVENT: "zoiko:auth-expired",
}));
vi.mock("../../auth/AuthContext", () => ({
  useAuth: () => ({ user: { id: "u-me", full_name: "Ada Ops", role: "super_admin" }, logout: vi.fn() }),
}));

import api from "../../api";
import { ThemeProvider } from "../../theme/ThemeContext";
import UsageEntitlements from "./UsageEntitlements";

// Exactly the reported row: Business, active, 312.8 hrs against a 200-hour allowance.
const streaming = (over = {}) => ({
  key: "streaming_hours", label: "Streaming hours", used: 312.8, limit: 200, unit: "hrs",
  percent: 156.4, remaining: -112.8, unlimited: false,
  quota_state: "exceeded", usage_state: "measured", enforced: false, enforced_limit: null,
  ...over,
});

const storage = (over = {}) => ({
  key: "storage", label: "Storage", used: 10, limit: 100, unit: "GB",
  percent: 10, remaining: 90, unlimited: false,
  quota_state: "within", usage_state: "measured", enforced: true, enforced_limit: 100,
  ...over,
});

const org = (over = {}) => ({
  org_id: "o1", organization: "Acme", is_test: false,
  plan: "Business", plan_slug: "business", subscription_status: "active",
  entitlement_source: "subscription", current_period_end: null, trial_ends_at: null,
  quotas: [storage(), streaming()],
  metrics: [],
  ...over,
});

const serve = (rows) =>
  vi.mocked(api.get).mockResolvedValue({ data: { items: rows, total: rows.length, page: 1, page_size: 25 } });

const show = () =>
  render(
    <ThemeProvider>
      <MemoryRouter>
        <UsageEntitlements plans={[{ id: "p1", slug: "business", name: "Business" }]} />
      </MemoryRouter>
    </ThemeProvider>
  );

// Prime the endpoint AND mount the page. Kept as one call so a test cannot do the first and
// forget the second.
const open = (rows) => { serve(rows); return show(); };

const rowFor = async (name) => (await screen.findByText(name)).closest("li");
const notice = (li) => within(li).queryByTestId("over-quota-notice");

beforeEach(() => vi.clearAllMocks());

describe("the reported row: active Business, 312.8 of 200 hrs", () => {
  beforeEach(() => open([org()]));

  it("still reports the subscription as active", async () => {
    const li = await rowFor("Acme");
    expect(within(li).getByText("active")).toBeInTheDocument();
  });

  it("also carries an account-level Over quota badge", async () => {
    const li = await rowFor("Acme");
    // Beside the subscription badge, not instead of it.
    expect(within(li).getAllByText("Over quota").length).toBeGreaterThan(0);
  });

  it("names the metric, the usage and the allowance at account level", async () => {
    const li = await rowFor("Acme");
    const text = notice(li).textContent;
    expect(text).toContain("Streaming hours");
    expect(text).toContain("312.8 / 200 hrs");
  });

  it("states the excess and the percentage above the limit", async () => {
    const li = await rowFor("Acme");
    expect(notice(li).textContent).toContain("over by 112.8 hrs (56.4% above the limit)");
  });

  it("says the limit is not currently enforced", async () => {
    const li = await rowFor("Acme");
    expect(notice(li).textContent).toContain("monitoring only — not currently restricted");
  });

  it("is announced rather than colour-only", async () => {
    const li = await rowFor("Acme");
    expect(notice(li)).toHaveAttribute("role", "status");
  });

  it("keeps the per-metric reading intact", async () => {
    const li = await rowFor("Acme");
    const tile = within(li).getByTestId("quota-streaming_hours");
    expect(tile.textContent).toContain("312.8 / 200 hrs");
    expect(tile.textContent).toContain("Over by 112.8 hrs");
    expect(tile.textContent).toContain("156.4%");          // share OF the limit, unchanged
  });
});

describe("the warning clears", () => {
  it("is absent when every metric is within its allowance", async () => {
    open([org({ quotas: [storage(), streaming({ used: 10, percent: 5, remaining: 190, quota_state: "within" })] })]);
    const li = await rowFor("Acme");
    expect(notice(li)).toBeNull();
    expect(within(li).queryByText("Over quota")).toBeNull();
    expect(within(li).getByText("active")).toBeInTheDocument();
  });

  it("is absent for an organization with no entitled plan", async () => {
    // not_applicable is not a breach: there is no allowance to exceed.
    open([org({
      plan: null, subscription_status: null,
      quotas: [streaming({ quota_state: "not_applicable", limit: null, used: 0, percent: null, remaining: null })],
    })]);
    const li = await rowFor("Acme");
    expect(notice(li)).toBeNull();
  });

  it("is absent for an unlimited plan", async () => {
    open([org({ quotas: [streaming({ quota_state: "unlimited", limit: null, unlimited: true, percent: null, remaining: null })] })]);
    const li = await rowFor("Acme");
    expect(notice(li)).toBeNull();
  });
});

describe("an enforced metric reads differently", () => {
  it("says enforced when the platform actually refuses at that limit", async () => {
    open([org({ quotas: [storage({ used: 150, limit: 100, percent: 150, remaining: -50, quota_state: "exceeded" })] })]);
    const li = await rowFor("Acme");
    const text = notice(li).textContent;
    expect(text).toContain("Storage: 150 / 100 GB");
    expect(text).toContain("over by 50 GB (50% above the limit)");
    expect(text).toContain("enforced");
    expect(text).not.toContain("not currently restricted");
  });

  it("lists every breached metric, not just the first", async () => {
    open([org({
      quotas: [storage({ used: 150, limit: 100, percent: 150, remaining: -50, quota_state: "exceeded" }), streaming()],
    })]);
    const text = notice(await rowFor("Acme")).textContent;
    expect(text).toContain("Storage");
    expect(text).toContain("Streaming hours");
  });
});

describe("nothing is fabricated", () => {
  it("shows no figure the server did not send", async () => {
    open([org({ quotas: [streaming({ used: 0, limit: null, percent: null, remaining: null, quota_state: "not_applicable" })] })]);
    const li = await rowFor("Acme");
    for (const bad of ["NaN", "undefined", "Infinity"]) {
      expect(li.textContent).not.toContain(bad);
    }
  });

  it("does not divide by a zero allowance", async () => {
    open([org({ quotas: [streaming({ used: 5, limit: 0, percent: null, remaining: -5, quota_state: "exceeded" })] })]);
    const li = await rowFor("Acme");
    expect(li.textContent).not.toContain("Infinity");
    expect(li.textContent).not.toContain("NaN");
  });
});

describe("tenant isolation", () => {
  it("raises the warning only on the organization that is over", async () => {
    open([
      org({ org_id: "o1", organization: "Acme" }),
      org({ org_id: "o2", organization: "Globex", quotas: [storage()] }),
    ]);
    const acme = await rowFor("Acme");
    const globex = await rowFor("Globex");
    expect(notice(acme)).not.toBeNull();
    expect(notice(globex)).toBeNull();
    expect(within(globex).getByText("active")).toBeInTheDocument();
  });
});
