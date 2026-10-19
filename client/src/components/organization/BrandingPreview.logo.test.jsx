// The logo inside the Branding Preview header.
//
// ── THE BUG ─────────────────────────────────────────────────────────────────────────────
// Two faults, both visible only once you knew the asset's shape.
//
// 1. WRONG BOX SHAPE. The logo sat in a 48x48 SQUARE (`h-12 w-12`) with overflow-hidden.
//    /zoiko-logo.png is the 1336x471 WORDMARK, so `object-contain` fitted it to the 48px
//    WIDTH and left it about 17px tall — the lettering collapsed into an unreadable smear.
//    Nothing was stretched; it was scaled into a box the wrong shape for it. The viewer
//    header it previews does the opposite: fixes HEIGHT and lets width follow
//    (EventWatch.jsx passes OrgLogo `height="h-6 sm:h-7"`).
//
// 2. CHIP INVERTED IN DARK MODE. ui/Logo.jsx states the rule — the wordmark's lettering is
//    dark navy, so it ships on a LIGHT chip in BOTH themes rather than as a second
//    white-text asset. The preview's square was `bg-white dark:bg-slate-800`, putting a
//    dark-navy wordmark on a dark background. It also passed its own raw <img> as the
//    fallback, which bypassed <Logo> and so had no chip at all.
//
// The fix is to render it the way the viewer does and let OrgLogo fall through to <Logo>.
// No filter, invert or opacity anywhere: a brand's colours are the brand.
import { render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

vi.mock("../../theme/ThemeContext", async (importOriginal) => {
  const real = await importOriginal();
  return { ...real, useTheme: () => ({ theme: "dark" }) };
});

import BrandingPreview from "./BrandingPreview";

const open = (props = {}) =>
  render(
    <BrandingPreview
      open
      onClose={() => {}}
      accent="violet"
      logoUrl={null}
      logoUrlDark={null}
      orgName="Acme Live"
      {...props}
    />
  );

const stage = () => screen.getByTestId("branding-preview-stage");
/** The logo image, whichever asset the chain resolved to. */
const logo = () => within(stage()).getByRole("img");

describe("the default ZoikoStream branding", () => {
  it("renders the wordmark asset the viewer header also uses", () => {
    open();
    expect(logo()).toHaveAttribute("src", "/zoiko-logo.png");
  });

  it("is not squeezed into a square box", () => {
    // The regression: a 1336x471 wordmark constrained to 48px WIDE is ~17px tall.
    open();
    const cls = logo().className;
    expect(cls).toMatch(/\bw-auto\b/);
    expect(cls).not.toMatch(/\bw-12\b/);
  });

  it("is constrained by HEIGHT, the way the viewer constrains it", () => {
    open();
    expect(logo().className).toMatch(/\bh-\d/);
  });

  it("preserves its aspect ratio", () => {
    open();
    expect(logo().className).toMatch(/object-contain/);
  });

  it("keeps the chip LIGHT in dark mode, not just in light mode", () => {
    // ui/Logo.jsx's rule: dark-navy lettering ships on a light chip in BOTH themes rather
    // than as a second white-text asset. The old square was `bg-white dark:bg-slate-800`,
    // so the chip went dark exactly where the wordmark needed it light — asserting only
    // that `bg-white` appears would have passed on the broken markup too.
    open();                                   // theme is pinned to dark above
    const chip = logo().parentElement;
    expect(chip.className).toMatch(/bg-white/);
    expect(chip.className).not.toMatch(/dark:bg-(slate|neutral|zinc|gray|black)/);
  });

  it("applies no filter, invert or opacity to the artwork", () => {
    open();
    const cls = `${logo().className} ${logo().parentElement.className}`;
    for (const forbidden of ["invert", "grayscale", "brightness-", "contrast-", "opacity-"]) {
      expect(cls).not.toMatch(new RegExp(forbidden));
    }
  });

  it("names the brand for assistive technology", () => {
    open();
    expect(logo()).toHaveAccessibleName("ZoikoStream");
  });
});

describe("a custom organization logo", () => {
  const CUSTOM = "https://cdn.acme.com/mark.svg";

  it("is used instead of the ZoikoStream default", () => {
    open({ logoUrl: CUSTOM });
    expect(logo()).toHaveAttribute("src", CUSTOM);
  });

  it("prefers the dark-theme asset when one is configured", () => {
    // The theme is pinned to dark above. A brand that ships a dark variant gets it —
    // which is the supported answer to a dark surface, not a CSS filter.
    open({ logoUrl: CUSTOM, logoUrlDark: "https://cdn.acme.com/mark-dark.svg" });
    expect(logo()).toHaveAttribute("src", "https://cdn.acme.com/mark-dark.svg");
  });

  it("gets the same aspect-preserving treatment as the default", () => {
    open({ logoUrl: CUSTOM });
    const cls = logo().className;
    expect(cls).toMatch(/\bw-auto\b/);
    expect(cls).toMatch(/object-contain/);
  });

  it("is bounded so a very wide logo cannot push the header apart", () => {
    // Boundedness is the behaviour; HOW it is bounded is the component's business. It is a
    // fixed-width box that clips, plus a logo that cannot exceed it — asserting one specific
    // utility class pinned an implementation detail instead.
    open({ logoUrl: CUSTOM });
    expect(logo().className).toMatch(/max-w-full/);
    const box = screen.getByTestId("branding-preview-logo");
    expect(box.className).toMatch(/overflow-hidden/);
    expect(box.className).toMatch("w-28");
  });

  it("is named by the organization for assistive technology", () => {
    open({ logoUrl: CUSTOM });
    expect(logo()).toHaveAccessibleName("Acme Live logo");
  });

  it("carries no filter either", () => {
    open({ logoUrl: CUSTOM });
    expect(logo().className).not.toMatch(/invert|grayscale|brightness-|contrast-/);
  });
});

describe("the preview is only a preview", () => {
  it("renders without being handed any save or mutation callback", () => {
    // It takes `onClose` and presentational values only — there is no path from opening it
    // to persisting branding.
    expect(() => open()).not.toThrow();
    expect(stage()).toBeInTheDocument();
  });
});
