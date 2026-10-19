// A trial ENDS. A paid subscription RENEWS. The card must not confuse the two.
//
// ── THE BUG ─────────────────────────────────────────────────────────────────────────────
// The summary asked `ent.status === "trial"` — the PRE-Section-12 spelling. Every
// subscription current code creates reports `trialing` (models/subscription.py keeps "trial"
// only as a legacy alias, `"trial": "trialing"`), so the test never matched and the card fell
// through to the renewal wording. It printed:
//
//     Renews on 7 Oct 2026
//
// over a date that was the TRIAL END. Nothing renews on that date — the trial lapses or
// converts — and telling somebody deciding whether to pay that their plan "renews" is a
// different promise from telling them their trial runs out.
//
// services/org.py hit the identical bug in the trial-ending warning, against the same dead
// literal, and fixed it by normalizing rather than matching a string. This is the same fix on
// the client side of the same contract.
//
// The second half was `trial_ends_at || current_period_end`: a trialing subscription with no
// recorded trial end silently borrowed the period end and presented it as a renewal — a date
// the customer never agreed to, under a word implying they had.
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../api", () => ({
  default: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
  errMsg: (e) => e?.message ?? "Something went wrong",
  transportMessage: () => null,
  diagnoseLoadError: () => "Couldn't load billing information.",
}));
vi.mock("../../ui/Toast", () => ({ notify: { success: vi.fn(), error: vi.fn() } }));

import api from "../../api";
import { ThemeProvider } from "../../theme/ThemeContext";
import OrganizationBilling from "./Billing";
import { isTrialing } from "./planPresentation";

const PLANS = [
  { id: "p-dev", slug: "developer", name: "Developer", pricing_state: "PUBLISHED",
    price_monthly: 49, features: [], billing_intervals: ["monthly"], self_service: true },
  { id: "p-biz", slug: "business", name: "Business", pricing_state: "PUBLISHED",
    price_monthly: 249, features: [], billing_intervals: ["monthly"], self_service: true },
];

const TRIAL_END = "2026-10-07T00:00:00Z";
const PERIOD_END = "2026-12-25T00:00:00Z";   // deliberately different, so a swap is visible

const BASE = {
  plan: "Developer", plan_slug: "developer", billing_interval: "monthly",
  checkout_locked: false, subscription_plan_slug: null, subscription_status: null, items: [],
};

async function mount(entitlements) {
  api.get.mockImplementation((url) => {
    if (url === "/organization/overview") {
      return Promise.resolve({
        data: { organization: { name: "Northwind" },
                entitlements: { ...BASE, ...entitlements } },
      });
    }
    if (url === "/organization/plans") return Promise.resolve({ data: PLANS });
    return Promise.reject(new Error(`unexpected GET ${url}`));
  });
  api.post.mockResolvedValue({ data: {} });
  render(
    <MemoryRouter>
      <ThemeProvider>
        <OrganizationBilling />
      </ThemeProvider>
    </MemoryRouter>
  );
  await screen.findByText("Available Plans");
}

beforeEach(() => {
  vi.clearAllMocks();
  window.history.replaceState({}, "", "/organization/billing");
});

describe("a trialing subscription", () => {
  const TRIALING = { status: "trialing", trial_ends_at: TRIAL_END, current_period_end: PERIOD_END };

  it("says the trial ends, not that the plan renews", async () => {
    await mount(TRIALING);
    expect(screen.getByText(/Trial ends on/)).toBeInTheDocument();
    expect(screen.queryByText(/Renews on/)).not.toBeInTheDocument();
  });

  it("names the trial end date, not the period end", async () => {
    await mount(TRIALING);
    const line = screen.getByText(/Trial ends on/).textContent;
    expect(line).toMatch(/2026/);
    // The period end is a different month; borrowing it would be the original defect.
    expect(line).not.toMatch(/Dec/i);
  });

  it("is recognised in the legacy `trial` spelling too", async () => {
    // Older rows are stored that way, and they are real trials.
    await mount({ ...TRIALING, status: "trial" });
    expect(screen.getByText(/Trial ends on/)).toBeInTheDocument();
  });
});

describe("a trialing subscription with no recorded trial end", () => {
  it("does NOT present the period end as a renewal", async () => {
    await mount({ status: "trialing", trial_ends_at: null, current_period_end: PERIOD_END });
    expect(screen.queryByText(/Renews on/)).not.toBeInTheDocument();
  });

  it("says it is a trial without inventing a date", async () => {
    await mount({ status: "trialing", trial_ends_at: null, current_period_end: PERIOD_END });
    expect(screen.getByText("In trial")).toBeInTheDocument();
  });
});

describe("an active paid subscription", () => {
  it("still says the plan renews", async () => {
    await mount({ status: "active", trial_ends_at: null, current_period_end: PERIOD_END });
    expect(screen.getByText(/Renews on/)).toBeInTheDocument();
    expect(screen.queryByText(/Trial ends on/)).not.toBeInTheDocument();
  });

  it("reads the period end even if a stale trial date is still on the row", async () => {
    // A converted trial keeps trial_ends_at; the renewal date is the period end.
    await mount({ status: "active", trial_ends_at: TRIAL_END, current_period_end: PERIOD_END });
    const line = screen.getByText(/Renews on/).textContent;
    expect(line).toMatch(/Dec/i);
  });

  it("says nothing at all when no renewal date is recorded", async () => {
    // A backend gap should surface as a gap, not as invented copy.
    await mount({ status: "active", trial_ends_at: null, current_period_end: null });
    expect(screen.queryByText(/Renews on/)).not.toBeInTheDocument();
    expect(screen.queryByText("In trial")).not.toBeInTheDocument();
  });
});

describe("other states keep the existing wording", () => {
  for (const status of ["past_due", "plan_change_scheduled", "canceled"]) {
    it(`${status} still reads as a renewal date`, async () => {
      await mount({ status, trial_ends_at: null, current_period_end: PERIOD_END });
      expect(screen.getByText(/Renews on/)).toBeInTheDocument();
    });
  }
});

describe("the predicate itself", () => {
  it("matches both stored spellings", () => {
    expect(isTrialing("trialing")).toBe(true);
    expect(isTrialing("trial")).toBe(true);
  });

  it("is not fooled by case or stray whitespace", () => {
    expect(isTrialing(" Trialing ")).toBe(true);
  });

  it("does not match a paid or absent state", () => {
    for (const s of ["active", "past_due", "canceled", "trial_expired", "", null, undefined]) {
      expect(isTrialing(s)).toBe(false);
    }
  });

  it("does not match a state that merely contains the word", () => {
    // trial_expired is NOT in a trial — the trial is over.
    expect(isTrialing("trial_expired")).toBe(false);
    expect(isTrialing("trial_eligible")).toBe(false);
  });
});

describe("the rest of the card is unchanged", () => {
  it("still shows the plan and its cadence", async () => {
    await mount({ status: "trialing", trial_ends_at: TRIAL_END, current_period_end: PERIOD_END });
    expect(screen.getByText(/Billed monthly/i)).toBeInTheDocument();
  });
});
