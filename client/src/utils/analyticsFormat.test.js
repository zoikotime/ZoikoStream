// Measured zero must not look like never-measured.
//
// The analytics cards rendered `0` for both "we sampled and the answer was zero" and "no
// AnalyticsSnapshot rows exist, so nothing was ever measured". Watch Time also rounded any
// session under three minutes to "0.0 hrs", which made a real short stream indistinguishable
// from missing telemetry. The service now returns null for the second case; these pin that
// the client renders the two differently and never converts null into a figure.
import { describe, expect, it } from "vitest";

import { formatEngagement, formatWatchTime } from "./analyticsFormat";

const EM_DASH = "—";

describe("watch time", () => {
  it("renders an em dash when nothing was measured", () => {
    expect(formatWatchTime(null)).toBe(EM_DASH);
    expect(formatWatchTime(undefined)).toBe(EM_DASH);
  });

  it("renders a measured zero as zero, not as unavailable", () => {
    // Sampled, and nobody was watching. A real fact, and a different one.
    expect(formatWatchTime(0)).toBe("0 min");
  });

  it("renders a very short session in minutes rather than 0.0 hrs", () => {
    // 2 minutes. The old `${v} hrs` with one decimal printed "0.0 hrs".
    expect(formatWatchTime(2 / 60)).toBe("2 min");
  });

  it("names anything under a minute without claiming precision the sampler lacks", () => {
    expect(formatWatchTime(30 / 3600)).toBe("< 1 min");
  });

  it("switches to hours at an hour", () => {
    expect(formatWatchTime(1)).toBe("1.0 hrs");
    expect(formatWatchTime(2.5)).toBe("2.5 hrs");
  });

  it("rounds minutes rather than truncating", () => {
    expect(formatWatchTime(59.6 / 60)).toBe("60 min");
  });
});

describe("engagement score", () => {
  it("renders an em dash when nothing was measured", () => {
    expect(formatEngagement(null)).toBe(EM_DASH);
    expect(formatEngagement(undefined)).toBe(EM_DASH);
  });

  it("renders a measured zero as 0%", () => {
    // Snapshots exist and showed no interaction — a measurement, so it gets a number.
    expect(formatEngagement(0)).toBe("0%");
  });

  it("renders a real score", () => {
    expect(formatEngagement(42)).toBe("42%");
  });
});

describe("null never becomes a number", () => {
  it("no falsy-coercion path turns unavailable into zero", () => {
    // `value || 0` and `value ?? 0` are the two ways this regresses; both would print a
    // figure for a metric that was never measured.
    for (const fn of [formatWatchTime, formatEngagement]) {
      expect(fn(null)).toBe(EM_DASH);
      expect(fn(null)).not.toBe("0");
      expect(fn(null)).not.toBe("0%");
      expect(fn(null)).not.toBe("0.0 hrs");
    }
  });
});
