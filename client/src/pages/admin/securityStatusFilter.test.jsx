// Trust & Safety: the status strip, the status dropdown and the Cases table.
//
// `status` was already ONE piece of state shared by the dropdown and the cards — there was no
// duplicate to reconcile. Three things were wrong around it:
//
//   1. Only Open and Resolved had cards, while STATUSES is open/investigating/monitoring/
//      resolved. Choosing one of the middle two filtered the table and left the strip showing
//      no active filter, because no card could be `pressed`.
//   2. The counts were computed from the status-filtered list, so Open read 0 whenever
//      Resolved was selected — a card reporting 0 because of the filter it is offering.
//   3. Status was a request parameter, and useApi refetches only on reload(). With no effect
//      watching it, picking a status changed nothing until the 30-second poll.
//
// All three came from status being filtered on the server while `q` and `severity` were
// filtered in the browser. It is now filtered in the browser with them.
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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
import Security from "./Security";

const CASE_ROWS = [
  { id: "1", ref: "SEC-1", title: "Credential stuffing", status: "open", severity: "sev1", organization_name: "Acme", opened_at: "2026-10-23T09:00:00Z" },
  { id: "2", ref: "SEC-2", title: "Phishing report", status: "investigating", severity: "sev2", organization_name: "Acme", opened_at: "2026-10-23T09:05:00Z" },
  { id: "3", ref: "SEC-3", title: "Suspicious login", status: "investigating", severity: "sev3", organization_name: "Globex", opened_at: "2026-10-23T09:10:00Z" },
  { id: "4", ref: "SEC-4", title: "Rate limit abuse", status: "monitoring", severity: "sev3", organization_name: "Globex", opened_at: "2026-10-23T09:15:00Z" },
  { id: "5", ref: "SEC-5", title: "Resolved incident", status: "resolved", severity: "sev1", organization_name: "Acme", opened_at: "2026-10-23T09:20:00Z" },
];

const ORGS = [{ id: "o1", name: "Acme" }, { id: "o2", name: "Globex" }];

const serve = (rows = CASE_ROWS) =>
  vi.mocked(api.get).mockImplementation((url) =>
    url.includes("/admin/incidents")
      ? Promise.resolve({ data: { items: rows, total: rows.length } })
      : Promise.resolve({ data: { items: ORGS, total: ORGS.length } })
  );

const show = () =>
  render(<ThemeProvider><MemoryRouter><Security /></MemoryRouter></ThemeProvider>);

const card = (label) => screen.getByText(label, { selector: "p" }).closest("button");
const statusSelect = () => screen.getByLabelText("Filter by status");
const refs = () => screen.getAllByText(/^SEC-\d$/).map((n) => n.textContent).sort();
const incidentCalls = () => vi.mocked(api.get).mock.calls.filter(([u]) => u.includes("/admin/incidents"));

beforeEach(async () => {
  vi.clearAllMocks();
  serve();
});

const ready = () => screen.findByText("Credential stuffing");

describe("every canonical status is represented", () => {
  it("gives Investigating and Monitoring cards of their own", async () => {
    show();
    await ready();
    for (const label of ["Open", "Investigating", "Monitoring", "Resolved"]) {
      expect(card(label)).toBeInTheDocument();
    }
  });

  it("counts each status over the whole fetched set, not the filtered one", async () => {
    show();
    await ready();
    expect(within(card("Open")).getByText("1")).toBeInTheDocument();
    expect(within(card("Investigating")).getByText("2")).toBeInTheDocument();
    expect(within(card("Monitoring")).getByText("1")).toBeInTheDocument();
    expect(within(card("Resolved")).getByText("1")).toBeInTheDocument();
  });

  it("keeps the counts steady while a status filter is applied", async () => {
    // The regression: Open used to read 0 whenever Resolved was selected, because the counts
    // were taken from the list the server had already narrowed.
    show();
    await ready();
    fireEvent.click(card("Resolved"));
    await waitFor(() => expect(card("Resolved")).toHaveAttribute("aria-pressed", "true"));
    expect(within(card("Open")).getByText("1")).toBeInTheDocument();
    expect(within(card("Investigating")).getByText("2")).toBeInTheDocument();
  });
});

describe("the cards and the dropdown are one state", () => {
  it.each([
    ["Open", "open", ["SEC-1"]],
    ["Investigating", "investigating", ["SEC-2", "SEC-3"]],
    ["Monitoring", "monitoring", ["SEC-4"]],
    ["Resolved", "resolved", ["SEC-5"]],
  ])("clicking %s filters the table and presses that card", async (label, value, expected) => {
    show();
    await ready();
    fireEvent.click(card(label));
    await waitFor(() => expect(refs()).toEqual(expected));
    expect(card(label)).toHaveAttribute("aria-pressed", "true");
    expect(statusSelect()).toHaveValue(value);
  });

  it.each([
    ["investigating", "Investigating", ["SEC-2", "SEC-3"]],
    ["monitoring", "Monitoring", ["SEC-4"]],
  ])("choosing %s in the dropdown presses its card — the reported bug", async (value, label, expected) => {
    show();
    await ready();
    fireEvent.change(statusSelect(), { target: { value } });
    await waitFor(() => expect(refs()).toEqual(expected));
    expect(card(label)).toHaveAttribute("aria-pressed", "true");
  });

  it("never presses Open just because the status is not Resolved", async () => {
    show();
    await ready();
    for (const value of ["investigating", "monitoring", "resolved"]) {
      fireEvent.change(statusSelect(), { target: { value } });
      await waitFor(() => expect(statusSelect()).toHaveValue(value));
      expect(card("Open")).toHaveAttribute("aria-pressed", "false");
    }
  });

  it("presses exactly one status card at a time", async () => {
    show();
    await ready();
    fireEvent.change(statusSelect(), { target: { value: "monitoring" } });
    await waitFor(() => expect(card("Monitoring")).toHaveAttribute("aria-pressed", "true"));
    const pressed = ["Open", "Investigating", "Monitoring", "Resolved"]
      .filter((l) => card(l).getAttribute("aria-pressed") === "true");
    expect(pressed).toEqual(["Monitoring"]);
  });

  it("switching status moves the indicator rather than adding one", async () => {
    show();
    await ready();
    fireEvent.click(card("Open"));
    await waitFor(() => expect(card("Open")).toHaveAttribute("aria-pressed", "true"));
    fireEvent.click(card("Investigating"));
    await waitFor(() => expect(card("Investigating")).toHaveAttribute("aria-pressed", "true"));
    expect(card("Open")).toHaveAttribute("aria-pressed", "false");
  });
});

describe("clearing", () => {
  it("pressing the active card again clears the filter and the indicator", async () => {
    show();
    await ready();
    fireEvent.click(card("Open"));
    await waitFor(() => expect(refs()).toEqual(["SEC-1"]));
    fireEvent.click(card("Open"));
    await waitFor(() => expect(refs()).toHaveLength(CASE_ROWS.length));
    expect(card("Open")).toHaveAttribute("aria-pressed", "false");
    expect(statusSelect()).toHaveValue("all");
  });

  it("shows no active indicator when nothing is filtered", async () => {
    show();
    await ready();
    for (const l of ["Open", "Investigating", "Monitoring", "Resolved"]) {
      expect(card(l)).toHaveAttribute("aria-pressed", "false");
    }
  });
});

describe("the other filters are independent", () => {
  it("status and search narrow together, and neither clears the other", async () => {
    show();
    await ready();
    fireEvent.change(screen.getByLabelText(/search/i), { target: { value: "login" } });
    await waitFor(() => expect(refs()).toEqual(["SEC-3"]));
    fireEvent.click(card("Investigating"));
    await waitFor(() => expect(card("Investigating")).toHaveAttribute("aria-pressed", "true"));
    expect(refs()).toEqual(["SEC-3"]);                               // both still applied
    expect(screen.getByLabelText(/search/i)).toHaveValue("login");   // search survived
  });

  it("status and severity narrow together", async () => {
    show();
    await ready();
    fireEvent.change(screen.getByLabelText("Filter by severity"), { target: { value: "sev3" } });
    fireEvent.change(statusSelect(), { target: { value: "investigating" } });
    await waitFor(() => expect(refs()).toEqual(["SEC-3"]));           // sev3 AND investigating
    expect(screen.getByLabelText("Filter by severity")).toHaveValue("sev3");
  });

  it("the Critical card reports severity and does not touch the status filter", async () => {
    show();
    await ready();
    // Sev1/Sev2 that are not resolved: SEC-1 and SEC-2. SEC-5 is sev1 but resolved.
    const critical = screen.getByText("Critical (Sev1/Sev2)").closest("div, button");
    expect(within(critical).getByText("2")).toBeInTheDocument();
    // It is not a filter control, so it cannot overwrite the status selection.
    expect(screen.queryByRole("button", { name: /^Critical/ })).toBeNull();
  });
});

describe("the request", () => {
  it("does not refetch when only the status changes", async () => {
    // Status is filtered in the browser now, so switching it costs nothing. It also used to
    // mean the table did not move until the 30s poll, because nothing triggered a reload.
    show();
    await ready();
    const before = incidentCalls().length;
    fireEvent.change(statusSelect(), { target: { value: "investigating" } });
    await waitFor(() => expect(refs()).toEqual(["SEC-2", "SEC-3"]));
    expect(incidentCalls().length).toBe(before);
  });

  it("never sends a status parameter", async () => {
    show();
    await ready();
    fireEvent.change(statusSelect(), { target: { value: "monitoring" } });
    await waitFor(() => expect(refs()).toEqual(["SEC-4"]));
    for (const [, cfg] of incidentCalls()) {
      expect(cfg?.params ?? {}).not.toHaveProperty("status");
    }
  });

  it("asks the organizations endpoint for a page size it will accept", async () => {
    // 200 was past the endpoint's le=100, so it 422'd and the organization filter was empty.
    show();
    await ready();
    const orgCall = vi.mocked(api.get).mock.calls.find(([u]) => u.includes("/admin/organizations"));
    expect(orgCall?.[1]?.params?.page_size).toBeLessThanOrEqual(100);
  });
});

describe("accessibility", () => {
  it("status cards are real buttons carrying their own pressed state", async () => {
    show();
    await ready();
    for (const l of ["Open", "Investigating", "Monitoring", "Resolved"]) {
      expect(card(l).tagName).toBe("BUTTON");
      expect(card(l)).toHaveAttribute("aria-pressed");
    }
  });

  it("states the active filter in words, not colour alone", async () => {
    show();
    await ready();
    fireEvent.click(card("Monitoring"));
    await waitFor(() => expect(card("Monitoring")).toHaveAttribute("aria-pressed", "true"));
    expect(within(card("Monitoring")).getByText(/Filtering/i)).toBeInTheDocument();
  });
});

describe("unexpected data", () => {
  it("ignores a status outside the vocabulary rather than miscounting it", async () => {
    serve([...CASE_ROWS, { id: "9", ref: "SEC-9", title: "Odd one", status: "archived", severity: "sev4", organization_name: "Acme", opened_at: "2026-10-23T09:30:00Z" }]);
    show();
    await ready();
    // It is not folded into any status card...
    expect(within(card("Open")).getByText("1")).toBeInTheDocument();
    expect(within(card("Resolved")).getByText("1")).toBeInTheDocument();
    // ...and it is still listed when no status filter is applied.
    expect(refs()).toContain("SEC-9");
  });
});
