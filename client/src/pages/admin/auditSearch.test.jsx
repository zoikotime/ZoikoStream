// Audit Logs search, against the actor label the table actually shows.
//
// An audit row stores `actor_email = actor.email if actor else None` (crud/admin.py), and the
// API carries nothing else about the actor — AuditLogOut has `actor_email` and no actor type
// or name. So "system" is this console's label for the ABSENCE of an actor, not a value in
// the database.
//
// The Actor column rendered that label. The search did not:
//
//     render: (a) => a.actor_email || "system"      // the column
//     (a.actor_email || "").toLowerCase()           // the filter
//
// On a system row those are "system" and "". Searching the word on screen compared it against
// an empty string and matched nothing, on every system-generated entry, while the table went
// on displaying "system" in the column beside the search box.
//
// Both now read one helper, the way targetLabel was already used by both.
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
import AuditLogs from "./AuditLogs";

const now = () => new Date().toISOString();

// Two system rows (no actor_email at all) and two human ones.
const ROWS = [
  { id: "1", actor_email: null, action: "subscription.checkout_started", target_type: "organization", target_id: "11111111-aaaa", meta: { name: "Acme" }, ip: "10.0.0.1", created_at: now() },
  { id: "2", actor_email: "", action: "live.broadcast.host", target_type: "event", target_id: "22222222-bbbb", meta: null, ip: "10.0.0.2", created_at: now() },
  { id: "3", actor_email: "radha@zoikogroup.com", action: "organization.delete", target_type: "organization", target_id: "33333333-cccc", meta: { name: "Globex" }, ip: "10.0.0.3", created_at: now() },
  { id: "4", actor_email: "naveen@zoikogroup.com", action: "user.update", target_type: "user", target_id: "44444444-dddd", meta: { email: "someone@tenant.test" }, ip: "10.0.0.4", created_at: now() },
];

const serve = (rows = ROWS) =>
  vi.mocked(api.get).mockResolvedValue({ data: { items: rows, total: rows.length } });

const show = () =>
  render(<ThemeProvider><MemoryRouter><AuditLogs /></MemoryRouter></ThemeProvider>);

const searchBox = () => screen.getByPlaceholderText(/Search by actor, action, or target/i);
const search = (text) => fireEvent.change(searchBox(), { target: { value: text } });

// Which rows are on screen, identified by their action (unique per row here). Scoped to the
// table body because the action filter's <option> list is built from these same rows, so a
// bare text query would match the dropdown as well as the table.
const bodyText = () => {
  const body = document.querySelector("table tbody");
  return body ? [...body.querySelectorAll("tr")].map((tr) => tr.textContent || "") : [];
};
const visible = () =>
  ROWS.filter((r) => bodyText().some((t) => t.includes(r.action))).map((r) => r.id).sort();

const ready = () => waitFor(() => expect(bodyText().length).toBeGreaterThan(0));

beforeEach(() => {
  vi.clearAllMocks();
  serve();
});

describe("searching for a system actor", () => {
  it("finds system entries by the word the table shows — the reported bug", async () => {
    // Fails against the original: the filter read actor_email, which is null on these rows,
    // so "system" matched nothing while the Actor column displayed exactly that word.
    show();
    await ready();
    search("system");
    await waitFor(() => expect(visible()).toEqual(["1", "2"]));
  });

  it("matches on a prefix of it", async () => {
    show();
    await ready();
    search("sys");
    await waitFor(() => expect(visible()).toEqual(["1", "2"]));
  });

  it("is case-insensitive", async () => {
    show();
    await ready();
    search("SYSTEM");
    await waitFor(() => expect(visible()).toEqual(["1", "2"]));
    search("SyStEm");
    await waitFor(() => expect(visible()).toEqual(["1", "2"]));
  });

  it("ignores surrounding whitespace", async () => {
    show();
    await ready();
    search("   system   ");
    await waitFor(() => expect(visible()).toEqual(["1", "2"]));
  });

  it("treats an empty actor_email the same as a null one", async () => {
    // Row 2 carries "" rather than null; both mean "no actor" and both read as system.
    show();
    await ready();
    search("system");
    await waitFor(() => expect(bodyText().some((t) => t.includes("live.broadcast.host"))).toBe(true));
  });

  it("shows the same label in the Actor column that the search matched", async () => {
    show();
    await ready();
    search("system");
    await waitFor(() => expect(visible()).toEqual(["1", "2"]));
    const row = bodyText().find((t) => t.includes("subscription.checkout_started"));
    expect(row).toContain("system");
  });
});

describe("the searches that already worked still do", () => {
  it("finds a human actor by email", async () => {
    show();
    await ready();
    search("radha");
    await waitFor(() => expect(visible()).toEqual(["3"]));
  });

  it("finds an action identifier", async () => {
    show();
    await ready();
    search("subscription.checkout_started");
    await waitFor(() => expect(visible()).toEqual(["1"]));
  });

  it("finds a target by its meta label", async () => {
    show();
    await ready();
    search("Globex");
    await waitFor(() => expect(visible()).toEqual(["3"]));
  });

  it("finds a target by its id fragment", async () => {
    show();
    await ready();
    search("22222222");
    await waitFor(() => expect(visible()).toEqual(["2"]));
  });

  it("finds target types even when a metadata label is present", async () => {
    show();
    await ready();
    search("organization");
    await waitFor(() => expect(visible()).toEqual(["1", "3"]));
  });

  it("searches the full target identifier, not only the displayed prefix", async () => {
    show();
    await ready();
    search("33333333-cccc");
    await waitFor(() => expect(visible()).toEqual(["3"]));
  });

  it("matches action and actor email without regard to case", async () => {
    show();
    await ready();
    search("RADHA@ZOIKOGROUP.COM");
    await waitFor(() => expect(visible()).toEqual(["3"]));
    search("LIVE.BROADCAST.HOST");
    await waitFor(() => expect(visible()).toEqual(["2"]));
  });

  it("does not sweep every row in just because some are system", async () => {
    // The lazy fix — treating a null actor as matching anything — would show all four.
    show();
    await ready();
    search("system");
    await waitFor(() => expect(visible()).not.toContain("3"));
    expect(visible()).not.toContain("4");
  });
});

describe("clearing and combining", () => {
  it("restores every row when the search is emptied", async () => {
    show();
    await ready();
    search("system");
    await waitFor(() => expect(visible()).toEqual(["1", "2"]));
    search("");
    await waitFor(() => expect(visible()).toEqual(["1", "2", "3", "4"]));
  });

  it("narrows with the action filter rather than replacing it", async () => {
    show();
    await ready();
    fireEvent.change(screen.getByLabelText(/action/i), { target: { value: "live.broadcast.host" } });
    await waitFor(() => expect(visible()).toEqual(["2"]));
    search("system");                                   // still a system row, still matches
    await waitFor(() => expect(visible()).toEqual(["2"]));
    search("radha");                                    // human actor, excluded by the action filter
    await waitFor(() => expect(visible()).toEqual([]));
  });

  it("says there are no matches only when there genuinely are none", async () => {
    show();
    await ready();
    search("system");
    await waitFor(() => expect(visible()).toEqual(["1", "2"]));
    expect(screen.queryByText(/No audit entries match your filters/i)).toBeNull();

    search("zzz-nothing-matches-this");
    expect(await screen.findByText(/No audit entries match your filters/i)).toBeInTheDocument();
  });
});

describe("rows with missing fields", () => {
  it("does not throw when optional fields are absent", async () => {
    serve([{ id: "9", actor_email: null, action: "system.sweep", target_type: null, target_id: null, meta: null, ip: null, created_at: null }]);
    show();
    await ready();
    search("system");
    await waitFor(() => expect(bodyText().some((t) => t.includes("system.sweep"))).toBe(true));
  });

  it("keeps such a row searchable by its display identity", async () => {
    serve([{ id: "9", actor_email: null, action: "cleanup.run", target_type: null, target_id: null, meta: null, ip: null, created_at: null }]);
    show();
    await ready();
    search("sys");
    await waitFor(() => expect(bodyText().some((t) => t.includes("cleanup.run"))).toBe(true));
  });
});
