import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../api", () => ({
  default: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
  errMsg: (e) => e?.message ?? "Something went wrong.",
  transportMessage: (e) => (e?.response ? null : "Couldn't reach the ZoikoStream server."),
  diagnoseLoadError: (e) => `Error: ${e?.message}`,
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
    price_monthly: 49, features: ["1 concurrent stream", "720p", "Community support"],
    billing_intervals: ["monthly"], self_service: true,
  },
  {
    id: "p-biz", slug: "business", name: "Business", pricing_state: "PUBLISHED",
    price_monthly: 249, features: ["5 concurrent streams", "1080p", "Recordings"],
    billing_intervals: ["monthly"], self_service: true,
  },
  {
    id: "p-ent", slug: "enterprise", name: "Enterprise", pricing_state: "CUSTOM",
    price_monthly: null, features: ["Unlimited streams", "4K", "SSO"],
    billing_intervals: [], self_service: false,
  },
];

const overviewFor = (entitlements) => ({
  organization: { name: "Northwind" },
  entitlements: {
    plan: "Developer",
    plan_slug: "developer",
    status: "active",
    checkout_locked: true,
    subscription_plan_slug: "developer",
    subscription_status: "active",
    items: [],
    ...entitlements,
  },
});

async function renderBilling(entitlements = {}) {
  api.get.mockImplementation((url) => {
    if (url === "/organization/overview") return Promise.resolve({ data: overviewFor(entitlements) });
    if (url === "/organization/plans") return Promise.resolve({ data: PLANS });
    return Promise.reject(new Error(`unexpected GET ${url}`));
  });

  render(
    <MemoryRouter>
      <ThemeProvider>
        <OrganizationBilling />
      </ThemeProvider>
    </MemoryRouter>
  );

  await screen.findByText("Available Plans");
}

function getPlanCard(name) {
  const title = screen.getByText(name, { selector: "p" });
  return title.closest("[data-plan-slug]");
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("Billing Plan Card Selection and Border State", () => {
  it("Developer initially displays the correct current-subscription state and selected border", async () => {
    await renderBilling({ plan: "Developer", plan_slug: "developer" });

    const devCard = getPlanCard("Developer");
    const bizCard = getPlanCard("Business");
    const entCard = getPlanCard("Enterprise");

    // Developer card has Current badge and disabled Current Plan button
    expect(within(devCard).getByText("Current")).toBeInTheDocument();
    expect(within(devCard).getByRole("button", { name: /current plan/i })).toBeDisabled();

    // Developer has authoritative selection state
    expect(devCard).toHaveAttribute("data-selected", "true");
    expect(devCard).toHaveAttribute("data-current", "true");
    expect(devCard.className).toContain("border-violet-500");

    // Business and Enterprise are not selected
    expect(bizCard).toHaveAttribute("data-selected", "false");
    expect(bizCard).toHaveAttribute("data-current", "false");
    expect(bizCard.className).not.toContain("border-violet-500");

    expect(entCard).toHaveAttribute("data-selected", "false");
    expect(entCard.className).not.toContain("border-violet-500");
  });

  it("selecting Business moves the selection highlight to Business and removes it from Developer", async () => {
    await renderBilling({ plan: "Developer", plan_slug: "developer" });

    const devCard = getPlanCard("Developer");
    const bizCard = getPlanCard("Business");

    // Click Business card to select it
    await userEvent.click(bizCard);

    // Business is now selected with highlight border
    expect(bizCard).toHaveAttribute("data-selected", "true");
    expect(bizCard.className).toContain("border-violet-500");

    // Developer loses selection highlight border
    expect(devCard).toHaveAttribute("data-selected", "false");
    expect(devCard.className).not.toContain("border-violet-500");

    // The Current badge and Current Plan button remain attached to Developer
    expect(within(devCard).getByText("Current")).toBeInTheDocument();
    expect(within(devCard).getByRole("button", { name: /current plan/i })).toBeDisabled();
    expect(within(bizCard).queryByText("Current")).toBeNull();
  });

  it("selecting Developer again moves the highlight back to Developer", async () => {
    await renderBilling({ plan: "Developer", plan_slug: "developer" });

    const devCard = getPlanCard("Developer");
    const bizCard = getPlanCard("Business");

    // Switch to Business
    await userEvent.click(bizCard);
    expect(bizCard).toHaveAttribute("data-selected", "true");
    expect(devCard).toHaveAttribute("data-selected", "false");

    // Switch back to Developer
    await userEvent.click(devCard);
    expect(devCard).toHaveAttribute("data-selected", "true");
    expect(devCard.className).toContain("border-violet-500");

    expect(bizCard).toHaveAttribute("data-selected", "false");
    expect(bizCard.className).not.toContain("border-violet-500");
  });

  it("clicking the already selected card does not break or duplicate selection styling", async () => {
    await renderBilling({ plan: "Developer", plan_slug: "developer" });

    const devCard = getPlanCard("Developer");
    const bizCard = getPlanCard("Business");

    // Click Developer (which is already selected)
    await userEvent.click(devCard);
    expect(devCard).toHaveAttribute("data-selected", "true");
    expect(devCard.className).toContain("border-violet-500");
    expect(bizCard).toHaveAttribute("data-selected", "false");

    // Switch to Business, then click Business again
    await userEvent.click(bizCard);
    await userEvent.click(bizCard);
    expect(bizCard).toHaveAttribute("data-selected", "true");
    expect(bizCard.className).toContain("border-violet-500");
    expect(devCard).toHaveAttribute("data-selected", "false");
  });

  it("the Current Plan button and Business Upgrade button retain their correct behavior", async () => {
    await renderBilling({ plan: "Developer", plan_slug: "developer" });

    const devCard = getPlanCard("Developer");
    const bizCard = getPlanCard("Business");

    const currentBtn = within(devCard).getByRole("button", { name: /current plan/i });
    expect(currentBtn).toBeDisabled();
    expect(currentBtn).toHaveAttribute("aria-current", "true");

    const upgradeBtn = within(bizCard).getByRole("button", { name: /upgrade/i });
    expect(upgradeBtn).toBeEnabled();
    expect(upgradeBtn).toHaveAttribute("aria-label", "Upgrade to the Business plan");
  });

  it("keyboard focus does not accidentally change the selected plan", async () => {
    await renderBilling({ plan: "Developer", plan_slug: "developer" });

    const devCard = getPlanCard("Developer");
    const bizCard = getPlanCard("Business");

    // Initially Developer is selected
    expect(devCard).toHaveAttribute("data-selected", "true");
    expect(bizCard).toHaveAttribute("data-selected", "false");

    // Tab through to focus on Business card
    bizCard.focus();
    expect(document.activeElement).toBe(bizCard);

    // Focusing Business MUST NOT change the selected plan
    expect(bizCard).toHaveAttribute("data-selected", "false");
    expect(devCard).toHaveAttribute("data-selected", "true");

    // Explicitly pressing Enter on Business activates selection
    fireEvent.keyDown(bizCard, { key: "Enter" });
    expect(bizCard).toHaveAttribute("data-selected", "true");
    expect(devCard).toHaveAttribute("data-selected", "false");
  });

  it("Enterprise remains unavailable and non-selectable according to product rules", async () => {
    await renderBilling({ plan: "Developer", plan_slug: "developer" });

    const entCard = getPlanCard("Enterprise");
    const devCard = getPlanCard("Developer");

    // Enterprise is custom / un-published with self_service=false
    expect(within(entCard).getByText(/Contact Sales|Not currently published/)).toBeInTheDocument();
    expect(within(entCard).getByRole("link", { name: /talk to an expert/i })).toBeInTheDocument();
    expect(entCard).not.toHaveAttribute("tabindex");

    // Clicking Enterprise card does not select it
    await userEvent.click(entCard);
    expect(entCard).toHaveAttribute("data-selected", "false");
    expect(entCard.className).not.toContain("border-violet-500");
    expect(devCard).toHaveAttribute("data-selected", "true");
  });
});
