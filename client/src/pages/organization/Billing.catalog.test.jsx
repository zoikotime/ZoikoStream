// Which plans the Billing page OFFERS.
//
// ZST-COM-PLAN-001 Section 03 names three tiers: Developer, Business, Enterprise. `starter`
// and `pro` are the pre-Section-03 spellings of the first two — migrate_plan_names.py renames
// starter -> developer and pro -> business — so a deployment that has not run that migration
// still serves five plans from GET /organization/plans and the page used to render all five.
//
// What these pin:
//   1. exactly three cards, and which three
//   2. the retired tiers are ABSENT FROM THE DOM, not hidden — a hidden card is still in the
//      grid's flow and still readable by anything that reads the page
//   3. removing them leaves no empty card and no gap in the row
//   4. Developer/Business/Enterprise behave exactly as before
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../api", () => ({
  default: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
  errMsg: (e) => e?.message ?? "Something went wrong.",
}));

vi.mock("../../ui/Toast", () => ({
  notify: { success: vi.fn(), error: vi.fn() },
}));

import api from "../../api";
import { ThemeProvider } from "../../theme/ThemeContext";
import OrganizationBilling from "./Billing";

// The five-plan payload an unmigrated deployment actually serves, in the order the backend
// returns it (crud.list_plans: price_monthly NULLS LAST, then name).
const PLANS = [
  {
    id: "p-dev", slug: "developer", name: "Developer", pricing_state: "PUBLISHED",
    price_monthly: 49, features: ["1 concurrent stream", "720p", "Community support"],
    billing_intervals: ["monthly"], self_service: true,
  },
  {
    id: "p-biz", slug: "business", name: "Business", pricing_state: "PUBLISHED",
    price_monthly: 249, features: ["5 concurrent streams", "1080p", "Recordings"],
    billing_intervals: ["monthly"], self_service: true,
  },
  {
    id: "p-ent", slug: "enterprise", name: "Enterprise", pricing_state: "NOT_PUBLISHED",
    price_monthly: null, features: ["Unlimited streams", "4K", "SSO"],
    billing_intervals: [], self_service: false,
  },
  {
    id: "p-pro", slug: "pro", name: "Pro", pricing_state: "NOT_PUBLISHED",
    price_monthly: null, features: ["5 concurrent streams", "1080p"],
    billing_intervals: [], self_service: false,
  },
  {
    id: "p-start", slug: "starter", name: "Starter", pricing_state: "NOT_PUBLISHED",
    price_monthly: null, features: ["1 concurrent stream", "720p"],
    billing_intervals: [], self_service: false,
  },
];

const overviewFor = (entitlements) => ({
  organization: { name: "Northwind" },
  entitlements: {
    plan: null, plan_slug: null, status: null, items: [], ...entitlements,
  },
});

async function renderBilling(entitlements = {}, { plans = PLANS, post } = {}) {
  api.get.mockImplementation((url) => {
    if (url === "/organization/overview") {
      return Promise.resolve({ data: overviewFor(entitlements) });
    }
    if (url === "/organization/plans") return Promise.resolve({ data: plans });
    return Promise.reject(new Error(`unexpected GET ${url}`));
  });
  if (post) api.post.mockImplementation(post);
  render(
    <MemoryRouter>
      <ThemeProvider>
        <OrganizationBilling />
      </ThemeProvider>
    </MemoryRouter>
  );
  await screen.findByText("Available Plans");
}

/** The plan grid: the row the cards live in. */
const planGrid = () =>
  screen.getByText("Developer", { selector: "p" }).closest("div.grid");
const cardTitles = () =>
  [...planGrid().children].map((c) => c.querySelector("p")?.textContent);
const planCard = (name) =>
  screen.getByText(name, { selector: "p" }).closest("div.flex.flex-col");
const cta = (name) => {
  const card = planCard(name);
  return within(card).queryByRole("button") || within(card).queryByRole("link");
};

beforeEach(() => {
  vi.clearAllMocks();
  window.history.replaceState({}, "", "/organization/billing");
});

describe("The offered catalog", () => {
  it("renders Developer", async () => {
    await renderBilling();
    expect(planCard("Developer")).toBeTruthy();
  });

  it("renders Business", async () => {
    await renderBilling();
    expect(planCard("Business")).toBeTruthy();
  });

  it("renders Enterprise", async () => {
    await renderBilling();
    expect(planCard("Enterprise")).toBeTruthy();
  });

  it("does not render Pro", async () => {
    await renderBilling();
    expect(screen.queryByText("Pro", { selector: "p" })).toBeNull();
  });

  it("does not render Starter", async () => {
    await renderBilling();
    expect(screen.queryByText("Starter", { selector: "p" })).toBeNull();
  });

  it("renders exactly three cards, in tier order", async () => {
    await renderBilling();
    expect(cardTitles()).toEqual(["Developer", "Business", "Enterprise"]);
  });

  it("leaves no empty card behind", async () => {
    await renderBilling();
    // Three children in a 3-column grid is one full row: no placeholder, no gap.
    expect(planGrid().children).toHaveLength(3);
    for (const card of planGrid().children) {
      expect(card.textContent.trim()).not.toBe("");
    }
  });

  it("removes them from the DOM rather than hiding them", async () => {
    await renderBilling();
    // Not rendered at all: absent from the page's text, and absent from the grid. A CSS-hidden
    // card would still satisfy neither — it would appear in textContent and in children.
    expect(document.body.textContent).not.toMatch(/\bPro\b/);
    expect(document.body.textContent).not.toMatch(/\bStarter\b/);
    expect(planGrid().children).toHaveLength(3);
    // And nothing inside the grid is display:none / visibility:hidden, which is how a
    // "removal" done in CSS would look. (A broad [class*="hidden"] sweep would false-positive
    // on Tailwind's `overflow-hidden`, which the subscription card uses legitimately.)
    for (const el of planGrid().querySelectorAll("*")) {
      const cls = el.className?.baseVal ?? el.className ?? "";
      expect(String(cls)).not.toMatch(/(^|\s)(hidden|invisible)(\s|$)/);
    }
  });

  it("offers no Talk to an expert button for a retired tier", async () => {
    await renderBilling();
    const experts = screen.getAllByRole("link", { name: /talk to an expert/i });
    // Enterprise only.
    expect(experts).toHaveLength(1);
  });

  it("renders the same three when the backend already filters them out", async () => {
    // The backend now excludes them too; the page must be correct either way.
    await renderBilling({}, { plans: PLANS.slice(0, 3) });
    expect(cardTitles()).toEqual(["Developer", "Business", "Enterprise"]);
  });
});

describe("The surviving plans are untouched", () => {
  it("keeps Developer's price and features", async () => {
    await renderBilling();
    const card = planCard("Developer");
    expect(card).toHaveTextContent("$49");
    expect(card).toHaveTextContent("/mo");
    for (const f of ["1 concurrent stream", "720p", "Community support"]) {
      expect(card).toHaveTextContent(f);
    }
  });

  it("keeps Business's price and features", async () => {
    await renderBilling();
    const card = planCard("Business");
    expect(card).toHaveTextContent("$249");
    for (const f of ["5 concurrent streams", "1080p", "Recordings"]) {
      expect(card).toHaveTextContent(f);
    }
  });

  it("keeps Enterprise quote-led", async () => {
    await renderBilling();
    expect(planCard("Enterprise")).toHaveTextContent(/not currently published/i);
    expect(cta("Enterprise")).toHaveTextContent(/talk to an expert/i);
    // A link to sales, never a button that could post a checkout.
    expect(within(planCard("Enterprise")).queryByRole("button")).toBeNull();
  });

  it("still starts Developer checkout with the right slug", async () => {
    await renderBilling({}, {
      post: () => Promise.resolve({ data: { checkout_url: "https://checkout.stripe.com/c/x" } }),
    });
    await userEvent.click(cta("Developer"));
    await waitFor(() => expect(api.post).toHaveBeenCalledTimes(1));
    expect(api.post.mock.calls[0][0]).toBe("/organization/billing/checkout-session");
    expect(api.post.mock.calls[0][1].plan_slug).toBe("developer");
  });

  it("still starts Business checkout with the right slug", async () => {
    await renderBilling({}, {
      post: () => Promise.resolve({ data: { checkout_url: "https://checkout.stripe.com/c/x" } }),
    });
    await userEvent.click(cta("Business"));
    await waitFor(() => expect(api.post).toHaveBeenCalledTimes(1));
    expect(api.post.mock.calls[0][1].plan_slug).toBe("business");
  });

  it("still marks the current plan", async () => {
    await renderBilling({ plan: "Business", plan_slug: "business", status: "active" });
    expect(within(planCard("Business")).getByText("Current")).toBeTruthy();
  });
});

describe("An organization still on a retired tier", () => {
  // The data-integrity case. The row and the subscription are untouched, so the page must
  // still report what they are actually on — it just stops OFFERING that tier to anyone.
  const onPro = { plan: "Pro", plan_slug: "pro", status: "active" };

  it("still sees its real plan name and status in the subscription card", async () => {
    await renderBilling(onPro);
    expect(screen.getByRole("heading", { name: /Pro Plan/i })).toBeTruthy();
    expect(screen.getByText("active")).toBeTruthy();
  });

  it("keeps its own retired card, and is offered no OTHER retired tier", async () => {
    // Retired tiers are dropped from the catalog, with one exception: the one this customer is
    // actually on. Hiding the tier somebody is paying for would leave them looking at three
    // cards, none of them theirs, with no indication which they hold. So Pro stays and is
    // marked current; Starter — which is nobody's here — is still gone.
    await renderBilling(onPro);
    // Pro ranks alongside Business (migrate_plan_names maps pro -> business), so it sorts
    // into that tier rather than to the end.
    expect(cardTitles()).toEqual(["Developer", "Business", "Pro", "Enterprise"]);
    expect(within(planCard("Pro")).getByText("Current")).toBeTruthy();
    expect(screen.queryByText("Starter", { selector: "p" })).toBeNull();
  });

  it("cannot re-buy the retired tier it is on", async () => {
    await renderBilling(onPro);
    const el = cta("Pro");
    expect(el).toHaveTextContent("Current Plan");
    expect(el).toBeDisabled();
  });

  it("has nothing written back on render", async () => {
    await renderBilling(onPro);
    expect(api.post).not.toHaveBeenCalled();
    expect(api.patch).not.toHaveBeenCalled();
    expect(api.delete).not.toHaveBeenCalled();
  });
});
