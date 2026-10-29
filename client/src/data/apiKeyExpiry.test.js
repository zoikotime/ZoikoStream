// What the Developer page says about a key's lifetime (data/apiKeyExpiry.js). The server owns
// the state; these pin the words and the boundaries, including the fallback for a payload
// without server fields, which used Math.ceil and read a key expired hours ago as active.
import { describe, expect, it } from "vitest";
import {
  badgeFor, exactExpiry, expiryBanner, expiryDate, expiryText, expiryTooltip, lifecycleOf, summaryOf,
} from "./apiKeyExpiry";

const NOW = Date.parse("2026-10-16T12:00:00Z");
const DAY = 86_400;
const at = (seconds) => new Date(NOW + seconds * 1000).toISOString();
const server = (status, seconds, warning = null, usable = status !== "expired" && status !== "revoked") =>
  ({ status, usable, seconds_until_expiry: seconds, expiry_warning: warning, expires_at: seconds == null ? null : at(seconds) });

describe("lifecycleOf", () => {
  it("takes the server's verdict as given", () => {
    expect(lifecycleOf(server("expiring_soon", 7 * DAY, "urgent"), NOW))
      .toEqual({ status: "expiring_soon", usable: true, seconds: 7 * DAY, warning: "urgent" });
    expect(lifecycleOf(server("expired", -21 * DAY), NOW).usable).toBe(false);
  });

  it("uses the absolute expiry instant to advance a response snapshot", () => {
    const key = {
      ...server("active", 30, null),
      expires_at: at(30),
    };
    const now = NOW + 60_000;
    const life = lifecycleOf(key, now, NOW);
    expect(life).toEqual({ status: "expired", usable: false, seconds: -30, warning: null });
    expect(expiryText(life, key.expires_at, now)).toBe("Expired less than an hour ago");
    expect(badgeFor(life)).toEqual({ tone: "danger", label: "Expired" });
    expect(expiryDate(key.expires_at, { timeZone: "UTC", locale: "en-US" })).toBe("Oct 16, 2026");
  });

  it("recalculates the warning boundary from the timestamp, not stale countdown fields", () => {
    const key = { ...server("active", 60 * DAY), expires_at: at(20 * DAY) };
    expect(lifecycleOf(key, NOW, NOW)).toMatchObject({
      status: "expiring_soon", usable: true, seconds: 20 * DAY, warning: "notice",
    });
  });

  it.each([
    [30 * DAY + 1, "active", null],
    [30 * DAY, "expiring_soon", "notice"],
    [7 * DAY, "expiring_soon", "urgent"],
    [DAY, "expiring_soon", "imminent"],
    [4 * 3600, "expiring_soon", "imminent"],
    [0, "expired", null],                       // the exact instant is already expired
    [-5 * 3600, "expired", null],               // was "Expires in 0d" and counted Active
  ])("without server fields: %i s left -> %s", (seconds, status, warning) => {
    const life = lifecycleOf({ expires_at: at(seconds), revoked: false }, NOW);
    expect(life.status).toBe(status);
    expect(life.warning).toBe(warning);
    expect(life.usable).toBe(status !== "expired");
  });

  it("without server fields: revoked outranks everything; no expiry is active", () => {
    expect(lifecycleOf({ expires_at: at(60 * DAY), revoked: true }, NOW).status).toBe("revoked");
    expect(lifecycleOf({ expires_at: null, revoked: false }, NOW)).toMatchObject({ status: "active", seconds: null });
  });
});

describe("expiryText", () => {
  const text = (seconds) => expiryText({ seconds }, seconds == null ? null : at(seconds), NOW);
  it.each([
    [27 * DAY, "Expires in 27 days"],
    [7 * DAY, "Expires in 7 days"],
    [7 * DAY - 1800, "Expires in 7 days"],      // an hourly sweep's view of "7 days"
    [4 * 3600, "Expires in 4 hours"],
    [3600, "Expires in 1 hour"],
    [59 * 60, "Expires in less than an hour"],
    [-21 * DAY, "Expired 21 days ago"],
    [-3 * 3600, "Expired 3 hours ago"],
    [-60, "Expired less than an hour ago"],
    [null, "Never expires"],
  ])("%s s -> %s", (seconds, expected) => {
    expect(text(seconds)).toBe(expected);
  });

  it("gives a revoked key no countdown", () => {
    expect(expiryText({ status: "revoked", seconds: 40 * DAY }, at(40 * DAY), NOW)).toBe("No longer usable");
  });

  it("distinguishes non-expiring, revoked, and expired keys without an expiry timestamp", () => {
    expect(expiryText({ status: "active", seconds: null }, null, NOW)).toBe("Never expires");
    expect(expiryText({ status: "revoked", seconds: null }, null, NOW)).toBe("No longer usable");
    expect(expiryText({ status: "expired", seconds: null }, null, NOW)).toBe("Expired (date unavailable)");
  });

  it("says tomorrow when the expiry falls on the next local calendar day", () => {
    const now = new Date(2026, 9, 16, 9, 0).getTime();             // local 09:00
    const tomorrow = new Date(2026, 9, 17, 18, 0).getTime();       // local 18:00 next day
    expect(expiryText({ seconds: (tomorrow - now) / 1000 }, new Date(tomorrow).toISOString(), now))
      .toBe("Expires tomorrow");
  });

  it("handles UTC instants across midnight without changing the displayed UTC date", () => {
    const expiresAt = "2026-10-17T00:15:00Z";
    const now = Date.parse("2026-10-16T23:45:00Z");
    const life = lifecycleOf({
      status: "expiring_soon", usable: true, seconds_until_expiry: 1800,
      expiry_warning: "imminent", expires_at: expiresAt,
    }, now, now);
    expect(expiryDate(expiresAt, { timeZone: "UTC", locale: "en-US" })).toBe("Oct 17, 2026");
    expect(expiryText(life, expiresAt, now)).toBe("Expires in less than an hour");
    expect(badgeFor(life).label).toBe("Expires within the hour");
  });

  it("keeps day pluralization and whole-day boundaries exact", () => {
    expect(expiryText({ seconds: -DAY }, at(-DAY), NOW)).toBe("Expired 1 day ago");
    expect(expiryText({ seconds: -2 * DAY }, at(-2 * DAY), NOW)).toBe("Expired 2 days ago");
    expect(expiryText({ seconds: DAY }, at(DAY), NOW)).toBe("Expires tomorrow");
  });
});

describe("exactExpiry", () => {
  it("names the zone it is shown in", () => {
    expect(exactExpiry("2026-09-25T10:00:00Z", { timeZone: "UTC", locale: "en-US" })).toBe("Sep 25, 2026, 10:00 AM UTC");
    expect(exactExpiry(null)).toBeNull();
    expect(expiryDate("2026-09-25T10:00:00Z", { timeZone: "UTC", locale: "en-US" })).toBe("Sep 25, 2026");
    expect(expiryTooltip("2026-09-25T10:00:00Z")).toMatch(/· 2026-09-25T10:00:00Z \(UTC\)$/);
  });
});

describe("badgeFor", () => {
  it("never says Active for a key that cannot be used", () => {
    expect(badgeFor({ status: "expired" })).toEqual({ tone: "danger", label: "Expired" });
    expect(badgeFor({ status: "revoked" })).toEqual({ tone: "neutral", label: "Revoked" });
  });

  it("strengthens as expiry approaches", () => {
    expect(badgeFor({ status: "active" })).toEqual({ tone: "success", label: "Active" });
    expect(badgeFor({ status: "expiring_soon", warning: "notice", seconds: 20 * DAY }))
      .toEqual({ tone: "warning", label: "Expiring soon" });
    expect(badgeFor({ status: "expiring_soon", warning: "urgent", seconds: 5 * DAY }))
      .toEqual({ tone: "warning", label: "Expires in 5 days" });
    expect(badgeFor({ status: "expiring_soon", warning: "imminent", seconds: 4 * 3600 }))
      .toEqual({ tone: "danger", label: "Expires in 4 hours" });
  });
});

describe("summaryOf and expiryBanner", () => {
  it("prefers the server's counts, else derives them by the same rule", () => {
    const given = { total: 9, active: 1, expiring_30d: 0, expiring_7d: 0, expired: 0, revoked: 0, non_expiring: 1 };
    expect(summaryOf([], given, [])).toBe(given);
    const lives = [
      { status: "expiring_soon", usable: true, seconds: 5 * DAY, warning: "urgent" },
      { status: "expired", usable: false, seconds: -DAY, warning: null },
      { status: "active", usable: true, seconds: null, warning: null },
    ];
    expect(summaryOf([1, 2, 3], null, lives)).toEqual({
      total: 3, active: 2, expiring_30d: 1, expiring_7d: 1, expired: 1, revoked: 0, non_expiring: 1,
    });
  });

  it("shows a notice only while a usable key is inside its last 30 days", () => {
    expect(expiryBanner({ expiring_30d: 0, expiring_7d: 0 })).toBeNull();
    expect(expiryBanner({ expiring_30d: 1, expiring_7d: 1 }).text)
      .toBe("1 API key expires within 7 days. Create a replacement key and update your integration before it expires.");
    expect(expiryBanner({ expiring_30d: 2, expiring_7d: 0 }).text)
      .toBe("2 API keys expire within 30 days. Create a replacement key and update your integration before they expire.");
  });
});
