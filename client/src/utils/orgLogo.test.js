// The logo rules every surface shares: which URL a theme gets, and which URLs are acceptable.
// The validation cases are the server's (test_org_branding_logos.py) so the two cannot drift.
import { describe, expect, it } from "vitest";
import { logoCandidates, logoUrlError } from "./orgLogo";

const LIGHT = "https://cdn.acme.com/logo.png";
const DARK = "https://cdn.acme.com/logo-dark.svg";

describe("logoCandidates", () => {
  it("light theme: the light logo only — a dark asset is never put on a light background", () => {
    expect(logoCandidates({ light: LIGHT, dark: DARK }, "light")).toEqual([LIGHT]);
    expect(logoCandidates({ dark: DARK }, "light")).toEqual([]);
  });

  it("dark theme: the dark logo, then the light one", () => {
    expect(logoCandidates({ light: LIGHT, dark: DARK }, "dark")).toEqual([DARK, LIGHT]);
  });

  it("dark theme with no dark logo falls back to the light one", () => {
    expect(logoCandidates({ light: LIGHT, dark: null }, "dark")).toEqual([LIGHT]);
    expect(logoCandidates({ light: LIGHT, dark: "   " }, "dark")).toEqual([LIGHT]);
  });

  it("nothing configured means the ZoikoStream mark (an empty list)", () => {
    expect(logoCandidates({}, "dark")).toEqual([]);
    expect(logoCandidates({ light: "", dark: "" }, "light")).toEqual([]);
    expect(logoCandidates(undefined, "light")).toEqual([]);
  });

  it("the same URL in both fields is tried once", () => {
    expect(logoCandidates({ light: LIGHT, dark: LIGHT }, "dark")).toEqual([LIGHT]);
  });
});

describe("logoUrlError mirrors the server's rules", () => {
  it.each([
    "http://cdn.acme.com/logo-dark.png",
    "javascript:alert(1)",
    "cdn.acme.com/logo.png",
    "https://",
    "https:/cdn.acme.com/logo.png",
    "https://cdn.acme.com/my logo.png",
    "https://cdn.acme.com/brand-guide.pdf",
    "https://cdn.acme.com/index.html",
    "https://cdn.acme.com/" + "a".repeat(500) + ".png",
  ])("refuses %s", (bad) => {
    expect(logoUrlError(bad)).toEqual(expect.any(String));
  });

  it.each([
    "https://cdn.acme.com/logo.svg",
    "https://cdn.acme.com/logo.PNG",
    "https://cdn.acme.com/logo.webp?v=3",
    "https://images.acme-cdn.net/brand/7f3a9c",
    "HTTPS://cdn.acme.com/logo.jpg",
    "  https://cdn.acme.com/logo.gif  ",
    "",
    "   ",
  ])("accepts %j", (good) => {
    expect(logoUrlError(good)).toBeNull();
  });

  it("says what is wrong in words a reader can act on", () => {
    expect(logoUrlError("http://cdn.acme.com/l.png")).toBe("Use an https:// link to the image.");
    expect(logoUrlError("https://cdn.acme.com/l.pdf")).toBe("Link to an image file: PNG, SVG, JPG, WebP, GIF or AVIF.");
  });
});
