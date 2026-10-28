import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";

vi.mock("../../api", () => ({
  default: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
  errMsg: (e) => e?.message ?? "failed",
  diagnoseLoadError: (e) => e?.message ?? "failed to load",
  AUTH_EXPIRED_EVENT: "zoiko:auth-expired",
}));

import api from "../../api";
import { ThemeProvider } from "../../theme/ThemeContext";
import Subscriptions from "./Subscriptions";

const SUBSCRIPTIONS = [
  {
    id: "sub00001-0000-0000-0000-000000000001",
    org_id: "org00001-0000-0000-0000-000000000001",
    organization_name: "zoiko",
    plan: "Developer",
    price_monthly: 49,
    status: "active",
    seats: 1,
    started_at: "2026-09-29T00:00:00Z",
    current_period_end: "2026-10-29T00:00:00Z",
    is_current: true,
  },
  {
    id: "sub00002-0000-0000-0000-000000000002",
    org_id: "org00001-0000-0000-0000-000000000001",
    organization_name: "zoiko",
    plan: "Developer",
    price_monthly: 49,
    status: "trialing",
    seats: 1,
    started_at: "2026-09-23T00:00:00Z",
    current_period_end: null,
    is_current: false,
  },
  {
    id: "sub00003-0000-0000-0000-000000000003",
    org_id: "org00002-0000-0000-0000-000000000002",
    organization_name: "zoiko",
    plan: "Developer",
    price_monthly: 49,
    status: "trialing",
    seats: 1,
    started_at: "2026-09-24T00:00:00Z",
    current_period_end: null,
    is_current: true,
  },
];

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.get).mockImplementation((url) => Promise.resolve({
    data: url === "/admin/subscriptions"
      ? { items: SUBSCRIPTIONS, total: SUBSCRIPTIONS.length }
      : url === "/admin/subscriptions/summary"
        ? { total: SUBSCRIPTIONS.length, active: 1, trial: 2, past_due: 0, contracted_mrr: 49 }
        : [],
  }));
});

describe("subscription identity and history", () => {
  it("distinguishes same-name organizations and marks the selected record without hiding history", async () => {
    render(
      <ThemeProvider>
        <MemoryRouter initialEntries={["/admin/usage-entitlements?tab=subscriptions"]}>
          <Subscriptions />
        </MemoryRouter>
      </ThemeProvider>
    );

    await waitFor(() => expect(document.querySelectorAll("table tbody tr")).toHaveLength(3));
    const body = document.querySelector("table tbody");
    const rows = [...body.querySelectorAll("tr")].map((row) => row.textContent);

    expect(rows.filter((text) => text.includes("zoiko"))).toHaveLength(3);
    expect(rows.some((text) => text.includes("Org org00001 · Sub sub00001") && text.includes("Current record"))).toBe(true);
    expect(rows.some((text) => text.includes("Org org00001 · Sub sub00002") && text.includes("Historical record"))).toBe(true);
    expect(rows.some((text) => text.includes("Org org00002 · Sub sub00003") && text.includes("Current record"))).toBe(true);
    expect(screen.getAllByText("Current record")).toHaveLength(2);
    expect(screen.getByText("Historical record")).toBeInTheDocument();
  });

  it("keeps local pagination working with the added references", async () => {
    const items = Array.from({ length: 11 }, (_, index) => {
      const suffix = String(index).padStart(5, "0");
      return {
        ...SUBSCRIPTIONS[0],
        id: `sub${suffix}-0000-0000-0000-000000000000`,
        org_id: `org${suffix}-0000-0000-0000-000000000000`,
        organization_name: `organization-${suffix}`,
        started_at: `2026-10-${String(28 - index).padStart(2, "0")}T00:00:00Z`,
      };
    });
    vi.mocked(api.get).mockImplementation((url) => Promise.resolve({
      data: url === "/admin/subscriptions"
        ? { items, total: items.length }
        : url === "/admin/subscriptions/summary"
          ? { total: items.length, active: items.length, trial: 0, past_due: 0, contracted_mrr: 539 }
          : [],
    }));

    render(
      <ThemeProvider>
        <MemoryRouter initialEntries={["/admin/usage-entitlements?tab=subscriptions"]}>
          <Subscriptions />
        </MemoryRouter>
      </ThemeProvider>
    );

    await waitFor(() => expect(document.querySelectorAll("table tbody tr")).toHaveLength(10));
    fireEvent.click(screen.getByRole("button", { name: /next page/i }));
    await waitFor(() => expect(document.querySelectorAll("table tbody tr")).toHaveLength(1));
    expect(document.querySelector("table tbody").textContent).toContain("Org org00010 · Sub sub00010");
    expect(screen.getByText(/Showing 11–11 of 11/)).toBeInTheDocument();
  });
});
