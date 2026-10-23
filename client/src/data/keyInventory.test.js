// keyInventory: the Developers page's API-credentials figures with or without the server's
// `key_summary` (see DeveloperPlatform.test.jsx for the tile itself).
import { describe, expect, it } from "vitest";
import { keyInventory } from "./keyInventory";

describe("keyInventory", () => {
  const NOW = Date.parse("2026-10-23T12:00:00Z");

  it("prefers the server's own counts", () => {
    const summary = { total: 5, active: 3, expiring_30d: 1 };
    expect(keyInventory({ key_summary: summary, api_keys: [] }, NOW)).toBe(summary);
  });

  it("derives total and active from the list: revoked, or at/after expires_at, is not active", () => {
    const keys = [
      { revoked: false, expires_at: null },
      { revoked: true, expires_at: null },
      { revoked: false, expires_at: "2026-10-23T12:00:00Z" },     // expires exactly now
      { revoked: false, expires_at: "2026-10-24T00:00:00Z" },
    ];
    expect(keyInventory({ api_keys: keys }, NOW)).toEqual({ total: 4, active: 2 });
  });

  it("trusts a per-key `usable` from the server over its own reading", () => {
    const keys = [{ revoked: false, expires_at: null, usable: false }, { revoked: true, usable: true }];
    expect(keyInventory({ api_keys: keys }, NOW)).toEqual({ total: 2, active: 1 });
  });

  it("is null — unreadable, not zero — when there is no key list", () => {
    expect(keyInventory({ webhooks: [] }, NOW)).toBeNull();
    expect(keyInventory(null, NOW)).toBeNull();
  });
});
