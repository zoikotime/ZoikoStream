// A live subscription on a plan this page cannot rank.
//
// ── THE BUG ─────────────────────────────────────────────────────────────────────────────
// Two spellings of one condition had drifted apart:
//
//   planCta  stops offering a purchase when PLAN_RANK[currentSlug] is undefined — true both
//            when there is no entitled slug AND when the slug is real but outside the five
//            tiers this page knows.
//   Billing  showed its explanatory banner only when `plan_slug` was FALSY.
//
// So an organization entitled to any other slug fell straight into the gap: every card read
// "Talk to an expert" and nothing on the page said why — indistinguishable from all three
// tiers genuinely being sales-led, which is the reported symptom. Both now ask
// planPresentation's own `isRankedPlan`, so the explanation cannot vanish while the lock
// stays on.
//
// The lock itself is deliberate and is NOT relaxed here: checkout answers 409 for a tenant
// that already has a live Stripe subscription, so offering Upgrade would be a button whose
// only outcome is a refusal, or a second charge.
import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../api", () => ({
  default: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
  errMsg: (e) => e?.message ?? "Something went wrong.",
  transportMessage: () => null,
  diagnoseLoadError: () => "Couldn't load billing information.",
}));
vi.mock("../../ui/Toast", () => ({ notify: { success: vi.fn(), error: vi.fn() } }));

import api from "../../api";
import { ThemeProvider } from "../../theme/ThemeContext";
import OrganizationBilling from "./Billing";
import { isRankedPlan, planCta } from "./planPresentation";

const PLANS = [
  { id: "p-dev", slug: "developer", name: "Developer", pricing_state: "PUBLISHED",
    price_monthly: 49, features: [], billing_intervals: ["monthly"], self_service: true },
  { id: "p-biz", slug: "business", name: "Business", pricing_state: "PUBLISHED",
    price_monthly: 249, features: [], billing_intervals: ["monthly"], self_service: true },
  { id: "p-ent", slug: "enterprise", name: "Enterprise", pricing_state: "CUSTOM",
    price_monthly: null, features: [], billing_intervals: [], self_service: false },
];

const overviewFor = (ent) => ({
  organization: { name: "Northwind" },
  entitlements: {
    plan: null, plan_slug: null, status: null, checkout_locked: false,
    subscription_plan_slug: null, subscription_status: null, items: [], ...ent,
  },
});

function mount(ent) {
  api.get.mockImplementation((url) => {
    if (url === "/organization/overview") return Promise.resolve({ data: overviewFor(ent) });
    if (url === "/organization/plans") return Promise.resolve({ data: PLANS });
    return Promise.reject(new Error(`unexpected GET ${url}`));
  });
  api.post.mockResolvedValue({ data: { synced: false, outcome: "current" } });
  render(
    <MemoryRouter>
      <ThemeProvider>
        <OrganizationBilling />
      </ThemeProvider>
    </MemoryRouter>
  );
}

const planCard = (name) => screen.getByText(name, { selector: "p" }).closest("div.flex.flex-col");
const ctaText = (name) => {
  const card = planCard(name);
  const el = within(card).queryByRole("button") || within(card).queryByRole("link");
  return el?.textContent?.trim();
};

beforeEach(() => {
  vi.clearAllMocks();
  window.history.replaceState({}, "", "/organization/billing");
});

// A real, entitled subscription on a tier outside Developer/Business/Enterprise.
const LEGACY = {
  plan: "Legacy Broadcast", plan_slug: "legacy_broadcast", status: "active",
  checkout_locked: true, subscription_plan_slug: "legacy_broadcast",
  subscription_status: "active",
};

describe("the predicate itself", () => {
  it("ranks the five tiers this page knows", () => {
    for (const slug of ["developer", "business", "enterprise", "starter", "pro"]) {
      expect(isRankedPlan(slug)).toBe(true);
    }
  });

  it("does not rank an absent or unknown slug", () => {
    expect(isRankedPlan(null)).toBe(false);
    expect(isRankedPlan(undefined)).toBe(false);
    expect(isRankedPlan("")).toBe(false);
    expect(isRankedPlan("legacy_broadcast")).toBe(false);
  });

  it("agrees with the CTA that an unranked plan cannot purchase", () => {
    // The drift this closes: these two must answer the same question the same way.
    const developer = PLANS[0];
    expect(planCta(developer, "legacy_broadcast", { checkoutLocked: true }).kind).toBe("contact");
    expect(isRankedPlan("legacy_broadcast")).toBe(false);
  });
});

describe("an entitled plan outside the hierarchy", () => {
  it("explains itself instead of silently showing sales CTAs", async () => {
    mount(LEGACY);
    await screen.findByText("Available Plans");
    // The regression: no banner at all, so three "Talk to an expert" cards and no reason given.
    expect(screen.getByRole("status")).toBeInTheDocument();
  });

  it("names the plan the organization is actually on", async () => {
    mount(LEGACY);
    await screen.findByText("Available Plans");
    expect(screen.getByRole("status").textContent).toMatch(/Legacy Broadcast/);
  });

  it("does not claim to be synchronizing something that is not pending", async () => {
    mount(LEGACY);
    await screen.findByText("Available Plans");
    expect(screen.getByRole("status").textContent).not.toMatch(/synchroniz/i);
  });

  it("still says nothing further will be charged", async () => {
    mount(LEGACY);
    await screen.findByText("Available Plans");
    expect(screen.getByRole("status").textContent).toMatch(/nothing further will be charged/i);
  });

  it("keeps the purchase lock on every card", async () => {
    mount(LEGACY);
    await screen.findByText("Available Plans");
    expect(ctaText("Developer")).toBe("Talk to an expert");
    expect(ctaText("Business")).toBe("Talk to an expert");
    expect(screen.queryByRole("button", { name: /^Upgrade$/ })).not.toBeInTheDocument();
  });
});

describe("a ranked plan is unaffected", () => {
  const ON_DEVELOPER = {
    plan: "Developer", plan_slug: "developer", status: "active",
    checkout_locked: true, subscription_plan_slug: "developer", subscription_status: "active",
  };

  it("shows no banner, because nothing is unidentified", async () => {
    mount(ON_DEVELOPER);
    await screen.findByText("Available Plans");
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("marks the current plan and offers the higher tier", async () => {
    mount(ON_DEVELOPER);
    await screen.findByText("Available Plans");
    expect(ctaText("Developer")).toBe("Current Plan");
    expect(ctaText("Business")).toBe("Upgrade");
  });

  it("keeps Enterprise sales-led, because it has no approved price", async () => {
    mount(ON_DEVELOPER);
    await screen.findByText("Available Plans");
    expect(ctaText("Enterprise")).toBe("Talk to an expert");
  });
});
