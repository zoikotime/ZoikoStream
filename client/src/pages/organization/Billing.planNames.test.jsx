// One name per tier on /organization/billing: Developer, Business, Enterprise.
//
// THE BUG: the current-plan card read "Developer Plan" while the Available Plans card for the
// same tier read "Developer". The server and the catalog both say "Developer" (plans.name);
// the card alone appended " Plan" in the browser. The word "plan" now lives in the card's
// label ("Current plan"), never in the tier's name. Internal slugs are untouched.
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../api", () => ({
  default: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
  errMsg: (e) => e?.message ?? "Something went wrong.",
  // The page asks this to tell "the server never answered" apart from an answer it did
  // not like, so the stub has to answer it too.
  transportMessage: (e) => (e?.response ? null : "Couldn't reach the ZoikoStream server."),
  // Billing now diagnoses a failed load instead of saying "try refreshing", so the stub
  // has to answer this too.
  diagnoseLoadError: (e) => (
    e?.response?.status
      ? `The platform API returned ${e.response.status}`
      : `The platform API is unreachable — check that the API server is running and that VITE_API_URL points at it.`
  ),
}));

vi.mock("../../ui/Toast", () => ({
  notify: { success: vi.fn(), error: vi.fn() },
}));

import api from "../../api";
import { ThemeProvider } from "../../theme/ThemeContext";
import OrganizationBilling from "./Billing";

const PLANS = [
  {
    id: "p-dev", slug: "developer", name: "Developer", pricing_state: "PUBLISHED",
    price_monthly: 49, features: ["1 concurrent stream"],
    billing_intervals: ["monthly"], self_service: true,
  },
  {
    id: "p-biz", slug: "business", name: "Business", pricing_state: "PUBLISHED",
    price_monthly: 249, features: ["5 concurrent streams"],
    billing_intervals: ["monthly"], self_service: true,
  },
  {
    id: "p-ent", slug: "enterprise", name: "Enterprise", pricing_state: "CUSTOM",
    price_monthly: null, features: ["Unlimited streams"],
    billing_intervals: [], self_service: false,
  },
];

const overviewFor = (entitlements) => ({
  organization: { name: "Northwind" },
  entitlements: {
    plan: null, plan_slug: null, status: null,
    checkout_locked: false, subscription_plan_slug: null, subscription_status: null,
    items: [], ...entitlements,
  },
});

function mountBilling() {
  render(
    <MemoryRouter>
      <ThemeProvider>
        <OrganizationBilling />
      </ThemeProvider>
    </MemoryRouter>
  );
}

async function renderBilling(entitlements) {
  api.get.mockImplementation((url) => {
    if (url === "/organization/overview") {
      return Promise.resolve({ data: overviewFor(entitlements) });
    }
    if (url === "/organization/plans") return Promise.resolve({ data: PLANS });
    return Promise.reject(new Error(`unexpected GET ${url}`));
  });
  // Default: the repair endpoint answers "nothing changed". Individual tests override it.
  api.post.mockImplementation((url) => (
    url === "/organization/billing/sync"
      ? Promise.resolve({ data: { synced: false, outcome: "current" } })
      : Promise.resolve({ data: {} })
  ));
  mountBilling();
  await screen.findByText("Available Plans");
}

/** api.post calls that are not the unidentified-subscription repair. */
const nonSyncPosts = () =>
  api.post.mock.calls.filter(([url]) => url !== "/organization/billing/sync");

const planCard = (name) => screen.getByText(name, { selector: "p" }).closest("div.flex.flex-col");

beforeEach(() => {
  vi.clearAllMocks();
  window.history.replaceState({}, "", "/organization/billing");
});


const subscribed = (name, slug) => ({
  plan: name, plan_slug: slug, status: "active", checkout_locked: true,
  subscription_plan_slug: slug, subscription_status: "active",
  current_period_end: "2026-11-07T00:00:00Z",
});
const NAMES = ["Developer", "Business", "Enterprise"];
// No word boundaries: textContent glues neighbouring elements together, so the old heading
// read "usageDeveloper Planactive" in the page text, which a \b at either end lets slip.
const SUFFIXED = /(Developer|Business|Enterprise) Plan/;

describe("one name per tier", () => {
  it.each([["Developer", "developer"], ["Business", "business"], ["Enterprise", "enterprise"]])(
    "a %s subscription (slug %s) is shown by its name alone, under a 'Current plan' label", async (name, slug) => {
      await renderBilling(subscribed(name, slug));
      const heading = screen.getByRole("heading", { level: 2, name });
      expect(heading).toHaveTextContent(new RegExp(`^${name}$`));
      expect(heading.parentElement.parentElement).toHaveTextContent(/^Current plan/);
      expect(document.body.textContent).not.toMatch(SUFFIXED);
    });

  it("labels the Available Plans cards with exactly the same names", async () => {
    await renderBilling(subscribed("Developer", "developer"));
    const cards = NAMES.map((n) => screen.getByText(n, { selector: "p" }).textContent);
    expect(cards).toEqual(NAMES);
    // The heading and the card spell the current tier identically.
    expect(screen.getByRole("heading", { level: 2, name: "Developer" })).toBeInTheDocument();
  });

  it("never shows a 'Developer Plan' style name anywhere on the page", async () => {
    await renderBilling(subscribed("Developer", "developer"));
    expect(document.body.textContent).not.toMatch(SUFFIXED);
    for (const el of document.querySelectorAll("[aria-label], [title]")) {
      expect(`${el.getAttribute("aria-label") || ""} ${el.getAttribute("title") || ""}`).not.toMatch(SUFFIXED);
    }
  });

  it("with no subscription, says so rather than inventing a name", async () => {
    await renderBilling({});
    expect(screen.getByRole("heading", { level: 2, name: "No plan" })).toBeInTheDocument();
  });
});

describe("plan changes still send the internal slugs", () => {
  it("an upgrade from Developer schedules plan_slug 'business'", async () => {
    const user = userEvent.setup();
    await renderBilling(subscribed("Developer", "developer"));
    await user.click(within(planCard("Business")).getByRole("button", { name: /upgrade/i }));
    await waitFor(() => expect(nonSyncPosts()).toContainEqual([
      "/organization/billing/plan-change", expect.objectContaining({ plan_slug: "business" }),
    ]));
  });

  it("a downgrade from Business confirms by name and schedules plan_slug 'developer'", async () => {
    const user = userEvent.setup();
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    await renderBilling(subscribed("Business", "business"));
    await user.click(within(planCard("Developer")).getByRole("button", { name: /downgrade|move to/i }));
    expect(confirm.mock.calls[0][0]).toMatch(/^Move to Developer /);
    expect(confirm.mock.calls[0][0]).not.toMatch(SUFFIXED);
    await waitFor(() => expect(nonSyncPosts()).toContainEqual([
      "/organization/billing/plan-change", expect.objectContaining({ plan_slug: "developer" }),
    ]));
    confirm.mockRestore();
  });
});
