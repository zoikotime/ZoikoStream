// The session-aware analytics panel: additional to the existing analytics, and honest about
// what was not measured (ZST-SPEC-VAP-001 §9; server/app/services/viewing_sessions.py).
import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import ViewingSessionsPanel from "./ViewingSessionsPanel";

const SUMMARY = {
  measured: true, events_measured: 2, sessions_admitted: 41, peak_concurrent_sessions: 17,
  rejoin_rate: 0.125, qoe_sessions_reporting: 30,
  join_time_distribution: [
    { bucket: "before_start", sessions: 6 }, { bucket: "0-5m", sessions: 20 }, { bucket: "5-15m", sessions: 9 },
    { bucket: "15-30m", sessions: 4 }, { bucket: "30-60m", sessions: 2 }, { bucket: "60m+", sessions: 0 },
  ],
};
const REPORTS = [
  { id: "a", event: "Memorial service", sessions: {
    measured: true, sessions_admitted: 30, peak_concurrent_sessions: 17, median_watch_seconds: 2730,
    rejoin_rate: 0.1, qoe: { startup_ms_median: 1500, failed_sessions: 2 } } },
  { id: "b", event: "Wedding", sessions: {
    measured: true, sessions_admitted: 11, peak_concurrent_sessions: 9, median_watch_seconds: 95,
    rejoin_rate: null, qoe: { startup_ms_median: null, failed_sessions: null } } },
  { id: "c", event: "Nobody came", sessions: { measured: false } },
];

describe("ViewingSessionsPanel", () => {
  it("shows the range totals and each measured event", () => {
    render(<ViewingSessionsPanel summary={SUMMARY} reports={REPORTS} />);
    expect(screen.getByText("Sessions admitted").nextSibling).toHaveTextContent("41");
    expect(screen.getByText("Peak concurrent sessions").nextSibling).toHaveTextContent("17");
    expect(screen.getByText("Rejoin rate", { selector: "p" }).nextSibling).toHaveTextContent("12.5%");
    const rows = within(screen.getByRole("table")).getAllByRole("row");
    expect(rows).toHaveLength(3);                                          // header + two measured
    expect(rows[1]).toHaveTextContent("Memorial service");
    expect(rows[1]).toHaveTextContent("45m 30s");
    expect(rows[1]).toHaveTextContent("1.5 s");
    expect(screen.queryByText("Nobody came")).toBeNull();
  });

  it("renders what was not measured as a dash, never as zero", () => {
    render(<ViewingSessionsPanel summary={SUMMARY} reports={REPORTS} />);
    const wedding = within(screen.getByRole("table")).getAllByRole("row")[2];
    const cells = within(wedding).getAllByRole("cell").map((c) => c.textContent);
    expect(cells).toEqual(["Wedding", "11", "9", "1m 35s", "—", "—", "—"]);
  });

  it("says so when nothing was recorded, rather than showing zeros", () => {
    render(<ViewingSessionsPanel summary={{ measured: false, sessions_admitted: null }} reports={[]} />);
    expect(screen.getByText("No viewing sessions recorded in this window yet.")).toBeInTheDocument();
    expect(screen.queryByText("0")).toBeNull();
  });

  it("labels sessions as sessions, not people", () => {
    render(<ViewingSessionsPanel summary={SUMMARY} reports={REPORTS} />);
    expect(screen.getByText(/not unique people/)).toBeInTheDocument();
  });
});
