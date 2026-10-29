import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const css = readFileSync(resolve(process.cwd(), "src/index.css"), "utf8");

// Collapse whitespace and strip comments for robust AST-like structural assertions
const flat = css.replace(/\/\*[\s\S]*?\*\//g, " ").replace(/\s+/g, " ");

describe("Chrome/WebKit autofill background and contrast", () => {
  it("declares autofill overrides for input, textarea, and select controls", () => {
    expect(flat).toMatch(/input:-webkit-autofill/);
    expect(flat).toMatch(/input:autofill/);
    expect(flat).toMatch(/textarea:-webkit-autofill/);
    expect(flat).toMatch(/select:-webkit-autofill/);
  });

  it("applies inset box-shadow to override browser UA background wash in light theme", () => {
    // Light theme background must be pure white (#ffffff) inset box-shadow
    expect(flat).toMatch(/input:-webkit-autofill[^}]*box-shadow:\s*0\s+0\s+0\s+1000px\s+#ffffff\s+inset/);
    expect(flat).toMatch(/input:-webkit-autofill[^}]*-webkit-text-fill-color:\s*#0f172a/);
    expect(flat).toMatch(/input:-webkit-autofill[^}]*caret-color:\s*#0f172a/);
  });

  it("applies dark inset box-shadow and light text in dark theme", () => {
    // Dark theme background must be deep slate (#0f172a) inset box-shadow
    expect(flat).toMatch(/\.dark\s+input:-webkit-autofill[^}]*box-shadow:\s*0\s+0\s+0\s+1000px\s+#0f172a\s+inset/);
    expect(flat).toMatch(/\.dark\s+input:-webkit-autofill[^}]*-webkit-text-fill-color:\s*#f8fafc/);
    expect(flat).toMatch(/\.dark\s+input:-webkit-autofill[^}]*caret-color:\s*#f8fafc/);
  });

  it("does not violate root-only color-scheme policy", () => {
    // Assert that color-scheme is never declared on input or select controls directly
    const perControl = flat.match(/(select|input)[^{]*\{[^}]*color-scheme/g) || [];
    expect(perControl).toHaveLength(0);
  });

  it("preserves accessible focus ring definition", () => {
    expect(flat).toMatch(/:where\([^)]*input[^)]*\):focus-visible\s*\{[^}]*outline:\s*2px\s+solid\s+#8b5cf6/);
  });
});

