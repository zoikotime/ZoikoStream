// ui/OrgLogo: the organization's logo for the current theme, with the fallback chain
// dark -> light -> ZoikoStream, a broken image dropping down the chain instead of showing a
// broken-image icon, and no CSS recolouring of anyone's brand.
import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";
import { ThemeProvider, useTheme } from "../theme/ThemeContext";
import OrgLogo from "./OrgLogo";

const LIGHT = "https://cdn.acme.com/logo.png";
const DARK = "https://cdn.acme.com/logo-dark.svg";

function ThemeButton() {
  const { theme, toggle } = useTheme();
  return <button type="button" onClick={toggle}>theme is {theme}</button>;
}

function show(props, theme = "light") {
  localStorage.setItem("theme", theme);
  return render(
    <ThemeProvider>
      <ThemeButton />
      <OrgLogo name="Acme" height="h-7" {...props} />
    </ThemeProvider>
  );
}

const orgLogo = () => screen.getByAltText("Acme logo");
const zoiko = () => screen.getByAltText("ZoikoStream");

beforeEach(() => localStorage.clear());

describe("which logo a theme gets", () => {
  it("light theme uses the light logo", () => {
    show({ light: LIGHT, dark: DARK }, "light");
    expect(orgLogo()).toHaveAttribute("src", LIGHT);
  });

  it("dark theme uses the dark logo", () => {
    show({ light: LIGHT, dark: DARK }, "dark");
    expect(orgLogo()).toHaveAttribute("src", DARK);
  });

  it("dark theme with no dark logo falls back to the light logo", () => {
    show({ light: LIGHT, dark: null }, "dark");
    expect(orgLogo()).toHaveAttribute("src", LIGHT);
  });

  it("with neither, it is the ZoikoStream logo", () => {
    show({ light: null, dark: null }, "dark");
    expect(zoiko()).toHaveAttribute("src", "/zoiko-logo.png");
    expect(screen.queryByAltText("Acme logo")).not.toBeInTheDocument();
  });

  it("a dark logo alone is never shown on the light theme", () => {
    show({ light: null, dark: DARK }, "light");
    expect(zoiko()).toBeInTheDocument();
  });

  it("an explicit theme pins the choice regardless of the app's theme", () => {
    localStorage.setItem("theme", "light");
    render(
      <ThemeProvider>
        <OrgLogo name="Acme" light={LIGHT} dark={DARK} theme="dark" />
      </ThemeProvider>
    );
    expect(orgLogo()).toHaveAttribute("src", DARK);
  });
});

describe("theme switching", () => {
  it("swaps the rendered logo when the theme is toggled", async () => {
    const user = userEvent.setup();
    show({ light: LIGHT, dark: DARK }, "light");
    expect(orgLogo()).toHaveAttribute("src", LIGHT);

    await user.click(screen.getByRole("button", { name: "theme is light" }));
    expect(orgLogo()).toHaveAttribute("src", DARK);

    await user.click(screen.getByRole("button", { name: "theme is dark" }));
    expect(orgLogo()).toHaveAttribute("src", LIGHT);
  });
});

describe("broken images", () => {
  it("a broken dark logo falls back to the light one, then to ZoikoStream — never a broken icon", () => {
    show({ light: LIGHT, dark: DARK }, "dark");
    fireEvent.error(orgLogo());
    expect(orgLogo()).toHaveAttribute("src", LIGHT);

    fireEvent.error(orgLogo());
    expect(zoiko()).toBeInTheDocument();
    expect(screen.queryByAltText("Acme logo")).not.toBeInTheDocument();
  });

  it("keeps the caller's height and bounds the width, so a logo cannot reflow its header", () => {
    show({ light: LIGHT }, "light");
    expect(orgLogo().className).toMatch(/\bh-7\b/);
    expect(orgLogo().className).toMatch(/max-w-\[200px\]/);
    expect(orgLogo().className).toMatch(/object-contain/);
  });

  it("does not recolour a brand with CSS filters", () => {
    show({ light: LIGHT, dark: DARK }, "dark");
    expect(orgLogo().className).not.toMatch(/invert|filter|grayscale|brightness/);
    expect(orgLogo().getAttribute("style") || "").not.toMatch(/filter/);
  });
});
