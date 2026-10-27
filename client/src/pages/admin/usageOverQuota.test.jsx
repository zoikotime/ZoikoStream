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

const org = (quotas) => ({
  org_id: "o1", organization: "Acme", is_test: false,
  plan: null, plan_slug: null, subscription_status: null,
  entitlement_source: "none", current_period_end: null, trial_ends_at: null,
  quotas, metrics: [],
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
  it("uses a configured platform default when no subscription plan exists", async () => {
    show([org([quota()])]);
    const row = await rowFor("Acme");
    const tile = within(row).getByTestId("quota-storage");
    expect(tile.textContent).toContain("10 / 50 GB");
    expect(tile.textContent).toContain("Platform default");
    expect(tile.textContent).not.toContain("null");
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
});
