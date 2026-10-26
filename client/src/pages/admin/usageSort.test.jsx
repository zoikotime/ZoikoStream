// Sorting on Usage & Entitlements happens in SQL, not in the browser.
//
// The page sends `sort` to GET /admin/usage and renders the rows in the order they come back.
// services/admin.usage_overview orders by
//
//     Organization.storage_used_gb.desc().nulls_last(), Organization.name.asc(), id.asc()
//
// and applies that ORDER BY before .offset().limit(), so the ordering is over the whole
// filtered set rather than over one page.
//
// What can regress here, and is therefore pinned: the option sending the wrong value, the page
// failing to re-ask when the option changes, the selection being dropped when another filter
// moves, and — the one that would silently undo server-side ordering — the page sorting the
// rows again on arrival.
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
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

const quota = (used) => ({
  key: "storage", label: "Storage", used, limit: 1000, unit: "GB",
  percent: used / 10, remaining: 1000 - used, unlimited: false,
  quota_state: "within", usage_state: "measured", enforced: true, enforced_limit: 1000,
});

const org = (name, used) => ({
  org_id: name, organization: name, is_test: false,
  plan: "Business", plan_slug: "business", subscription_status: "active",
  entitlement_source: "subscription", current_period_end: null, trial_ends_at: null,
  quotas: [quota(used)], metrics: [],
});

// The dataset from the report, in the order the server returns it for sort=storage:
// heaviest first, zero still a measured value above anything unmeasured.
const BY_STORAGE = [
  org("Harish Events", 850),
  org("zoiko", 320),
  org("Sample Org", 125),
  org("Acme", 0),
  org("Unknown Usage", 0),
];
const BY_NAME = [...BY_STORAGE].sort((a, b) => a.organization.localeCompare(b.organization));

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

const open = (rows) => { serve(rows); return show(); };

// The order actually painted, read off the rendered rows.
const rendered = () =>
  screen.getAllByTestId("usage-row").map((li) => li.querySelector("p")?.textContent);

const sortSelect = () => screen.getByLabelText(/sort/i);
const usageCalls = () => vi.mocked(api.get).mock.calls.filter(([u]) => u.includes("/admin/usage"));
const lastParams = () => usageCalls().at(-1)?.[1]?.params;

beforeEach(() => vi.clearAllMocks());

describe("the page renders the server's order", () => {
  it("paints the rows exactly as received, heaviest first", async () => {
    open(BY_STORAGE);
    await screen.findByText("Harish Events");
    expect(rendered()).toEqual(["Harish Events", "zoiko", "Sample Org", "Acme", "Unknown Usage"]);
  });

  it("does not re-sort them alphabetically on arrival", async () => {
    // The regression that would silently undo SQL ordering: a .sort() after the fetch.
    open(BY_STORAGE);
    await screen.findByText("Harish Events");
    expect(rendered()).not.toEqual(BY_NAME.map((o) => o.organization));
    expect(rendered()[0]).toBe("Harish Events");
  });

  it("paints a name-ordered response in that order too", async () => {
    // The page is not opinionated about order; it shows what the query returned.
    open(BY_NAME);
    await screen.findByText("Acme");
    expect(rendered()).toEqual(BY_NAME.map((o) => o.organization));
  });

  it("treats zero as a measured value, not as missing", async () => {
    open(BY_STORAGE);
    await screen.findByText("Acme");
    const acme = screen.getAllByTestId("usage-row").find((li) => li.textContent.includes("Acme"));
    expect(acme.textContent).toContain("0 / 1000 GB");
    expect(acme.textContent).not.toContain("Not measured");
  });
});

describe("the sort option reaches the query", () => {
  it("offers the three the backend accepts", async () => {
    open(BY_NAME);
    await screen.findByText("Acme");
    const values = [...sortSelect().querySelectorAll("option")].map((o) => o.value);
    // services/admin.usage_overview validates ^(name|-name|storage)$ — anything else is a 422.
    expect(values).toEqual(["name", "-name", "storage"]);
  });

  it("asks for storage ordering when that option is chosen", async () => {
    open(BY_NAME);
    await screen.findByText("Acme");
    fireEvent.change(sortSelect(), { target: { value: "storage" } });
    await waitFor(() => expect(lastParams()?.sort).toBe("storage"));
  });

  it("re-asks the server rather than reordering what it already has", async () => {
    open(BY_NAME);
    await screen.findByText("Acme");
    const before = usageCalls().length;
    fireEvent.change(sortSelect(), { target: { value: "storage" } });
    await waitFor(() => expect(usageCalls().length).toBeGreaterThan(before));
  });

  it("switches back to the other orders", async () => {
    open(BY_NAME);
    await screen.findByText("Acme");
    for (const value of ["storage", "-name", "name"]) {
      fireEvent.change(sortSelect(), { target: { value } });
      await waitFor(() => expect(lastParams()?.sort).toBe(value));
    }
  });
});

describe("the selection survives the other controls", () => {
  it("is still storage after a plan filter changes", async () => {
    open(BY_STORAGE);
    await screen.findByText("Harish Events");
    fireEvent.change(sortSelect(), { target: { value: "storage" } });
    await waitFor(() => expect(lastParams()?.sort).toBe("storage"));
    fireEvent.change(screen.getByLabelText(/plan/i), { target: { value: "business" } });
    await waitFor(() => expect(lastParams()?.plan).toBe("business"));
    expect(lastParams()?.sort).toBe("storage");
    expect(sortSelect()).toHaveValue("storage");
  });

  it("is still storage after a subscription-state filter changes", async () => {
    open(BY_STORAGE);
    await screen.findByText("Harish Events");
    fireEvent.change(sortSelect(), { target: { value: "storage" } });
    await waitFor(() => expect(lastParams()?.sort).toBe("storage"));
    fireEvent.change(screen.getByLabelText("Filter by subscription status"), { target: { value: "active" } });
    await waitFor(() => expect(lastParams()?.status).toBe("active"));
    expect(lastParams()?.sort).toBe("storage");
  });

  it("is still storage after a search", async () => {
    open(BY_STORAGE);
    await screen.findByText("Harish Events");
    fireEvent.change(sortSelect(), { target: { value: "storage" } });
    await waitFor(() => expect(lastParams()?.sort).toBe("storage"));
    fireEvent.change(screen.getByPlaceholderText(/Search organizations/i), { target: { value: "zoi" } });
    await waitFor(() => expect(lastParams()?.q).toBe("zoi"));
    expect(lastParams()?.sort).toBe("storage");
  });

  it("sends the sort with every page request", async () => {
    // Ordering is applied in SQL before OFFSET/LIMIT, so page 2 is only meaningful if the
    // page keeps asking for the same order.
    open(BY_STORAGE);
    await screen.findByText("Harish Events");
    fireEvent.change(sortSelect(), { target: { value: "storage" } });
    await waitFor(() => expect(lastParams()?.sort).toBe("storage"));
    for (const [, cfg] of usageCalls().slice(-1)) {
      expect(cfg.params).toHaveProperty("page");
      expect(cfg.params).toHaveProperty("page_size");
    }
  });
});
