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
  const ev = (id, start, end, status = "scheduled") => ({ id, status, start_time: start, end_time: end });

  it("does not mark same-day events whose actual scheduled intervals do not intersect", () => {
    expect(overlapping([
      ev("a", "2026-10-06T11:40:00+05:30", "2026-10-06T11:42:00+05:30"),
      ev("b", "2026-10-06T11:43:00+05:30", "2026-10-06T12:00:00+05:30"),
      ev("c", "2026-10-06T12:33:00+05:30", "2026-10-06T13:00:00+05:30"),
    ])).toEqual(new Set());
  });

  it("marks partially overlapping and fully contained events", () => {
    const clash = overlapping([
      ev("a", "2026-11-02T10:00:00Z", "2026-11-02T11:00:00Z"),
      ev("b", "2026-11-02T10:30:00Z", "2026-11-02T12:00:00Z"),
      ev("c", "2026-11-02T10:15:00Z", "2026-11-02T10:45:00Z"),
      ev("d", "2026-11-02T12:00:00Z", "2026-11-02T13:00:00Z"),
    ]);
    expect([...clash].sort()).toEqual(["a", "b", "c"]);
  });

  it("uses half-open intervals so events meeting at an endpoint do not overlap", () => {
    expect(overlapping([
      ev("a", "2026-11-02T10:00:00Z", "2026-11-02T11:00:00Z"),
      ev("b", "2026-11-02T11:00:00Z", "2026-11-02T12:00:00Z"),
    ])).toEqual(new Set());
  });

  it("detects overlap across midnight using absolute timestamps", () => {
    expect(overlapping([
      ev("a", "2026-11-02T23:30:00-05:00", "2026-11-03T00:30:00-05:00"),
      ev("b", "2026-11-03T00:00:00-05:00", "2026-11-03T01:00:00-05:00"),
    ])).toEqual(new Set(["a", "b"]));
  });

  it("normalizes explicit timezone offsets before comparing", () => {
    expect(overlapping([
      ev("a", "2026-11-02T10:00:00+05:30", "2026-11-02T11:00:00+05:30"),
      ev("b", "2026-11-02T05:00:00Z", "2026-11-02T06:00:00Z"),
    ])).toEqual(new Set(["a", "b"]));
  });

  it("does not invent durations for missing or invalid timestamps", () => {
    expect(overlapping([
      ev("missing-end", "2026-11-02T10:00:00Z", null),
      ev("invalid-end", "2026-11-02T10:00:00Z", "not-a-time"),
      ev("end-before-start", "2026-11-02T10:00:00Z", "2026-11-02T09:00:00Z"),
      ev("missing-start", null, "2026-11-02T11:00:00Z"),
      ev("valid", "2026-11-02T10:30:00Z", "2026-11-02T11:30:00Z"),
    ])).toEqual(new Set());
  });

  it("excludes cancelled and archived events from schedule conflicts", () => {
    expect(overlapping([
      ev("cancelled", "2026-11-02T10:00:00Z", "2026-11-02T12:00:00Z", "cancelled"),
      ev("archived", "2026-11-02T10:00:00Z", "2026-11-02T12:00:00Z", "archived"),
      ev("scheduled", "2026-11-02T10:30:00Z", "2026-11-02T11:30:00Z"),
    ])).toEqual(new Set());
  });

  it("counts distinct involved events rather than overlapping pairs", () => {
    const clash = overlapping([
      ev("a", "2026-11-02T10:00:00Z", "2026-11-02T11:00:00Z"),
      ev("b", "2026-11-02T10:30:00Z", "2026-11-02T10:45:00Z"),
      ev("c", "2026-11-02T10:50:00Z", "2026-11-02T11:30:00Z"),
      ev("d", "2026-11-02T12:00:00Z", "2026-11-02T13:00:00Z"),
    ]);
    expect(clash.size).toBe(3);
    expect([...clash].sort()).toEqual(["a", "b", "c"]);
  });
});
