// initNativeChrome adds viewport-fit=cover in the native build only, so env(safe-area-inset-*)
// can report the status bar on devices that draw the app under it. The web build's viewport
// meta must stay exactly as index.html wrote it.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const ORIGINAL = "width=device-width, initial-scale=1.0";

async function loadBridge(isNative) {
  vi.resetModules();
  vi.doMock("../platform", () => ({ IS_NATIVE: isNative }));
  return import("./bridge");
}

const viewport = () => document.querySelector('meta[name="viewport"]').getAttribute("content");

beforeEach(() => {
  document.head.innerHTML = `<meta name="viewport" content="${ORIGINAL}">`;
  document.documentElement.className = "";
});

afterEach(() => {
  vi.doUnmock("../platform");
});

describe("viewport-fit for the safe-area inset", () => {
  it("is added in the native build, once", async () => {
    const { initNativeChrome } = await loadBridge(true);
    await initNativeChrome();
    await initNativeChrome();
    expect(viewport()).toBe(`${ORIGINAL}, viewport-fit=cover`);
    expect(document.documentElement).toHaveClass("native");
  });

  it("is not added in the web build", async () => {
    const { initNativeChrome } = await loadBridge(false);
    await initNativeChrome();
    expect(viewport()).toBe(ORIGINAL);
    expect(document.documentElement).not.toHaveClass("native");
  });
});
