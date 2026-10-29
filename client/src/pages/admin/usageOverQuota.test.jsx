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

const quota = (over = {}) => ({
  key: "storage", label: "Storage", used: 10, limit: null, effective_limit: 50,
  unit: "GB", percent: 20, remaining: 40, unlimited: false,
  quota_state: "within", usage_state: "measured", enforced: true,
  enforced_limit: 50, quota_source: "platform_default",
  ...over,
});

const org = (quotas, over = {}) => ({
  org_id: "o1", organization: "Acme", is_test: false,
  plan: null, plan_slug: null, subscription_status: null,
  entitlement_source: "none", current_period_end: null, trial_ends_at: null,
  quotas, metrics: [],
  ...over,
});

const show = (rows) => {
  vi.mocked(api.get).mockResolvedValue({
    data: { items: rows, total: rows.length, page: 1, page_size: 25 },
  });
  return render(
    <ThemeProvider>
      <MemoryRouter>
        <UsageEntitlements />
      </MemoryRouter>
    </ThemeProvider>
  );
};

const rowFor = async (name) => (await screen.findByText(name)).closest("li");

beforeEach(() => vi.clearAllMocks());

describe("effective quota display", () => {
  it("uses the entitled organization's plan storage quota", async () => {
    show([org([quota({
      used: 10, limit: 50, effective_limit: 50, percent: 20, remaining: 40,
      quota_source: "plan",
    })], { plan: "Business", entitlement_source: "subscription" })]);
    const row = await rowFor("Acme");
    const tile = within(row).getByTestId("quota-storage");
    expect(tile.textContent).toContain("10 / 50 GB");
    expect(tile.textContent).toContain("40 GB remaining");
    expect(tile.textContent).not.toContain("null");
  });

  it("shows a zero storage quota when there is no entitled plan", async () => {
    show([org([quota({
      used: 2, limit: null, effective_limit: 0, percent: null, remaining: -2,
      quota_state: "exceeded", enforced_limit: 0, quota_source: "no_entitled_plan",
    })])]);

    const tile = within(await rowFor("Acme")).getByTestId("quota-storage");
    expect(tile).toHaveTextContent("2 / 0 GB");
    expect(tile).toHaveTextContent("Over by 2 GB");
    expect(tile).toHaveTextContent("No entitled plan — new recordings are blocked until an entitled plan is in effect");
    expect(tile).not.toHaveTextContent("Enforced ceiling 5000 GB");
    expect(tile).not.toHaveTextContent("null%");
  });

  it("reports quota exhaustion without implying the organization is unlimited", async () => {
    show([org([quota({
      used: 60, percent: 120, remaining: -10, effective_limit: 50,
      quota_state: "exceeded", enforced_limit: 50,
    })])]);
    const tile = within(await rowFor("Acme")).getByTestId("quota-storage");
    expect(tile.textContent).toContain("60 / 50 GB");
    expect(tile.textContent).toContain("Over by 10 GB");
    expect(tile.textContent).toContain("Over quota");
  });

  it("distinguishes an unconfigured quota from zero or unlimited", async () => {
    show([org([quota({
      used: 0, limit: null, effective_limit: null, percent: null, remaining: null,
      quota_state: "not_applicable", enforced_limit: null,
      quota_source: "unconfigured",
    })])]);
    const tile = within(await rowFor("Acme")).getByTestId("quota-storage");
    expect(tile.textContent).toContain("No quota configured");
    expect(tile.textContent).toContain("new activity is blocked");
    expect(tile.textContent).not.toContain("unlimited");
    expect(tile.textContent).not.toContain("null");
  });

  it("keeps an active subscription active while showing over-quota enforcement", async () => {
    show([org([quota({
      key: "streaming_hours", label: "Streaming hours", used: 312.8, limit: 200,
      effective_limit: 200, unit: "hrs", percent: 156.4, remaining: -112.8,
      quota_state: "exceeded", enforced_limit: 200,
    })], { plan: "Business", subscription_status: "active" })]);

    const row = await rowFor("Acme");
    const tile = within(row).getByTestId("quota-streaming_hours");
    expect(within(row).getByText("active")).toBeInTheDocument();
    expect(tile).toHaveTextContent("312.8 / 200 hrs");
    expect(tile).toHaveTextContent("Over by 112.8 hrs");
    expect(tile).toHaveTextContent("Over quota");
    expect(tile).toHaveTextContent("Lifetime quota: new broadcasts are blocked at the limit; active broadcasts may continue");
  });

  it("labels an exactly exhausted enforced quota as reached, not over quota", async () => {
    show([org([quota({
      key: "streaming_hours", label: "Streaming hours", used: 200, limit: 200,
      effective_limit: 200, unit: "hrs", percent: 100, remaining: 0,
      quota_state: "at_limit", enforced_limit: 200,
    })])]);

    const tile = within(await rowFor("Acme")).getByTestId("quota-streaming_hours");
    expect(tile).toHaveTextContent("Limit reached");
    expect(tile).not.toHaveTextContent("Over quota");
    expect(tile).toHaveTextContent("Lifetime quota: new broadcasts are blocked at the limit");
  });

  it("identifies a plan comparison as informational when the backend says it is not enforced", async () => {
    show([org([quota({
      key: "streaming_hours", label: "Streaming hours", used: 12, limit: 10,
      effective_limit: 10, unit: "hrs", percent: 120, remaining: -2,
      quota_state: "exceeded", enforced: false, enforced_limit: null,
    })], { plan: "Business", entitlement_source: "subscription" })]);

    const tile = within(await rowFor("Acme")).getByTestId("quota-streaming_hours");
    expect(tile).toHaveTextContent("Not enforced — shown against the plan only");
    expect(tile).not.toHaveTextContent("No entitled plan — Standard usage limits apply");
    expect(tile).not.toHaveTextContent("New broadcasts are blocked");
  });

  it("uses the standard-limits message for streaming hours without an entitled plan", async () => {
    show([org([quota({
      key: "streaming_hours", label: "Streaming hours", used: 0, limit: null,
      effective_limit: null, unit: "hrs", percent: null, remaining: null,
      quota_state: "not_applicable", quota_source: "unconfigured",
    })], { entitlement_source: "none" })]);

    const tile = within(await rowFor("Acme")).getByTestId("quota-streaming_hours");
    expect(tile).toHaveTextContent("No entitled plan — Standard usage limits apply");
    expect(tile).not.toHaveTextContent("Not enforced — shown against the plan only");
  });
});
