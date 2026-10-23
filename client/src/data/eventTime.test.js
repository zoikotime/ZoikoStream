import { describe, expect, it } from "vitest";
import { ageOf, fmtSeconds, fmtUtc, fmtZoned, safeZone, secondsSince, zonedParts } from "./eventTime";

describe("event-local and UTC time", () => {
  const iso = "2026-10-24T17:00:00Z";

  it("formats the same instant in the event's zone and in UTC", () => {
    expect(fmtZoned(iso, "Europe/London")).toMatch(/Oct 24, 2026 · 6:00 PM (BST|GMT\+1)/);
    expect(fmtUtc(iso)).toBe("Oct 24 · 17:00 UTC");
  });

  it("never throws on a malformed stored zone", () => {
    expect(safeZone("Not/AZone")).toBeUndefined();
    expect(() => fmtZoned(iso, "Not/AZone")).not.toThrow();
    expect(fmtZoned(null, "Europe/London")).toBe("—");
  });

  it("gives the wall-clock fields of an instant in a zone", () => {
    expect(zonedParts(iso, "Europe/London")).toEqual({ date: "2026-10-24", time: "18:00" });
    expect(zonedParts(iso, "America/New_York")).toEqual({ date: "2026-10-24", time: "13:00" });
    expect(zonedParts(null, "UTC")).toEqual({ date: "", time: "" });
  });
});

describe("freshness", () => {
  const now = Date.parse("2026-10-24T17:10:00Z");

  it("says how old a reading is", () => {
    expect(ageOf("2026-10-24T17:09:58Z", now)).toBe("just now");
    expect(ageOf("2026-10-24T17:09:20Z", now)).toBe("40s ago");
    expect(ageOf("2026-10-24T17:00:00Z", now)).toBe("10 min ago");
    expect(ageOf(null, now)).toBeNull();
  });

  it("treats a missing timestamp as infinitely old, so it can never read as current", () => {
    expect(secondsSince(null, now)).toBe(Infinity);
  });

  it("formats a duration", () => {
    expect(fmtSeconds(3600)).toBe("1h 00m");
    expect(fmtSeconds(900)).toBe("15m");
    expect(fmtSeconds(45)).toBe("45s");
    expect(fmtSeconds(null)).toBe("—");
  });
});
