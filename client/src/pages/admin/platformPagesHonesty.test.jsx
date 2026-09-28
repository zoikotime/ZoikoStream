// The PLATFORM pages, pinned against the specific fabrications the audit found.
//
// Each assertion here is a negative one against a string or number the page USED to show,
// because "we removed a fake value" is only true until the next refactor reintroduces it.
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";

vi.mock("../../api", () => ({
  default: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
  errMsg: (e) => e?.message ?? "failed",
  diagnoseLoadError: (e) => e?.message ?? "failed",
  // Imported transitively by the topbar (auth context listens for it).
  AUTH_EXPIRED_EVENT: "zoiko:auth-expired",
}));

vi.mock("../../auth/AuthContext", () => ({
  useAuth: () => ({ user: { full_name: "Ada Ops", role: "super_admin" }, logout: vi.fn() }),
}));

import api from "../../api";
import { ThemeProvider } from "../../theme/ThemeContext";
import Commerce from "./Commerce";
import Subscriptions from "./Subscriptions";
import AdminTopbar from "../../components/admin/AdminTopbar";

// Usage & Entitlements opens on the usage view; the subscription list is its second tab.
const openSubscriptionsTab = async () => fireEvent.click(await screen.findByRole("tab", { name: "Subscriptions" }));

const wrap = (node) =>
  render(
    <ThemeProvider>
      <MemoryRouter>{node}</MemoryRouter>
    </ThemeProvider>
  );

beforeEach(() => vi.clearAllMocks());

// ── Commerce: the payment-provider notice is detected, not assumed ─────────────────────

describe("the Stripe notice reflects the real configuration", () => {
  const serve = (provider) =>
    vi.mocked(api.get).mockImplementation((url) =>
      url === "/admin/payment-provider"
        ? provider instanceof Error ? Promise.reject(provider) : Promise.resolve({ data: provider })
        : Promise.resolve({ data: [] })
    );

  it("says LIVE when the key is live — it used to say TEST regardless", async () => {
    serve({ provider: "stripe", configured: true, mode: "live" });
    wrap(<Commerce />);
    expect(await screen.findByText(/LIVE key/i)).toBeInTheDocument();
    expect(screen.queryByText(/TEST mode/i)).not.toBeInTheDocument();
  });

  it("says not configured when there is no key", async () => {
    serve({ provider: "stripe", configured: false, mode: "not_configured" });
    wrap(<Commerce />);
    expect(await screen.findByText(/Stripe is not configured/i)).toBeInTheDocument();
  });

  it("says TEST only when the key really is a test key", async () => {
    serve({ provider: "stripe", configured: true, mode: "test" });
    wrap(<Commerce />);
    expect(await screen.findByText(/TEST-mode key/i)).toBeInTheDocument();
  });

  it("says unavailable, not TEST, when the status cannot be read", async () => {
    serve(new Error("network"));
    wrap(<Commerce />);
    expect(await screen.findByText(/status unavailable/i)).toBeInTheDocument();
    expect(screen.queryByText(/TEST/)).not.toBeInTheDocument();
  });
});

// ── the header verdict covers only what was checked ────────────────────────────────────

describe("the console header does not overclaim", () => {
  const topbar = (health) =>
    wrap(<AdminTopbar onMenuClick={vi.fn()} state={{ health }} unknown={false} onRetry={vi.fn()} />);

  it("does not say 'All systems operational' while services are unmonitored", () => {
    topbar({ overall: "ok", degraded: 0, total: 9, unmonitored: 3 });
    expect(screen.queryByText("All systems operational")).not.toBeInTheDocument();
    expect(screen.getByText(/Operational · 3 not monitored/)).toBeInTheDocument();
  });

  it("says 'All systems operational' only when everything was probed", () => {
    topbar({ overall: "ok", degraded: 0, total: 5, unmonitored: 0 });
    expect(screen.getByText("All systems operational")).toBeInTheDocument();
  });

  it("still reports degradation plainly", () => {
    topbar({ overall: "warn", degraded: 1, total: 9, unmonitored: 3 });
    expect(screen.getByText(/Degraded/i)).toBeInTheDocument();
  });
});

// ── Usage & Entitlements: counts are dataset-wide ──────────────────────────────────────

describe("subscription KPIs come from the server, not the fetched page", () => {
  it("shows the server's total even when only a page of rows is loaded", async () => {
    // 3 rows fetched, 250 in the dataset. The page used to show "3".
    vi.mocked(api.get).mockImplementation((url) => {
      if (url === "/admin/subscriptions") {
        return Promise.resolve({ data: { items: [
          { id: "s1", organization_name: "A", plan: "Pro", status: "active", price_monthly: 10 },
          { id: "s2", organization_name: "B", plan: "Pro", status: "trial", price_monthly: 10 },
          { id: "s3", organization_name: "C", plan: "Pro", status: "active", price_monthly: 10 },
        ], total: 250 } });
      }
      if (url === "/admin/subscriptions/summary") {
        return Promise.resolve({ data: { total: 250, active: 180, trial: 40, past_due: 5,
                                         contracted_mrr: 1800, mrr_basis: "contracted_list_price" } });
      }
      return Promise.resolve({ data: [] });
    });
    wrap(<Subscriptions />);
    await openSubscriptionsTab();

    // The rendered figure is not asserted: StatCard animates through ui/Counter, which reads
    // 0 under jsdom because IntersectionObserver is stubbed inert (src/test/setup.js). What
    // is pinned is the thing that was actually wrong — WHERE the count comes from. It must
    // be the dataset-wide summary, not the length of the three rows on the page.
    expect(await screen.findByText("Contracted MRR")).toBeInTheDocument();
    expect(api.get).toHaveBeenCalledWith("/admin/subscriptions/summary");
  });

  it("sends the status filter to the server rather than filtering in the browser", async () => {
    vi.mocked(api.get).mockResolvedValue({ data: { items: [], total: 0 } });
    wrap(<Subscriptions />);
    await openSubscriptionsTab();
    await waitFor(() => expect(api.get).toHaveBeenCalledWith("/admin/subscriptions", expect.anything()));
    const call = vi.mocked(api.get).mock.calls.find(([u]) => u === "/admin/subscriptions");
    expect(call[1].params).toHaveProperty("status");
  });

  it("does not describe itself as billing — payment is outside the platform boundary", async () => {
    vi.mocked(api.get).mockResolvedValue({ data: { items: [], total: 0 } });
    wrap(<Subscriptions />);
    await screen.findByText(/the plan subscriptions behind them/i);
    expect(screen.queryByText(/Billing status across/i)).not.toBeInTheDocument();
    // The old copy said no usage view existed; there is one now, and it is the default tab.
    expect(screen.queryByText(/Usage metering\s+and quota views are not available/i)).not.toBeInTheDocument();
  });
});
