import { describe, expect, it } from "vitest";
import { dayKey, monthKey, overlapping, parseMonth } from "./eventCalendar";

describe("parseMonth", () => {
  it("reads YYYY-MM and falls back to this month for anything else", () => {
    expect(monthKey(parseMonth("2026-11"))).toBe("2026-11");
    const now = new Date(2026, 9, 21);
    for (const bad of [null, "", "2026-13", "11-2026", "2026-1"]) {
      expect(monthKey(parseMonth(bad, now))).toBe("2026-10");
    }
  });

  it("keys a day by its local calendar date", () => {
    expect(dayKey(new Date(2026, 0, 5))).toBe("2026-01-05");
  });
});

describe("overlapping", () => {
  const ev = (id, start, end) => ({ id, start_time: start, end_time: end });

  it("marks both events of an overlapping pair, and neither of back-to-back events", () => {
    const clash = overlapping([
      ev("a", "2026-11-02T10:00:00Z", "2026-11-02T11:00:00Z"),
      ev("b", "2026-11-02T10:30:00Z", "2026-11-02T12:00:00Z"),
      ev("c", "2026-11-02T12:00:00Z", "2026-11-02T13:00:00Z"),
    ]);
    expect([...clash].sort()).toEqual(["a", "b"]);
  });

  it("takes an event without an end as one hour long", () => {
    expect(overlapping([ev("a", "2026-11-02T10:00:00Z", null), ev("b", "2026-11-02T10:59:00Z", null)]).size).toBe(2);
    expect(overlapping([ev("a", "2026-11-02T10:00:00Z", null), ev("b", "2026-11-02T11:00:00Z", null)]).size).toBe(0);
  });

  it("ignores events with no start time", () => {
    expect(overlapping([ev("a", null, null), ev("b", null, null)]).size).toBe(0);
  });
});
