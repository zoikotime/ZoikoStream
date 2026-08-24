import { describe, expect, it } from "vitest";

import { money } from "./money";

// Guards the defect this module exists to prevent: a missing currency being rendered as USD.
// The super-admin commerce console had its own copy of this formatter with `currency = "USD"`,
// so a GBP order was shown to staff in dollars.
describe("money", () => {
  it("formats USD", () => {
    expect(money("1440.00", "USD")).toBe("$1,440.00");
  });

  it("formats EUR", () => {
    expect(money("1440.00", "EUR")).toBe("€1,440.00");
  });

  it("formats GBP", () => {
    expect(money("1440.00", "GBP")).toBe("£1,440.00");
  });

  it("never renders a non-USD amount with a dollar sign", () => {
    for (const code of ["EUR", "GBP", "JPY", "INR"]) {
      expect(money("1440.00", code)).not.toContain("$");
    }
  });

  it("does not default a missing currency to USD", () => {
    for (const missing of [undefined, null, "", "   "]) {
      const out = money("1440.00", missing);
      expect(out).not.toContain("$");
      expect(out).not.toContain("USD");
      expect(out).toBe("1,440.00");
    }
  });

  it("shows an unrecognised but well-formed code instead of guessing a symbol", () => {
    // Intl accepts any three-letter code and renders the code itself rather than throwing.
    // Asserted by content, not by literal: Intl separates code and number with a NON-BREAKING
    // space (U+00A0), so an exact string with a normal space silently never matches.
    const out = money("10.00", "XYZ");
    expect(out).toContain("XYZ");
    expect(out).toContain("10.00");
    expect(out).not.toContain("$");
  });

  it("falls back to number-plus-code for a malformed code Intl rejects", () => {
    // Longer than three letters makes Intl throw; the catch path must still not invent a symbol.
    const out = money("10.00", "TOOLONG");
    expect(out).toBe("10.00 TOOLONG");
    expect(out).not.toContain("$");
  });

  it("uppercases and trims a lowercase code rather than treating it as missing", () => {
    expect(money("5.00", " gbp ")).toBe("£5.00");
  });

  it("renders a dash for a missing or unparseable amount", () => {
    expect(money(null, "USD")).toBe("—");
    expect(money(undefined, "USD")).toBe("—");
    expect(money("not-a-number", "USD")).toBe("—");
  });

  it("accepts a number as well as a numeric string", () => {
    expect(money(1440, "USD")).toBe("$1,440.00");
  });

  it("keeps zero-decimal currencies free of invented decimals", () => {
    // JPY has no minor unit; Intl handles it, and we must not override that.
    expect(money("1000", "JPY")).toBe("¥1,000");
  });
});
