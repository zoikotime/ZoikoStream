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

describe("overlapping detection", () => {
  const ev = (id, start, end, extra = {}) => ({
    id,
    start_time: start,
    end_time: end,
    status: "scheduled",
    ...extra,
  });

  it("1: Three events at distinct times on the same day produce no overlaps (October 6 reproduction)", () => {
    // October 6 reproduction: 11:40 AM (10m), 11:55 AM (10m), and 12:33 PM (15m)
    const distinctEvents = [
      ev("e1", "2026-10-06T11:40:00Z", "2026-10-06T11:50:00Z"),
      ev("e2", "2026-10-06T11:55:00Z", "2026-10-06T12:05:00Z"),
      ev("e3", "2026-10-06T12:33:00Z", "2026-10-06T12:48:00Z"),
    ];
    expect(overlapping(distinctEvents).size).toBe(0);

    // Three events at 11:40, 11:43, and 12:33 with 2-minute durations
    const shortEvents = [
      ev("e1", "2026-10-06T11:40:00Z", "2026-10-06T11:42:00Z"),
      ev("e2", "2026-10-06T11:43:00Z", "2026-10-06T11:45:00Z"),
      ev("e3", "2026-10-06T12:33:00Z", "2026-10-06T12:35:00Z"),
    ];
    expect(overlapping(shortEvents).size).toBe(0);
  });

  it("2: Two events with intersecting intervals detect overlap on both events", () => {
    const clash = overlapping([
      ev("a", "2026-11-02T10:00:00Z", "2026-11-02T11:00:00Z"),
      ev("b", "2026-11-02T10:30:00Z", "2026-11-02T12:00:00Z"),
      ev("c", "2026-11-02T13:00:00Z", "2026-11-02T14:00:00Z"),
    ]);
    expect([...clash].sort()).toEqual(["a", "b"]);
    expect(clash.size).toBe(2);
  });

  it("3: Events whose intervals touch exactly at the boundary are non-overlapping", () => {
    const boundaryEvents = [
      ev("a", "2026-11-02T10:00:00Z", "2026-11-02T11:00:00Z"),
      ev("b", "2026-11-02T11:00:00Z", "2026-11-02T12:00:00Z"),
    ];
    expect(overlapping(boundaryEvents).size).toBe(0);
  });

  it("4: Nested events detect overlap correctly", () => {
    const nested = [
      ev("outer", "2026-11-02T10:00:00Z", "2026-11-02T12:00:00Z"),
      ev("inner", "2026-11-02T10:30:00Z", "2026-11-02T11:30:00Z"),
    ];
    expect([...overlapping(nested)].sort()).toEqual(["inner", "outer"]);
  });

  it("5: Multiple independent overlap pairs match the documented unique event metric", () => {
    const multiple = [
      // Pair 1
      ev("p1a", "2026-11-02T09:00:00Z", "2026-11-02T10:00:00Z"),
      ev("p1b", "2026-11-02T09:30:00Z", "2026-11-02T10:30:00Z"),
      // Separate non-overlapping event
      ev("mid", "2026-11-02T11:00:00Z", "2026-11-02T12:00:00Z"),
      // Pair 2
      ev("p2a", "2026-11-02T14:00:00Z", "2026-11-02T15:00:00Z"),
      ev("p2b", "2026-11-02T14:15:00Z", "2026-11-02T14:45:00Z"),
    ];
    const clash = overlapping(multiple);
    expect([...clash].sort()).toEqual(["p1a", "p1b", "p2a", "p2b"]);
    expect(clash.size).toBe(4);
  });

  it("6: Events across midnight or different timezones compare UTC instants properly", () => {
    // Event A: 23:30Z to 00:30Z next day
    // Event B: starts 19:45-04:00 (EDT), which is 23:45Z, ends 20:45-04:00 (00:45Z next day)
    const crossTz = [
      ev("london", "2026-10-06T23:30:00Z", "2026-10-07T00:30:00Z"),
      ev("newyork", "2026-10-06T19:45:00-04:00", "2026-10-06T20:45:00-04:00"),
    ];
    expect(overlapping(crossTz).size).toBe(2);

    // Non-overlapping across timezones
    const crossTzNonOverlap = [
      ev("london", "2026-10-06T20:00:00Z", "2026-10-06T21:00:00Z"),
      ev("kolkata", "2026-10-07T03:00:00+05:30", "2026-10-07T04:00:00+05:30"), // 21:30Z to 22:30Z
    ];
    expect(overlapping(crossTzNonOverlap).size).toBe(0);
  });

  it("7: Missing end times and duration handling does not invent arbitrary durations", () => {
    // Missing end_time and missing duration_minutes -> does not invent 1-hour duration
    expect(overlapping([
      ev("a", "2026-11-02T10:00:00Z", null),
      ev("b", "2026-11-02T10:30:00Z", null),
    ]).size).toBe(0);

    // Explicit duration_minutes is respected
    expect(overlapping([
      ev("a", "2026-11-02T10:00:00Z", null, { duration_minutes: 45 }),
      ev("b", "2026-11-02T10:30:00Z", null, { duration_minutes: 30 }),
    ]).size).toBe(2);

    // Non-overlapping with duration_minutes
    expect(overlapping([
      ev("a", "2026-11-02T10:00:00Z", null, { duration_minutes: 20 }),
      ev("b", "2026-11-02T10:30:00Z", null, { duration_minutes: 20 }),
    ]).size).toBe(0);

    // End time at or before start time is invalid interval -> ignored
    expect(overlapping([
      ev("a", "2026-11-02T10:00:00Z", "2026-11-02T09:30:00Z"),
      ev("b", "2026-11-02T09:45:00Z", "2026-11-02T10:15:00Z"),
    ]).size).toBe(0);
  });

  it("8: Cancelled, archived, and deleted events do not participate in collision detection", () => {
    const withCancelled = [
      ev("active", "2026-11-02T10:00:00Z", "2026-11-02T11:00:00Z", { status: "scheduled" }),
      ev("cancelled", "2026-11-02T10:30:00Z", "2026-11-02T11:30:00Z", { status: "cancelled" }),
      ev("archived", "2026-11-02T10:15:00Z", "2026-11-02T10:45:00Z", { status: "archived" }),
      ev("deleted", "2026-11-02T10:20:00Z", "2026-11-02T10:50:00Z", { deleted_at: "2026-11-01T00:00:00Z" }),
    ];
    expect(overlapping(withCancelled).size).toBe(0);
  });

  it("9: Duplicate records do not collide with themselves", () => {
    const withDuplicates = [
      ev("dup", "2026-11-02T10:00:00Z", "2026-11-02T11:00:00Z"),
      ev("dup", "2026-11-02T10:00:00Z", "2026-11-02T11:00:00Z"),
    ];
    expect(overlapping(withDuplicates).size).toBe(0);
  });

  it("ignores events with no start time or invalid input", () => {
    expect(overlapping([ev("a", null, null), ev("b", null, null)]).size).toBe(0);
    expect(overlapping(null).size).toBe(0);
    expect(overlapping(undefined).size).toBe(0);
    expect(overlapping("not-an-array").size).toBe(0);
  });
});
