import { describe, expect, it } from "vitest";
import { dayKey, monthKey, overlapCount, overlapping, overlappingPairs, parseMonth } from "./eventCalendar";

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

  it("1: Same day, non-overlapping events -> 0", () => {
    const sameDay = [
      ev("e1", "2026-10-06T09:00:00Z", "2026-10-06T10:00:00Z"),
      ev("e2", "2026-10-06T10:30:00Z", "2026-10-06T11:30:00Z"),
      ev("e3", "2026-10-06T14:00:00Z", "2026-10-06T15:00:00Z"),
    ];
    expect(overlapCount(sameDay)).toBe(0);
    expect(overlappingPairs(sameDay)).toEqual([]);
    expect(overlapping(sameDay).pairCount).toBe(0);
    expect(overlapping(sameDay).size).toBe(0);
  });

  it("2: October 6 events at 11:40 AM, 11:43 AM, and 12:33 PM with non-intersecting durations -> 0", () => {
    // Exact user reproduction: 11:40 (2 min), 11:43 (2 min), and 12:33 (2 min)
    const oct6Short = [
      ev("e1", "2026-10-06T11:40:00Z", "2026-10-06T11:42:00Z"),
      ev("e2", "2026-10-06T11:43:00Z", "2026-10-06T11:45:00Z"),
      ev("e3", "2026-10-06T12:33:00Z", "2026-10-06T12:35:00Z"),
    ];
    expect(overlapCount(oct6Short)).toBe(0);
    expect(overlappingPairs(oct6Short)).toEqual([]);
    expect(overlapping(oct6Short).pairCount).toBe(0);
    expect(overlapping(oct6Short).size).toBe(0);

    // October 6 events with slightly longer but non-intersecting intervals
    const oct6Spaced = [
      ev("e1", "2026-10-06T11:40:00Z", "2026-10-06T11:42:00Z"),
      ev("e2", "2026-10-06T11:43:00Z", "2026-10-06T12:00:00Z"),
      ev("e3", "2026-10-06T12:33:00Z", "2026-10-06T13:00:00Z"),
    ];
    expect(overlapCount(oct6Spaced)).toBe(0);
    expect(overlapping(oct6Spaced).pairCount).toBe(0);
  });

  it("3: Two events with genuinely intersecting intervals -> 1", () => {
    const clash = overlapping([
      ev("a", "2026-11-02T10:00:00Z", "2026-11-02T11:00:00Z"),
      ev("b", "2026-11-02T10:30:00Z", "2026-11-02T12:00:00Z"),
      ev("c", "2026-11-02T13:00:00Z", "2026-11-02T14:00:00Z"),
    ]);
    expect(clash.pairCount).toBe(1);
    expect(clash.pairs).toEqual([["a", "b"]]);
    expect([...clash].sort()).toEqual(["a", "b"]);
    expect(clash.size).toBe(2);
    expect(overlapCount([
      ev("a", "2026-11-02T10:00:00Z", "2026-11-02T11:00:00Z"),
      ev("b", "2026-11-02T10:30:00Z", "2026-11-02T12:00:00Z"),
    ])).toBe(1);
  });

  it("4: Three events that all overlap each other -> 3 overlapping pairs", () => {
    const threeClash = [
      ev("a", "2026-11-02T10:00:00Z", "2026-11-02T12:00:00Z"),
      ev("b", "2026-11-02T10:30:00Z", "2026-11-02T12:30:00Z"),
      ev("c", "2026-11-02T11:00:00Z", "2026-11-02T13:00:00Z"),
    ];
    expect(overlapCount(threeClash)).toBe(3);
    const clash = overlapping(threeClash);
    expect(clash.pairCount).toBe(3);
    expect(clash.pairs).toEqual([
      ["a", "b"],
      ["a", "c"],
      ["b", "c"],
    ]);
    expect([...clash].sort()).toEqual(["a", "b", "c"]);
    expect(clash.size).toBe(3);
  });

  it("5: Sequential events where one ends exactly when the next starts -> 0", () => {
    const sequential = [
      ev("a", "2026-11-02T10:00:00Z", "2026-11-02T11:00:00Z"),
      ev("b", "2026-11-02T11:00:00Z", "2026-11-02T12:00:00Z"),
      ev("c", "2026-11-02T12:00:00Z", "2026-11-02T13:00:00Z"),
    ];
    expect(overlapCount(sequential)).toBe(0);
    expect(overlapping(sequential).pairCount).toBe(0);
    expect(overlapping(sequential).size).toBe(0);
  });

  it("6: Events on different days -> 0", () => {
    const diffDays = [
      ev("mon", "2026-11-02T10:00:00Z", "2026-11-02T11:00:00Z"),
      ev("tue", "2026-11-03T10:00:00Z", "2026-11-03T11:00:00Z"),
      ev("wed", "2026-11-04T10:00:00Z", "2026-11-04T11:00:00Z"),
    ];
    expect(overlapCount(diffDays)).toBe(0);
    expect(overlapping(diffDays).pairCount).toBe(0);
    expect(overlapping(diffDays).size).toBe(0);
  });

  it("7: An event crossing midnight and another event intersecting that interval -> correctly detected", () => {
    // Event A: 23:30Z (Oct 6) to 01:00Z next day (Oct 7)
    // Event B: 00:15Z (Oct 7) to 01:15Z (Oct 7)
    const midnight = [
      ev("a", "2026-10-06T23:30:00Z", "2026-10-07T01:00:00Z"),
      ev("b", "2026-10-07T00:15:00Z", "2026-10-07T01:15:00Z"),
      ev("c", "2026-10-07T01:30:00Z", "2026-10-07T02:00:00Z"), // non-overlapping
    ];
    expect(overlapCount(midnight)).toBe(1);
    const clash = overlapping(midnight);
    expect(clash.pairCount).toBe(1);
    expect(clash.pairs).toEqual([["a", "b"]]);
    expect([...clash].sort()).toEqual(["a", "b"]);
    expect(clash.has("a")).toBe(true);
    expect(clash.has("b")).toBe(true);
    expect(clash.has("c")).toBe(false);
  });

  it("8: Missing or invalid start/end timestamps -> safely excluded from overlap calculations", () => {
    // Missing start time or non-date string
    expect(overlapCount([ev("a", null, "2026-11-02T11:00:00Z"), ev("b", "bad-date", "2026-11-02T11:00:00Z")])).toBe(0);

    // Missing end time without duration_minutes (does not invent arbitrary durations)
    expect(overlapCount([
      ev("a", "2026-11-02T10:00:00Z", null),
      ev("b", "2026-11-02T10:30:00Z", null),
    ])).toBe(0);

    // End time at or before start time is invalid interval (end <= start) -> ignored
    expect(overlapCount([
      ev("a", "2026-11-02T10:00:00Z", "2026-11-02T09:30:00Z"), // end < start
      ev("b", "2026-11-02T09:45:00Z", "2026-11-02T10:15:00Z"),
    ])).toBe(0);
    expect(overlapCount([
      ev("a", "2026-11-02T10:00:00Z", "2026-11-02T10:00:00Z"), // end === start
      ev("b", "2026-11-02T09:45:00Z", "2026-11-02T10:15:00Z"),
    ])).toBe(0);

    // Cancelled, archived, or deleted events are excluded
    const withCancelled = [
      ev("active", "2026-11-02T10:00:00Z", "2026-11-02T11:00:00Z", { status: "scheduled" }),
      ev("cancelled", "2026-11-02T10:30:00Z", "2026-11-02T11:30:00Z", { status: "cancelled" }),
      ev("archived", "2026-11-02T10:15:00Z", "2026-11-02T10:45:00Z", { status: "archived" }),
      ev("deleted", "2026-11-02T10:20:00Z", "2026-11-02T10:50:00Z", { deleted_at: "2026-11-01T00:00:00Z" }),
    ];
    expect(overlapCount(withCancelled)).toBe(0);

    // Null or non-array inputs
    expect(overlapCount(null)).toBe(0);
    expect(overlapCount(undefined)).toBe(0);
    expect(overlapCount("not-an-array")).toBe(0);
  });

  it("9: Duplicate event records -> no duplicate overlap counts", () => {
    const withDuplicates = [
      ev("dup", "2026-11-02T10:00:00Z", "2026-11-02T11:00:00Z"),
      ev("dup", "2026-11-02T10:00:00Z", "2026-11-02T11:00:00Z"),
    ];
    expect(overlapCount(withDuplicates)).toBe(0);
    expect(overlapping(withDuplicates).pairCount).toBe(0);

    // Duplicate records among real overlaps do not double-count pairs
    const dupWithOverlap = [
      ev("a", "2026-11-02T10:00:00Z", "2026-11-02T11:00:00Z"),
      ev("a", "2026-11-02T10:00:00Z", "2026-11-02T11:00:00Z"),
      ev("b", "2026-11-02T10:30:00Z", "2026-11-02T11:30:00Z"),
      ev("b", "2026-11-02T10:30:00Z", "2026-11-02T11:30:00Z"),
    ];
    expect(overlapCount(dupWithOverlap)).toBe(1);
    expect(overlapping(dupWithOverlap).pairCount).toBe(1);
    expect(overlapping(dupWithOverlap).pairs).toEqual([["a", "b"]]);

    // Duplicate event IDs with conflicting timestamps are deterministically deduplicated (first entry kept)
    const dupConflictingTimestamps = [
      ev("dup", "2026-11-02T10:00:00Z", "2026-11-02T11:00:00Z"),
      ev("dup", "2026-11-02T10:30:00Z", "2026-11-02T11:30:00Z"),
    ];
    expect(overlapCount(dupConflictingTimestamps)).toBe(0);
  });

  it("evaluates real database records from October 6 with genuine overlaps", () => {
    // Actual event records present in zoiko_stream_dev for October 6:
    // Event 1: d0c1d1ff (12:55 to 13:07 IST, 07:25 to 07:37 UTC)
    // Event 2: 9a01b8c6 (12:58 to 13:19 IST, 07:28 to 07:49 UTC) -> overlaps Event 1
    // Event 3: fc0f7c82 (15:56 to 16:08 IST, 10:26 to 10:38 UTC)
    // Event 4: e4ae828d (15:56 to 16:06 IST, 10:26 to 10:36 UTC) -> overlaps Event 3
    const realDbRecords = [
      ev("d0c1d1ff-5951-4934-b32e-2c8dfd9a4e56", "2026-10-06T12:55:00+05:30", "2026-10-06T13:07:00+05:30", { status: "ended" }),
      ev("9a01b8c6-29b6-4221-a4df-9d029ff16efd", "2026-10-06T12:58:00+05:30", "2026-10-06T13:19:00+05:30", { status: "scheduled" }),
      ev("fc0f7c82-39dc-4943-8f70-8afb934e4b05", "2026-10-06T15:56:00+05:30", "2026-10-06T16:08:00+05:30", { status: "scheduled" }),
      ev("e4ae828d-434f-4aff-bd1f-432568f52d14", "2026-10-06T15:56:00+05:30", "2026-10-06T16:06:00+05:30", { status: "draft" }),
    ];
    const clash = overlapping(realDbRecords);
    expect(clash.pairCount).toBe(2);
    expect(clash.pairs).toEqual([
      ["d0c1d1ff-5951-4934-b32e-2c8dfd9a4e56", "9a01b8c6-29b6-4221-a4df-9d029ff16efd"],
      ["e4ae828d-434f-4aff-bd1f-432568f52d14", "fc0f7c82-39dc-4943-8f70-8afb934e4b05"],
    ]);
    expect(clash.size).toBe(4);
  });

  it("10: Timezone conversion -> comparisons remain correct using normalized timestamps", () => {
    // Event A: 23:30Z to 00:30Z next day
    // Event B: starts 19:45-04:00 (EDT = 23:45Z), ends 20:45-04:00 (00:45Z next day)
    const crossTz = [
      ev("london", "2026-10-06T23:30:00Z", "2026-10-07T00:30:00Z"),
      ev("newyork", "2026-10-06T19:45:00-04:00", "2026-10-06T20:45:00-04:00"),
    ];
    expect(overlapCount(crossTz)).toBe(1);
    expect(overlapping(crossTz).pairCount).toBe(1);
    expect(overlapping(crossTz).size).toBe(2);

    // Non-overlapping across timezones
    const crossTzNonOverlap = [
      ev("london", "2026-10-06T20:00:00Z", "2026-10-06T21:00:00Z"),
      ev("kolkata", "2026-10-07T03:00:00+05:30", "2026-10-07T04:00:00+05:30"), // 21:30Z to 22:30Z
    ];
    expect(overlapCount(crossTzNonOverlap)).toBe(0);
    expect(overlapping(crossTzNonOverlap).pairCount).toBe(0);
  });



  describe("main branch compatibility tests", () => {
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
        ev("cancelled", "2026-11-02T10:00:00Z", "2026-11-02T12:00:00Z", { status: "cancelled" }),
        ev("archived", "2026-11-02T10:00:00Z", "2026-11-02T12:00:00Z", { status: "archived" }),
        ev("scheduled", "2026-11-02T10:30:00Z", "2026-11-02T11:30:00Z"),
      ])).toEqual(new Set());
    });
  });
});
