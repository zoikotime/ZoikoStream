// The Producer Console's watch durations (Stats → Avg watch, Median watch).
//
// Production report: MEDIAN WATCH read "1m 30.099999999999994s". The server sends the median
// rounded to 0.1s (services/viewing_sessions.py); the console split the unrounded value into
// minutes and `s % 60`, which printed the float's binary residue. It now rounds first.
import { describe, expect, it } from "vitest";
import { fmtWatchDuration } from "./host";

describe("fmtWatchDuration", () => {
  it("prints the reported median as whole seconds", () => {
    expect(fmtWatchDuration(90.1)).toBe("1m 30s");
  });

  it.each([
    [0, "0s"], [0.4, "0s"], [12.6, "13s"], [59.6, "1m 0s"], [60, "1m 0s"],
    [89.5, "1m 30s"], [3599.4, "59m 59s"], [3600, "1h 0m"], [3725.2, "1h 2m"],
  ])("%s seconds reads %s", (s, text) => {
    expect(fmtWatchDuration(s)).toBe(text);
  });

  it("never prints a fractional or exponent part", () => {
    for (const s of [0.1, 1.05, 30.099999999999994, 90.30000000000001, 1234.5678]) {
      expect(fmtWatchDuration(s)).toMatch(/^(\d+h \d+m|\d+m \d+s|\d+s)$/);
    }
  });

  it("shows an unmeasured duration as unavailable, not as zero", () => {
    expect(fmtWatchDuration(null)).toBe("—");
    expect(fmtWatchDuration(undefined)).toBe("—");
    expect(fmtWatchDuration(Number.NaN)).toBe("—");
  });
});
