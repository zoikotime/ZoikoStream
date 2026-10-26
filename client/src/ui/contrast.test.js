// WCAG 2.2 AA contrast for the console's shared colour tokens, computed from Tailwind's own
// palette (node_modules/tailwindcss/theme.css), not from class names. jsdom paints nothing, so
// this is the CI check; the rendered app was measured in Chrome as well (the audit's
// contrast.cjs / pixelcontrast.cjs).
//
// The arithmetic is what a browser does: oklch → oklab → linear sRGB, clipped to gamut,
// quantised to 8-bit sRGB, then WCAG relative luminance. Gradients are interpolated in oklab
// (Tailwind v4's `bg-gradient-to-*` is `in oklab`) with stops at 0 / 50 / 100%, and the whole
// ramp is sampled — the lightest point under the text decides, not either end.
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { describe, expect, it } from "vitest";
import { CONSOLE, actionStops, actionStopsHover, brand, consoleAction, consoleChip } from "./tokens";

const theme = readFileSync(createRequire(import.meta.url).resolve("tailwindcss/theme.css"), "utf8");

function oklab(name) {
  if (name === "white") return { L: 1, a: 0, b: 0 };
  if (name === "black") return { L: 0, a: 0, b: 0 };
  const m = theme.match(new RegExp(`--color-${name}:\\s*oklch\\(([\\d.]+)%\\s+([\\d.]+)\\s+([\\d.]+|none)\\)`));
  if (!m) throw new Error(`no Tailwind colour ${name}`);
  const h = m[3] === "none" ? 0 : (Number(m[3]) * Math.PI) / 180;
  return { L: Number(m[1]) / 100, a: Number(m[2]) * Math.cos(h), b: Number(m[2]) * Math.sin(h) };
}

// → 8-bit sRGB, as painted.
function rgb({ L, a, b }) {
  const l = (L + 0.3963377774 * a + 0.2158037573 * b) ** 3;
  const m = (L - 0.1055613458 * a - 0.0638541728 * b) ** 3;
  const s = (L - 0.0894841775 * a - 1.291485548 * b) ** 3;
  const lin = [
    4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
    -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
    -0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s,
  ];
  const enc = (x) => (x <= 0.0031308 ? 12.92 * x : 1.055 * x ** (1 / 2.4) - 0.055);
  return lin.map((x) => Math.round(enc(Math.min(1, Math.max(0, x))) * 255));
}

const luminance = (c) => {
  const dec = (v) => ((v /= 255) <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4);
  return 0.2126 * dec(c[0]) + 0.7152 * dec(c[1]) + 0.0722 * dec(c[2]);
};
const ratio = (fg, bg) => {
  const [hi, lo] = [luminance(fg), luminance(bg)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
};
// A translucent fill (Tailwind's `bg-x/NN`) over what is beneath it.
const over = (fill, alpha, under) => fill.map((v, i) => Math.round(v * alpha + under[i] * (1 - alpha)));
const c = (name) => rgb(oklab(name));
const WHITE = [255, 255, 255];
const BLACK = [0, 0, 0];

// "from-x via-y to-z" (optionally hover:-prefixed) → ["x", "y", "z"]
const stopsOf = (classes) =>
  ["from", "via", "to"].map((k) => classes.match(new RegExp(`(?:^|\\s)(?:hover:)?${k}-([a-z]+-\\d+)`))?.[1]).filter(Boolean);

function worstWhiteContrast(stops) {
  const labs = stops.map(oklab);
  const mix = (p, q, t) => ({ L: p.L + (q.L - p.L) * t, a: p.a + (q.a - p.a) * t, b: p.b + (q.b - p.b) * t });
  let worst = Infinity;
  for (let i = 0; i <= 400; i += 1) {
    const t = i / 400;
    const lab = labs.length === 2 ? mix(labs[0], labs[1], t)
      : t <= 0.5 ? mix(labs[0], labs[1], t * 2) : mix(labs[1], labs[2], (t - 0.5) * 2);
    worst = Math.min(worst, ratio(WHITE, rgb(lab)));
  }
  return worst;
}

describe("the console action ramp carries white text at AA everywhere along it", () => {
  it("resting: every point of the gradient is at least 4.5:1 against its white label", () => {
    expect(stopsOf(actionStops)).toEqual(["violet-600", "fuchsia-700", "pink-700"]);
    expect(worstWhiteContrast(stopsOf(actionStops))).toBeGreaterThanOrEqual(4.5);
  });

  it("hover deepens the ramp, so it stays above 4.5:1 too", () => {
    expect(stopsOf(actionStopsHover)).toEqual(["violet-700", "fuchsia-800", "pink-800"]);
    expect(worstWhiteContrast(stopsOf(actionStopsHover))).toBeGreaterThanOrEqual(4.5);
  });

  it("is the same ramp in dark mode: nothing re-tints it lighter there", () => {
    expect(consoleAction).toContain(actionStops);
    expect(consoleAction).toContain(actionStopsHover);
    expect(consoleAction).toMatch(/(^|\s)text-white(\s|$)/);
    expect(consoleAction).not.toMatch(/dark:(hover:)?(from|via|to|bg)-/);
    expect(consoleAction).not.toMatch(/brightness/);
  });

  it("the ramp it replaced really did fail, at its pink end and on hover", () => {
    expect(worstWhiteContrast(["violet-600", "fuchsia-600", "pink-500"])).toBeLessThan(4.5);
    expect(worstWhiteContrast(["violet-500", "fuchsia-500", "pink-400"])).toBeLessThan(4.5);
  });
});

describe("identity chips (the member's avatar, the workspace badge) carry white initials at AA", () => {
  it("every point of the chip ramp is at least 4.5:1, and it is the same ramp in both themes", () => {
    expect(consoleChip).toContain("bg-gradient-to-br");
    expect(consoleChip).toMatch(/(^|\s)text-white(\s|$)/);
    expect(consoleChip).not.toMatch(/dark:/);
    expect(stopsOf(consoleChip)).toEqual(stopsOf(actionStops));
    expect(worstWhiteContrast(stopsOf(consoleChip))).toBeGreaterThanOrEqual(4.5);
  });

  it("what the shell's avatars used before falls short: violet-500 → fuchsia-500, and brand.chip in dark mode", () => {
    expect(worstWhiteContrast(["violet-500", "fuchsia-500"])).toBeLessThan(4.5);
    const dark = ["from", "via", "to"].map((k) => brand.chip.match(new RegExp(`dark:${k}-([a-z]+-\\d+)`))?.[1]);
    expect(dark).toEqual(["blue-500", "violet-500", "fuchsia-500"]);
    expect(worstWhiteContrast(dark)).toBeLessThan(4.5);
  });
});

describe("CONSOLE.faint on the surfaces it is actually used on", () => {
  const light = c(CONSOLE.faint.match(/(?:^|\s)text-([a-z]+-\d+)/)[1]);
  const dark = c(CONSOLE.faint.match(/dark:text-([a-z]+-\d+)/)[1]);

  it.each([
    ["a white card or panel", WHITE],
    ["the slate-50 page", c("slate-50")],
    ["a slate-50/70 table header row on white", over(c("slate-50"), 0.7, WHITE)],
    ["a slate-50/80 inset on white", over(c("slate-50"), 0.8, WHITE)],
  ])("light: at least 4.5:1 on %s", (_, bg) => {
    expect(ratio(light, bg)).toBeGreaterThanOrEqual(4.5);
  });

  it.each([
    ["the black page", BLACK],
    ["a white/5 panel on black", over(WHITE, 0.05, BLACK)],
    ["a white/7 inset on black", over(WHITE, 0.07, BLACK)],
    ["a white/10 hover row on black", over(WHITE, 0.1, BLACK)],
  ])("dark: at least 4.5:1 on %s", (_, bg) => {
    expect(ratio(dark, bg)).toBeGreaterThanOrEqual(4.5);
  });

  // Why a few tinted surfaces take slate-600 themselves instead of the token.
  it.each([
    ["violet-50/70 (selected ledger row)", over(c("violet-50"), 0.7, WHITE)],
    ["rose-50 (live status strip)", c("rose-50")],
  ])("on %s the token is not enough, and slate-600 is", (_, bg) => {
    expect(ratio(light, bg)).toBeLessThan(4.5);
    expect(ratio(c("slate-600"), bg)).toBeGreaterThanOrEqual(4.5);
  });

  it("the unmeasured-figure dash (26px bold, large text) clears 3:1 — and 4.5:1 — on its white tile", () => {
    expect(ratio(light, WHITE)).toBeGreaterThanOrEqual(3);
    expect(ratio(light, WHITE)).toBeGreaterThanOrEqual(4.5);
  });

  it("slate-400, the colour it replaced, fails on white", () => {
    expect(ratio(c("slate-400"), WHITE)).toBeLessThan(4.5);
  });
});
