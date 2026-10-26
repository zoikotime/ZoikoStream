// The browser has to be told which scheme it is painting for.
//
// A native control's popup is drawn by the browser, not by this stylesheet: a <select>'s
// option list, a date/datetime picker's calendar, scrollbars, autofill. No class and no
// utility reaches any of them. `color-scheme` is the only control there is, and it was never
// declared — so the console could put itself in dark mode while every native popup stayed on
// the light scheme. Clicking "All organizations" on Identity & Access opened a large white
// option list over a black page.
//
// Measured in Chromium against the built stylesheet, for every combination of OS preference
// and in-app theme:
//
//   OS=light app=dark   root=normal -> dark    option text black -> white    (the reported bug)
//   OS=dark  app=light  root=normal -> light                                 (the inverse, also wrong)
//
// This is asserted against the SOURCE rather than against a rendered page because jsdom does
// not resolve color-scheme from a stylesheet, and because the rule's absence — not its
// computed value — is what the bug was.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const css = readFileSync(resolve(process.cwd(), "src/index.css"), "utf8");

// Comments are prose — and this file's own comments name the selectors being asserted on —
// so they are stripped before anything is matched. Whitespace is collapsed so formatting
// cannot change a result.
const flat = css.replace(/\/\*[\s\S]*?\*\//g, " ").replace(/\s+/g, " ");

describe("native control chrome follows the app theme", () => {
  it("declares a scheme at the root, so nothing is left to the browser default", () => {
    expect(flat).toMatch(/:root\s*\{[^}]*color-scheme:\s*light/);
  });

  it("switches the scheme with the theme class the toggle actually writes", () => {
    // theme/ThemeContext does `documentElement.classList.toggle("dark", ...)`, so the
    // selector has to be on the root element itself — `.dark &` would never match it.
    expect(flat).toMatch(/:root\.dark\s*\{[^}]*color-scheme:\s*dark/);
  });

  it("sets it on the root rather than on individual controls", () => {
    // 24 native selects and 46 date/time inputs in the admin console alone — per-control
    // declarations would be 70 places to forget.
    const perControl = flat.match(/(select|input)[^{]*\{[^}]*color-scheme/g) || [];
    expect(perControl).toHaveLength(0);
  });

  it("covers both directions of mismatch, not just dark", () => {
    // A dark-OS operator who chooses the light theme must get light native chrome, which is
    // only true because the light rule is declared explicitly too.
    expect(flat).toMatch(/color-scheme:\s*light/);
    expect(flat).toMatch(/color-scheme:\s*dark/);
  });

  it("puts the dark rule after the base one, so it wins at equal specificity", () => {
    const light = css.indexOf("color-scheme: light");
    const dark = css.indexOf("color-scheme: dark");
    expect(light).toBeGreaterThan(-1);
    expect(dark).toBeGreaterThan(light);
  });
});
