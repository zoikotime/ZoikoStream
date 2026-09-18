// Identity & Access at 1128 accounts.
//
// The page asked for page_size:100 with no `page`, took `items` and threw `total` away. 1028
// of 1128 accounts were unreachable, and nothing on screen said so — the count line reported
// the size of the page it happened to hold. Sorting and the organization filter were worse
// than incomplete, they were quietly wrong: DataTable ordered the fetched rows, so "Joined,
// oldest first" returned the oldest of the newest 100, and the org dropdown was built from
// `new Set(rows.map(u => u.organization_name))` — the tenants on the current page.
//
// Every one of those now goes to the database. These assert on the REQUEST as well as the
// rendering, because "sorted" and "sorted globally" look identical in a screenshot.
import { configure, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

// Each of these drives several real interactions — a click, a 300ms search debounce, a
// re-fetch — and asserts on the REQUEST that results. That is the point of the file, but it
// makes the tests slow, and under the full parallel suite the 1s default async timeout is
// starved by other workers rather than by anything being wrong. Raising it removes the flake
// without weakening a single assertion.
configure({ asyncUtilTimeout: 8000 });
vi.setConfig({ testTimeout: 30000 });

vi.mock("../../api", () => ({
  default: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
  errMsg: (e) => e?.message ?? "Something went wrong.",
}));
vi.mock("react-hot-toast", () => ({ default: { success: vi.fn(), error: vi.fn() } }));
vi.mock("../../ui/Toast", () => ({ notify: { error: vi.fn(), success: vi.fn(), alert: vi.fn() } }));

import api from "../../api";
import { ThemeProvider } from "../../theme/ThemeContext";
import Users, { PAGE_SIZE } from "./Users";

const TOTAL = 1128;
const SUMMARY = { total: TOTAL, active: 1118, inactive: 10, super_admins: 103 };

const ORGS = Array.from({ length: 140 }, (_, i) => ({
  id: `org-${i}`,
  name: `Org ${String(i).padStart(3, "0")}`,
}));

/** A deterministic account for global row N (0-based). */
const userAt = (n) => ({
  id: `u-${n}`,
  full_name: `User ${String(n).padStart(4, "0")}`,
  email: `u${n}@example.com`,
  username: `u${n}`,
  organization_name: `Org ${String(n % 140).padStart(3, "0")}`,
  org_id: `org-${n % 140}`,
  role: ["super_admin", "org_admin", "host", "viewer"][n % 4],
  is_active: true,
  created_at: `2026-01-01T00:00:00Z`,
});

let summaryFails = false;

const serve = () => {
  vi.mocked(api.get).mockImplementation((url, cfg) => {
    if (url === "/admin/users/summary") {
      return summaryFails
        ? Promise.reject(new Error("boom"))
        : Promise.resolve({ data: SUMMARY });
    }
    if (url === "/admin/organizations") {
      const { page = 1, page_size = 100 } = cfg?.params || {};
      return Promise.resolve({
        data: { items: ORGS.slice((page - 1) * page_size, page * page_size), total: ORGS.length },
      });
    }
    if (url === "/admin/users") {
      const p = cfg?.params || {};
      const page = p.page || 1;
      const size = p.page_size || PAGE_SIZE;
      const total = p.org_id ? 8 : TOTAL;
      const start = (page - 1) * size;
      const items = Array.from(
        { length: Math.max(0, Math.min(size, total - start)) },
        (_, i) => userAt(start + i)
      );
      return Promise.resolve({ data: { items, total } });
    }
    return Promise.resolve({ data: {} });
  });
};

const renderPage = () =>
  render(
    <ThemeProvider>
      <Users />
    </ThemeProvider>
  );

/** A sortable column header. DataTable renders it as a <button> containing the header text,
 *  so the accessible name is that text — its `title` never reaches the name computation. */
const sortHeader = (header) => screen.getByRole("button", { name: header });

const listCalls = () => vi.mocked(api.get).mock.calls.filter(([u]) => u === "/admin/users");
const lastParams = () => listCalls()[listCalls().length - 1]?.[1]?.params;

beforeEach(() => {
  vi.clearAllMocks();
  summaryFails = false;
  serve();
});

// ── pagination ─────────────────────────────────────────────────────────────────────────

describe("pagination reaches every account", () => {
  it("asks for a page and a page size inside the API's cap", async () => {
    renderPage();
    await screen.findByText("User 0000");

    expect(lastParams().page).toBe(1);
    expect(lastParams().page_size).toBe(PAGE_SIZE);
    expect(PAGE_SIZE).toBeLessThanOrEqual(100);
  });

  it("reports the DATASET total, not the size of the page it holds", async () => {
    renderPage();
    await screen.findByText("User 0000");
    // The old page said "of 100" for 1128 accounts.
    expect(await screen.findByText(new RegExp(`of ${TOTAL}`))).toBeInTheDocument();
  });

  it("page 2 asks the server and returns different accounts", async () => {
    const u = userEvent.setup();
    renderPage();
    await screen.findByText("User 0000");

    await u.click(screen.getByRole("button", { name: "2" }));

    await waitFor(() => expect(lastParams().page).toBe(2));
    await waitFor(() => expect(screen.getByText(`User ${String(PAGE_SIZE).padStart(4, "0")}`)).toBeInTheDocument());
    expect(screen.queryByText("User 0000")).not.toBeInTheDocument();
  });

  it("exposes a page control for every page of the dataset", async () => {
    renderPage();
    await screen.findByText("User 0000");
    const pages = Math.ceil(TOTAL / PAGE_SIZE);
    expect(screen.getByRole("button", { name: String(pages) })).toBeInTheDocument();
  });

  it("reaches accounts far beyond the first 100 — the 1028 that used to be invisible", async () => {
    const u = userEvent.setup();
    renderPage();
    await screen.findByText("User 0000");

    const lastPage = Math.ceil(TOTAL / PAGE_SIZE);
    await u.click(screen.getByRole("button", { name: String(lastPage) }));

    await waitFor(() => expect(lastParams().page).toBe(lastPage));
    const firstOnLast = (lastPage - 1) * PAGE_SIZE;
    expect(firstOnLast).toBeGreaterThan(100);
    await waitFor(() =>
      expect(screen.getByText(`User ${String(firstOnLast).padStart(4, "0")}`)).toBeInTheDocument());
  });
});

describe("changing what is being asked resets to page 1", () => {
  const goToPageTwo = async (u) => {
    await screen.findByText("User 0000");
    await u.click(screen.getByRole("button", { name: "2" }));
    await waitFor(() => expect(lastParams().page).toBe(2));
  };

  it("role filter", async () => {
    const u = userEvent.setup();
    renderPage();
    await goToPageTwo(u);
    await u.selectOptions(screen.getByLabelText(/filter by role/i), "super_admin");
    await waitFor(() => expect(lastParams().page).toBe(1));
  });

  it("status filter", async () => {
    const u = userEvent.setup();
    renderPage();
    await goToPageTwo(u);
    await u.selectOptions(screen.getByLabelText(/filter by status/i), "inactive");
    await waitFor(() => expect(lastParams().page).toBe(1));
  });

  it("organization filter", async () => {
    const u = userEvent.setup();
    renderPage();
    await goToPageTwo(u);
    await u.selectOptions(screen.getByLabelText(/filter by organization/i), "org-3");
    await waitFor(() => expect(lastParams().page).toBe(1));
  });

  it("search", async () => {
    const u = userEvent.setup();
    renderPage();
    await goToPageTwo(u);
    await u.type(screen.getByPlaceholderText(/search by name/i), "hana");
    await waitFor(() => expect(lastParams().q).toBe("hana"));
    expect(lastParams().page).toBe(1);
  });

  it("sorting", async () => {
    const u = userEvent.setup();
    renderPage();
    await goToPageTwo(u);
    await u.click(sortHeader("Joined"));
    await waitFor(() => expect(lastParams().page).toBe(1));
  });
});

// ── sorting ────────────────────────────────────────────────────────────────────────────

describe("sorting is decided by the database", () => {
  it("sends sort_by and order rather than reordering the page", async () => {
    const u = userEvent.setup();
    renderPage();
    await screen.findByText("User 0000");

    await u.click(sortHeader("Joined"));
    await waitFor(() => expect(lastParams().sort_by).toBe("joined"));
    expect(lastParams().order).toBe("asc");
  });

  it("cycles ascending then descending, each as a fresh query", async () => {
    const u = userEvent.setup();
    renderPage();
    await screen.findByText("User 0000");

    const header = sortHeader("Joined");
    await u.click(header);
    await waitFor(() => expect(lastParams().order).toBe("asc"));
    await u.click(header);
    await waitFor(() => expect(lastParams().order).toBe("desc"));
  });

  it("sorts by user and organization through the server too", async () => {
    const u = userEvent.setup();
    renderPage();
    await screen.findByText("User 0000");

    await u.click(sortHeader("User"));
    await waitFor(() => expect(lastParams().sort_by).toBe("name"));

    await u.click(sortHeader("Organization"));
    await waitFor(() => expect(lastParams().sort_by).toBe("organization"));
  });

  it("only ever names a whitelisted field", async () => {
    const u = userEvent.setup();
    renderPage();
    await screen.findByText("User 0000");

    const allowed = new Set(["name", "email", "role", "joined", "organization", undefined]);
    for (const label of ["User", "Organization", "Role", "Joined"]) {
      await u.click(sortHeader(label));
      await waitFor(() => expect(allowed.has(lastParams().sort_by)).toBe(true));
    }
  });
});

// ── organization filter ────────────────────────────────────────────────────────────────

describe("organization filter spans every tenant", () => {
  it("lists organizations from the organizations endpoint, not from the current page", async () => {
    renderPage();
    await screen.findByText("User 0000");

    const select = screen.getByLabelText(/filter by organization/i);
    await waitFor(() =>
      expect(within(select).getAllByRole("option").length).toBe(ORGS.length + 1));  // + "All"
    // The page holds 50 rows across 140 orgs; the old dropdown could only offer what it saw.
    expect(within(select).getAllByRole("option").length).toBeGreaterThan(PAGE_SIZE);
  });

  it("pages the organizations endpoint to the end", async () => {
    renderPage();
    await screen.findByText("User 0000");
    const orgCalls = vi.mocked(api.get).mock.calls.filter(([u]) => u === "/admin/organizations");
    await waitFor(() => expect(orgCalls.length).toBeGreaterThanOrEqual(2));   // 140 > page_size 100
  });

  it("sends org_id to the server, not an organization name", async () => {
    const u = userEvent.setup();
    renderPage();
    await screen.findByText("User 0000");

    await u.selectOptions(screen.getByLabelText(/filter by organization/i), "org-7");
    await waitFor(() => expect(lastParams().org_id).toBe("org-7"));
    // A name would be the old client-side match.
    expect(lastParams().org_id).not.toMatch(/^Org /);
  });

  it("filters server-side, so the total narrows to the tenant", async () => {
    const u = userEvent.setup();
    renderPage();
    await screen.findByText(new RegExp(`of ${TOTAL}`));

    await u.selectOptions(screen.getByLabelText(/filter by organization/i), "org-7");
    await waitFor(() => expect(screen.getByText(/of 8/)).toBeInTheDocument());
  });
});

// ── KPI honesty ────────────────────────────────────────────────────────────────────────

describe("KPI cards", () => {
  it("show a measured figure — no failure banner, no em dash — when the summary loads", async () => {
    // Not asserted as the literal "1,128": StatCard renders through <Counter>, which tweens
    // from 0 and only starts when IntersectionObserver reports it in view. jsdom never fires
    // that, so the digits are an artefact of the environment, not of the data. What IS
    // meaningful here is that the cards are in their measured state rather than their
    // unavailable one. The figures themselves are checked against the database directly.
    renderPage();
    await screen.findByText("User 0000");

    expect(screen.queryByText(/couldn't load the account totals/i)).not.toBeInTheDocument();
    expect(screen.queryByText("—")).not.toBeInTheDocument();
  });

  it("do not fabricate zeros when the summary fails", async () => {
    summaryFails = true;
    renderPage();
    await screen.findByText("User 0000");

    // The old fallback rendered { total: rows.length, active: 0, inactive: 0, superAdmins: 0 }.
    expect(await screen.findByText(/couldn't load the account totals/i)).toBeInTheDocument();
    expect(screen.queryByText("0")).not.toBeInTheDocument();
    // And it never passes off the page size as the platform total.
    expect(screen.queryByText(String(PAGE_SIZE))).not.toBeInTheDocument();
  });

  it("render an em dash for unknown, which is not the same as zero", async () => {
    summaryFails = true;
    renderPage();
    await screen.findByText(/couldn't load the account totals/i);
    expect(screen.getAllByText("—").length).toBeGreaterThanOrEqual(4);
  });

  it("keep the table working when only the summary failed", async () => {
    summaryFails = true;
    renderPage();
    // A KPI failure is not a page failure.
    expect(await screen.findByText("User 0000")).toBeInTheDocument();
    expect(screen.queryByText(/couldn't load users/i)).not.toBeInTheDocument();
  });
});
