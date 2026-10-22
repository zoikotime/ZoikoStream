// Support Operations: what the page shows when there is nothing to show.
//
// The page has two INDEPENDENT data sections — the ticket queue and ORG-009 authorized
// support access — each with its own request and its own empty state. Pinned here:
//
//   * a failed ticket queue is reported inside the ticket panel and nowhere else. It used to
//     `return` an error card in place of the whole page, which took the heading, the
//     counters, the filter and the entire support-access panel down with it — including the
//     control that ENDS a live support session, over an unrelated request;
//   * loading, failed and genuinely-empty are three distinguishable states, and the queue
//     never claims to be empty while it is still loading or after it failed;
//   * the two empty sections are not the same size, so a quiet platform reads as a hierarchy
//     rather than as the same placeholder printed twice.
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";

vi.mock("../../api", () => ({
  default: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
  errMsg: (e) => e?.message ?? "failed",
  diagnoseLoadError: (e) => e?.message ?? "failed",
  AUTH_EXPIRED_EVENT: "zoiko:auth-expired",
}));
vi.mock("../../auth/AuthContext", () => ({
  useAuth: () => ({ user: { id: "u-me", full_name: "Ada Ops", role: "super_admin" }, logout: vi.fn() }),
}));

import api from "../../api";
import { ThemeProvider } from "../../theme/ThemeContext";
import Support from "./Support";

const EMPTY_ACCESS = { total: 0, page: 1, page_size: 100,
  summary: { requested: 0, approved: 0, active: 0, ended: 0, expired: 0, denied: 0 }, items: [] };
const SUMMARY = { open: 0, in_progress: 0, resolved: 0, urgent_unresolved: 0 };

// Every endpoint answers empty unless a test says otherwise. `fail` names the URL fragment
// whose request should reject, so one section can break while the others stay healthy.
const serve = ({ fail = null, tickets = [], orgs = [] } = {}) =>
  vi.mocked(api.get).mockImplementation((url) => {
    if (fail && url.includes(fail)) return Promise.reject(new Error("boom"));
    if (url.includes("/admin/support-tickets/summary")) return Promise.resolve({ data: SUMMARY });
    if (url.includes("/admin/support-tickets")) return Promise.resolve({ data: { items: tickets } });
    if (url.includes("/admin/support-access")) return Promise.resolve({ data: EMPTY_ACCESS });
    if (url.includes("/admin/organizations")) return Promise.resolve({ data: { items: orgs } });
    return Promise.resolve({ data: {} });
  });

const show = () =>
  render(
    <ThemeProvider>
      <MemoryRouter>
        <Support />
      </MemoryRouter>
    </ThemeProvider>
  );

const accessPanel = () => screen.queryByText("Authorized support access");

beforeEach(() => vi.clearAllMocks());

describe("an empty platform", () => {
  it("shows one ticket empty state, and keeps support access on the page", async () => {
    serve();
    show();
    expect(await screen.findByText("No support tickets")).toBeInTheDocument();
    expect(accessPanel()).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Request access/i })).toBeInTheDocument();
  });

  it("does not print the ticket empty state twice", async () => {
    serve();
    show();
    await screen.findByText("No support tickets");
    expect(screen.getAllByText("No support tickets")).toHaveLength(1);
  });

  it("gives the two empty sections different weight rather than one placeholder twice", async () => {
    serve();
    const { container } = show();
    await screen.findByText("No support tickets");
    const access = await screen.findByText("No support access has been requested");
    // The secondary section is compact: no decorative ring cluster, smaller type.
    expect(access.className).toContain("text-[13px]");
    expect(screen.getByText("No support tickets").className).toContain("text-[15px]");
    expect(container.querySelectorAll(".zk-float")).toHaveLength(1);   // the primary one only
  });

  it("keeps the counters and the status filter", async () => {
    serve();
    show();
    await screen.findByText("No support tickets");
    for (const l of ["Open", "In Progress", "Resolved", "Urgent (unresolved)"]) {
      expect(screen.getAllByText(l).length).toBeGreaterThan(0);
    }
    expect(screen.getByLabelText("Filter by status")).toBeInTheDocument();
  });
});

describe("a filtered queue with no matches", () => {
  it("says why it is empty instead of claiming nothing was ever logged", async () => {
    serve();
    show();
    await screen.findByText("Nothing logged yet.");
    fireEvent.change(screen.getByLabelText("Filter by status"), { target: { value: "resolved" } });
    expect(await screen.findByText("No resolved tickets.")).toBeInTheDocument();
    expect(screen.queryByText("Nothing logged yet.")).toBeNull();
  });
});

describe("a failed ticket queue", () => {
  it("does not take the rest of the page with it", async () => {
    serve({ fail: "/admin/support-tickets" });
    show();
    await screen.findByRole("alert");
    // The part that must survive: the ORG-009 workflow is unrelated to this request.
    expect(accessPanel()).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Request access/i })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Support Operations" })).toBeInTheDocument();
  });

  it("is distinguishable from empty — it never claims the queue is empty", async () => {
    serve({ fail: "/admin/support-tickets" });
    show();
    expect(await screen.findByText(/Couldn.t load the ticket queue/)).toBeInTheDocument();
    expect(screen.queryByText("No support tickets")).toBeNull();
  });

  it("offers a retry rather than telling the operator to refresh", async () => {
    serve({ fail: "/admin/support-tickets" });
    show();
    // Scoped to the queue's own alert: the totals banner above carries a Retry of its own,
    // because this failure takes out /support-tickets/summary too.
    const retry = within(await screen.findByRole("alert")).getByRole("button", { name: "Retry" });
    vi.mocked(api.get).mockClear();
    serve();
    fireEvent.click(retry);
    await waitFor(() => expect(screen.queryByText("No support tickets")).toBeInTheDocument());
  });
});

describe("a failed access panel", () => {
  it("leaves the ticket queue alone", async () => {
    serve({ fail: "/admin/support-access" });
    show();
    expect(await screen.findByText("No support tickets")).toBeInTheDocument();
    expect(accessPanel()).toBeInTheDocument();
    expect(await screen.findByText(/Couldn.t load support access requests/)).toBeInTheDocument();
  });
});

describe("while loading", () => {
  it("claims nothing — no empty state before the answer arrives", async () => {
    let release;
    vi.mocked(api.get).mockImplementation((url) =>
      url.includes("/admin/support-tickets") && !url.includes("summary")
        ? new Promise((r) => { release = () => r({ data: { items: [] } }); })
        : Promise.resolve({ data: url.includes("/admin/support-access") ? EMPTY_ACCESS : { items: [] } })
    );
    show();
    expect(screen.queryByText("No support tickets")).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
    release();
    expect(await screen.findByText("No support tickets")).toBeInTheDocument();
  });
});
