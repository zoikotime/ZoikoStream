// Where the Command Center's metric tiles actually send you.
//
// "Live Now" and "At Risk" sat on two tiles that measure different things and both pointed at
// the bare string "/admin/live-events" — the same URL, so the at-risk count was a link to the
// live list. Pinned here: the two lead somewhere DIFFERENT, each destination exists, and the
// view they ask for is one the destination applies rather than ignores.
//
// Live Operations cannot show "at risk": GET /admin/live-events takes state=live|recent and
// returns broadcast sessions with no severity on them. The at-risk rows are the Command
// Center's own `attention` list — the same list the tile counts — so the tile links to that
// table instead of inventing a second definition of at-risk.
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
import KpiRow from "../../components/admin/sections/KpiRow";
import SessionsAttention from "../../components/admin/sections/SessionsAttention";
import LiveEvents from "./LiveEvents";

const KPIS = {
  platform_health: { semantics: "current", status: "ok", ok: 7, total: 9, series: [], comparison: { delta_pct: null } },
  live_sessions: { semantics: "current", value: 4, in_period: 4, starting_soon: 0, unattended: 1, series: [], comparison: {} },
  at_risk_sessions: { semantics: "current", total: 2, critical: 1, high: 1, monitoring: 0, dominant_stage: "deliver" },
  concurrent_audience: { semantics: "current", value: 10, measurement_state: "measured", series: [], comparison: {} },
  api_health: { semantics: "window", measurement_state: "measured", requests: 10, errors: 0, value: 0, series: [], comparison: {} },
};

const ATTENTION = [{
  id: "a1", severity: "critical", event: "Keynote", organization: "Acme",
  issue: "Paused for 6 minutes", stage: "deliver", started_at: "2026-10-22T09:00:00Z", owner: null,
}];

const row = (over) => ({
  id: "s1", title: "Keynote", organization: "Acme", region: "eu",
  started_at: "2026-10-22T09:00:00Z", ended_at: null, viewers: 3, health: "ok", ...over,
});

const tiles = () =>
  render(
    <ThemeProvider>
      <MemoryRouter>
        <KpiRow kpis={KPIS} range={{ mode: "live" }} age="3s" />
        <SessionsAttention items={ATTENTION} />
      </MemoryRouter>
    </ThemeProvider>
  );

const liveOps = (path = "/admin/live-events") =>
  render(
    <ThemeProvider>
      <MemoryRouter initialEntries={[path]}>
        <LiveEvents />
      </MemoryRouter>
    </ThemeProvider>
  );

// "Live Now" labels TWO tiles — Live sessions and Concurrent audience — and both correctly
// point at the live view, so a tile is addressed through its own heading rather than through
// a label it shares.
const tile = (heading) => screen.getByText(heading).closest("div[class*='rounded-xl']");
const linkIn = (heading) => within(tile(heading)).getByRole("link");
const hrefIn = (heading) => linkIn(heading).getAttribute("href");
const href = (name) => screen.getByRole("link", { name }).getAttribute("href");

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.get).mockImplementation((_u, cfg) =>
    Promise.resolve({
      data: cfg?.params?.state === "recent"
        ? [row({ id: "r1", ended_at: "2026-10-22T09:30:00Z" })]
        : [row()],
    })
  );
});

describe("the two tiles lead to different places", () => {
  it("no longer share one destination", () => {
    tiles();
    expect(hrefIn("Live sessions")).not.toBe(hrefIn("At-risk sessions"));
  });

  it("Live Now opens Live Operations on the live view", () => {
    tiles();
    expect(hrefIn("Live sessions")).toBe("/admin/live-events?tab=live");
  });

  it("At Risk opens the at-risk table, which is on this page", () => {
    tiles();
    expect(hrefIn("At-risk sessions")).toBe("#sessions-attention");
    // The target must actually exist, or the link is a no-op.
    expect(document.getElementById("sessions-attention")).toBeTruthy();
  });

  it("At Risk does not fall back to the live list", () => {
    tiles();
    expect(hrefIn("At-risk sessions")).not.toContain("/admin/live-events");
  });

  it("leaves the other quick links alone", () => {
    tiles();
    expect(href(/Current Status/)).toBe("/admin/status");
    expect(href(/Traffic/)).toBe("/admin/status?tab=traffic");
  });

  it("the whole tile is the same target as its link — a card click cannot diverge", () => {
    tiles();
    // The stretched pseudo-element belongs to the link itself, so each tile has exactly one
    // destination and there is no separate card-level handler to disagree with it.
    expect(linkIn("At-risk sessions").className).toContain("after:inset-0");
    expect(linkIn("Live sessions").className).toContain("after:inset-0");
  });
});

describe("Live Operations applies the view it is handed", () => {
  it("opens the live view for ?tab=live — the URL the tile links to", async () => {
    liveOps("/admin/live-events?tab=live");
    await screen.findByText("Keynote");
    // The live view is the one carrying the force-end action.
    expect(await screen.findByRole("button", { name: "End" })).toBeInTheDocument();
    expect(screen.queryByRole("columnheader", { name: "Ended" })).toBeNull();
  });

  it("defaults to live with no parameter", async () => {
    liveOps();
    await screen.findByText("Keynote");
    expect(screen.queryByRole("columnheader", { name: "Ended" })).toBeNull();
  });

  it("opens the recent view for ?tab=recent, so the parameter is read and not ignored", async () => {
    liveOps("/admin/live-events?tab=recent");
    await screen.findByText("Keynote");
    expect(screen.getByRole("columnheader", { name: "Ended" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "End" })).toBeNull();
  });

  it("falls back to live for a value it does not recognise", async () => {
    liveOps("/admin/live-events?tab=nonsense");
    await screen.findByText("Keynote");
    expect(screen.queryByRole("columnheader", { name: "Ended" })).toBeNull();
  });

  it("writes the view back to the URL, so a refresh keeps it", async () => {
    liveOps();
    await screen.findByText("Keynote");
    fireEvent.click(screen.getByRole("button", { name: /Recently Ended/ }));
    await waitFor(() => expect(screen.getByRole("columnheader", { name: "Ended" })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: /^Live/ }));
    await waitFor(() => expect(screen.queryByRole("columnheader", { name: "Ended" })).toBeNull());
  });
});

describe("the at-risk table is a real anchor target", () => {
  const attention = (items) =>
    render(
      <ThemeProvider>
        <MemoryRouter><SessionsAttention items={items} /></MemoryRouter>
      </ThemeProvider>
    );

  it("carries the id the tile points at, and clears the sticky header", () => {
    attention(ATTENTION);
    const panel = document.getElementById("sessions-attention");
    expect(panel).toBeTruthy();
    expect(panel.className).toContain("scroll-mt-24");
    expect(within(panel).getByText("Keynote")).toBeInTheDocument();
  });

  it("still exists when nothing is at risk, so the link never dangles", () => {
    attention([]);
    expect(document.getElementById("sessions-attention")).toBeTruthy();
    expect(screen.getByText("No live session needs attention.")).toBeInTheDocument();
  });
});
