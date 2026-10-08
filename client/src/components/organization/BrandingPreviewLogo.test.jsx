// The logo in Settings -> Branding -> Preview.
//
// The defect: in dark mode the preview drew the ZoikoStream wordmark (1336x471, 2.84:1) inside a
// 48px square tile at 46x16 px — letters ~5px tall — and, through its own `fallback` <img>, drew
// it BARE on a slate tile, where the wordmark's navy "ZOIKO" lettering disappeared. The viewer
// page meanwhile showed the same fallback on ui/Logo's light chip, so the two disagreed.
//
// The preview now renders through ui/OrgLogo exactly as the viewer's brand bar does: same chain,
// same fallback component, same height. Layout itself is verified in a real browser (jsdom has
// none); these pin the selection, the fallback chain and the sizing contract that prevents
// stretching, cropping and recolouring.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";
import previewSource from "./BrandingPreview.jsx?raw";
import viewerSource from "../../pages/watch/EventWatch.jsx?raw";
import { ThemeProvider, useTheme } from "../../theme/ThemeContext";
import { logoCandidates } from "../../utils/orgLogo";
import BrandingPreview from "./BrandingPreview";

const LIGHT = "https://cdn.acme.com/logo-wide.png";   // a horizontal wordmark
const DARK = "https://cdn.acme.com/mark-dark.svg";    // a square mark

function ThemeButton() {
  const { theme, toggle } = useTheme();
  return <button type="button" onClick={toggle}>theme is {theme}</button>;
}

function show({ light = LIGHT, dark = DARK, theme = "dark" } = {}) {
  localStorage.setItem("theme", theme);
  return render(
    <ThemeProvider>
      <ThemeButton />
      <BrandingPreview open onClose={() => {}} accent="violet" logoUrl={light} logoUrlDark={dark} orgName="Acme Live" />
    </ThemeProvider>
  );
}

const logoBox = () => screen.getByTestId("branding-preview-logo");
const logoImg = () => logoBox().querySelector("img");

beforeEach(() => localStorage.clear());

describe("which logo the preview shows", () => {
  it("dark theme: the dark logo", () => {
    show({ theme: "dark" });
    expect(within(logoBox()).getByAltText("Acme Live logo")).toHaveAttribute("src", DARK);
  });

  it("dark theme without a dark logo: the light logo", () => {
    show({ theme: "dark", dark: "" });
    expect(within(logoBox()).getByAltText("Acme Live logo")).toHaveAttribute("src", LIGHT);
  });

  it("light theme: the light logo, never the dark one", () => {
    show({ theme: "light" });
    expect(within(logoBox()).getByAltText("Acme Live logo")).toHaveAttribute("src", LIGHT);
  });

  it("neither logo: the ZoikoStream wordmark on its light chip, legible on the dark preview", () => {
    show({ theme: "dark", light: "", dark: "" });
    const mark = within(logoBox()).getByAltText("ZoikoStream");
    expect(mark).toHaveAttribute("src", "/zoiko-logo.png");
    // ui/Logo's chip — what keeps the navy lettering visible on a dark surface. The defect
    // drew this asset bare on a slate tile.
    expect(mark.parentElement.className).toMatch(/\bbg-white/);
  });

  it("switching the theme swaps the preview's logo", async () => {
    const user = userEvent.setup();
    show({ theme: "light" });
    expect(logoImg()).toHaveAttribute("src", LIGHT);
    await user.click(screen.getByRole("button", { name: "theme is light" }));
    expect(logoImg()).toHaveAttribute("src", DARK);
    await user.click(screen.getByRole("button", { name: "theme is dark" }));
    expect(logoImg()).toHaveAttribute("src", LIGHT);
  });
});

describe("a logo that fails to load", () => {
  it("dark: a broken dark logo falls back to the light one, then to ZoikoStream — no broken icon", () => {
    show({ theme: "dark" });
    fireEvent.error(logoImg());
    expect(logoImg()).toHaveAttribute("src", LIGHT);
    fireEvent.error(logoImg());
    expect(within(logoBox()).getByAltText("ZoikoStream")).toHaveAttribute("src", "/zoiko-logo.png");
    expect(within(logoBox()).queryByAltText("Acme Live logo")).not.toBeInTheDocument();
  });

  it("light: a broken light logo falls back to ZoikoStream", () => {
    show({ theme: "light" });
    fireEvent.error(logoImg());
    expect(within(logoBox()).getByAltText("ZoikoStream")).toBeInTheDocument();
  });

  it("does not retry a source that already failed", () => {
    show({ theme: "dark" });
    fireEvent.error(logoImg());                      // the dark logo failed
    fireEvent.error(logoImg());                      // then the light one
    const srcs = [...logoBox().querySelectorAll("img")].map((i) => i.getAttribute("src"));
    expect(srcs).not.toContain(DARK);
    expect(srcs).not.toContain(LIGHT);
  });
});

describe("sizing: nothing stretched, cropped, blurred or recoloured", () => {
  it("draws at the brand bar's own fixed height with the width following the image", () => {
    show({ theme: "dark" });
    const cls = logoImg().className;
    expect(cls).toMatch(/\bh-6\b/);
    expect(cls).toMatch(/\bsm:h-7\b/);
    expect(cls).toMatch(/\bw-auto\b/);
    expect(cls).toMatch(/\bobject-contain\b/);
    expect(cls).toMatch(/\bobject-center\b/);
    // Height fixed + width auto keeps the image's own aspect ratio: a square mark stays square.
    // (Whole class tokens: "max-w-full" is a bound, not a width.)
    const widths = cls.split(/\s+/).filter((t) => /^(?:[a-z]+:)?w-/.test(t));
    expect(widths).toEqual(["w-auto"]);
    expect(cls).not.toMatch(/object-cover|object-fill|max-h-full/);
  });

  it("bounds a wide logo by the box width, so it letterboxes instead of being cropped", () => {
    show({ theme: "light" });
    expect(logoImg().className).toMatch(/\bmax-w-full\b/);
    // One width bound, not two competing ones.
    expect(logoImg().className.match(/max-w-/g)).toHaveLength(1);
  });

  it("sits in a fixed box wider than tall — not the old 48px square — so loading never shifts the title", () => {
    show({ theme: "dark" });
    const box = logoBox().className;
    expect(box).toMatch(/\bh-12\b/);
    expect(box).toMatch(/\bw-28\b/);
    expect(box).toMatch(/\bsm:w-32\b/);
    expect(box).not.toMatch(/\bw-12\b/);
    expect(box).toMatch(/\bshrink-0\b/);
  });

  it("shows the logo on the viewer brand bar's own surface, without filters or opacity", () => {
    show({ theme: "dark" });
    expect(logoBox().className).toMatch(/\bbg-white\b/);
    expect(logoBox().className).toMatch(/dark:bg-slate-950/);
    for (const el of [logoBox(), logoImg()]) {
      expect(el.className).not.toMatch(/invert|filter|grayscale|brightness|contrast|blur|opacity-/);
      expect(el.getAttribute("style") || "").not.toMatch(/filter|opacity/);
    }
  });
});

describe("one implementation for the preview and the viewer page", () => {
  it("both render through ui/OrgLogo; the preview has no image handling of its own", () => {
    expect(previewSource).toMatch(/<OrgLogo\b/);
    expect(viewerSource).toMatch(/<OrgLogo\b/);
    expect(previewSource).not.toMatch(/<img\b/);
    expect(previewSource).not.toMatch(/fallback=/);
    expect(previewSource).not.toMatch(/zoiko-logo\.png/);
  });

  it("the preview picks the same logo the shared selection rule picks", () => {
    for (const theme of ["light", "dark"]) {
      for (const [light, dark] of [[LIGHT, DARK], [LIGHT, ""], ["", DARK]]) {
        const view = show({ theme, light, dark });
        const expected = logoCandidates({ light, dark }, theme)[0] ?? "/zoiko-logo.png";
        expect(logoImg()).toHaveAttribute("src", expected);
        view.unmount();
      }
    }
  });

  it("the ZoikoStream fallback is the full-resolution UI wordmark, not a favicon or thumbnail", () => {
    show({ light: "", dark: "" });
    expect(logoImg()).toHaveAttribute("src", "/zoiko-logo.png");
    const png = readFileSync(resolve(process.cwd(), "public/zoiko-logo.png"));
    const width = png.readUInt32BE(16);
    const height = png.readUInt32BE(20);
    expect(width).toBeGreaterThanOrEqual(1000);      // 1336 x 471 today
    expect(width / height).toBeGreaterThan(2);       // the horizontal wordmark, not the square icon
  });
});
