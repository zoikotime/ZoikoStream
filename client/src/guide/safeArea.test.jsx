// The shared Guide launcher safe area (guide/safeArea.js): published footprint, lifting over
// bottom-docked chrome, and stacking so two launchers never share a spot.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

vi.mock("../api", () => ({ default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() }, API_BASE: "http://api.test" }));

import api from "../api";
import OperatorGuide from "../operator-guide/OperatorGuide";
import GuideWidget from "./GuideWidget";
import { clearSession } from "./guideApi";
import { GUIDE_AVOID_ATTR, refreshGuideSafeArea, registerGuideLauncher } from "./safeArea";

const html = () => document.documentElement;
const rootVar = (name) => html().style.getPropertyValue(name);
const VH = window.innerHeight; // jsdom: 768

// jsdom does no layout: give an element the box a browser would.
function box(el, { width = 100, height = 44, bottom = 20, right = 20 } = {}) {
  Object.defineProperty(el, "offsetWidth", { configurable: true, get: () => width });
  Object.defineProperty(el, "offsetHeight", { configurable: true, get: () => height });
  el.style.position = "fixed";
  el.style.bottom = `${bottom}px`;
  el.style.right = `${right}px`;
  return el;
}
function launcher(opts) {
  const el = box(document.createElement("button"), opts);
  document.body.appendChild(el);
  return el;
}
function bar({ top, bottom, position } = {}) {
  const el = document.createElement("div");
  el.setAttribute(GUIDE_AVOID_ATTR, "");
  if (position) el.style.position = position;
  el.getBoundingClientRect = () => ({ top, bottom, left: 0, right: 1024, width: 1024, height: bottom - top, x: 0, y: top });
  document.body.appendChild(el);
  return el;
}

const cleanups = [];
const register = (el, opts) => {
  const off = registerGuideLauncher(el, opts);
  cleanups.push(off);
  return off;
};

afterEach(() => {
  cleanups.splice(0).forEach((off) => off());
  document.body.innerHTML = "";
  vi.restoreAllMocks();
});

describe("published footprint", () => {
  it("is set on <html> while a launcher is mounted and removed when it goes", () => {
    expect(rootVar("--guide-safe-bottom")).toBe("");
    const el = launcher({ width: 100, height: 44, bottom: 20, right: 20 });
    const off = register(el);
    expect(rootVar("--guide-safe-bottom")).toBe("64px");   // 20 inset + 44 tall
    expect(rootVar("--guide-safe-right")).toBe("120px");   // 20 inset + 100 wide
    expect(rootVar("--guide-lift")).toBe("0px");
    off();
    expect(rootVar("--guide-safe-bottom")).toBe("");
    expect(rootVar("--guide-safe-right")).toBe("");
    expect(rootVar("--guide-lift")).toBe("");
    expect(el.style.getPropertyValue("--guide-stack")).toBe("");
  });

  it("a launcher that is not displayed takes no room", () => {
    const el = launcher({ width: 0, height: 0 });
    register(el);
    expect(rootVar("--guide-safe-bottom")).toBe("0px");
  });
});

describe("bottom-docked chrome", () => {
  it("lifts the launchers above a docked bar, by the bar's height", () => {
    bar({ top: VH - 72, bottom: VH });
    register(launcher());
    expect(rootVar("--guide-lift")).toBe("72px");
  });

  it("counts a thin status strip under the bar as part of the band", () => {
    bar({ top: VH - 100, bottom: VH - 28 });
    register(launcher());
    expect(rootVar("--guide-lift")).toBe("100px");
  });

  it("ignores a marked element that is not at the bottom edge", () => {
    bar({ top: 200, bottom: 272 });
    register(launcher());
    expect(rootVar("--guide-lift")).toBe("0px");
  });

  it("ignores in-flow content that scrolls past in a scrolling page, but not a fixed bar", () => {
    const scroller = document.scrollingElement || html();
    vi.spyOn(scroller, "scrollHeight", "get").mockReturnValue(VH * 3);
    vi.spyOn(scroller, "clientHeight", "get").mockReturnValue(VH);
    const inFlow = bar({ top: VH - 72, bottom: VH });
    register(launcher());
    expect(rootVar("--guide-lift")).toBe("0px");
    inFlow.style.position = "fixed";
    refreshGuideSafeArea();
    expect(rootVar("--guide-lift")).toBe("72px");
    expect(html().hasAttribute("data-guide-yield")).toBe(false);   // lifted, so nothing to yield
  });

  it("in a scrolling page the launcher yields while marked controls pass under it, and returns after", () => {
    const scroller = document.scrollingElement || html();
    vi.spyOn(scroller, "scrollHeight", "get").mockReturnValue(VH * 3);
    vi.spyOn(scroller, "clientHeight", "get").mockReturnValue(VH);
    let top = VH - 72;                                            // the deck at the bottom edge
    const deck = bar({ top, bottom: top + 72 });
    deck.getBoundingClientRect = () => ({ top, bottom: top + 72, left: 0, right: 1024, width: 1024, height: 72 });
    register(launcher({ width: 100, height: 44, bottom: 20, right: 20 }));
    expect(rootVar("--guide-lift")).toBe("0px");                  // nothing jumps
    expect(html().hasAttribute("data-guide-yield")).toBe(true);   // the deck's controls stay usable

    top = 300;                                                    // scrolled on
    refreshGuideSafeArea();
    expect(html().hasAttribute("data-guide-yield")).toBe(false);
  });

  it("never lifts past half the viewport", () => {
    bar({ top: 10, bottom: VH });
    register(launcher());
    expect(rootVar("--guide-lift")).toBe(`${Math.round(VH / 2)}px`);
  });

  it("drops the lift when the bar goes away", () => {
    const b = bar({ top: VH - 72, bottom: VH });
    register(launcher());
    expect(rootVar("--guide-lift")).toBe("72px");
    b.remove();
    refreshGuideSafeArea();
    expect(rootVar("--guide-lift")).toBe("0px");
  });
});

describe("two launchers never share a spot", () => {
  it("the higher order stacks above the lower one, and drops back when it is alone", () => {
    const consoleLauncher = launcher({ height: 44 });
    const publicLauncher = launcher({ height: 56 });
    register(publicLauncher, { order: 1 });
    const offConsole = register(consoleLauncher, { order: 0 });
    expect(consoleLauncher.style.getPropertyValue("--guide-stack")).toBe("0px");
    expect(publicLauncher.style.getPropertyValue("--guide-stack")).toBe("56px"); // 44 + 12 gap
    offConsole();
    expect(publicLauncher.style.getPropertyValue("--guide-stack")).toBe("0px");
  });
});

// ── integration ──────────────────────────────────────────────────────────────────────
const BOOT = {
  user: { name: "Dana Ops", role: "org_admin", is_org_admin: true },
  context: { organization: { id: "o1", name: "Acme" }, workspace: "Members", route: "/organization/members", event: null, environment: "production", role: "org_admin" },
  capabilities: { docs_qa: true, actions: true },
  attention: { count: 0, items: [] },
  suggestions: [],
  recent: [],
};

describe("the launchers use it", () => {
  let restore;
  beforeEach(() => {
    clearSession();
    api.get.mockImplementation((url) => Promise.resolve({ data: url.endsWith("/bootstrap") ? BOOT : {} }));
    // Launcher boxes, as a browser would lay them out.
    const h = Object.getOwnPropertyDescriptor(HTMLElement.prototype, "offsetHeight");
    const w = Object.getOwnPropertyDescriptor(HTMLElement.prototype, "offsetWidth");
    const size = (el, dim) => {
      if (el.classList?.contains("og-launcher")) return dim === "h" ? 44 : 96;
      if (el.classList?.contains("zg-launcher")) return 56;
      return 0;
    };
    Object.defineProperty(HTMLElement.prototype, "offsetHeight", { configurable: true, get() { return size(this, "h"); } });
    Object.defineProperty(HTMLElement.prototype, "offsetWidth", { configurable: true, get() { return size(this, "w"); } });
    restore = () => {
      Object.defineProperty(HTMLElement.prototype, "offsetHeight", h);
      Object.defineProperty(HTMLElement.prototype, "offsetWidth", w);
    };
  });
  afterEach(() => restore());

  it("the Operator Guide publishes its footprint on mount and clears it on unmount", async () => {
    const { unmount } = render(
      <MemoryRouter initialEntries={["/organization/members"]}>
        <OperatorGuide route="/organization/members" routeEventId={null} />
      </MemoryRouter>,
    );
    await screen.findByRole("button", { name: /open guide/i });
    await waitFor(() => expect(rootVar("--guide-safe-bottom")).toBe("44px"));
    unmount();
    expect(rootVar("--guide-safe-bottom")).toBe("");
  });

  it("the Operator Guide rises over the host control bar", async () => {
    bar({ top: VH - 72, bottom: VH });
    render(
      <MemoryRouter initialEntries={["/host/dashboard"]}>
        <OperatorGuide route="/host/dashboard" routeEventId={null} />
      </MemoryRouter>,
    );
    await screen.findByRole("button", { name: /open guide/i });
    await waitFor(() => expect(rootVar("--guide-lift")).toBe("72px"));
  });

  it("with both mounted, the public launcher stacks above the console one", async () => {
    globalThis.fetch = vi.fn(async () => new Response(JSON.stringify({ enabled: true, limits: {}, direct_routes: [], suggested_intents: [] }), { status: 200, headers: { "content-type": "application/json" } }));
    render(
      <MemoryRouter initialEntries={["/contact"]}>
        <OperatorGuide route="/organization/members" routeEventId={null} />
        <GuideWidget page={{ page_id: "contact", page_type: "contact" }} pathname="/contact" />
      </MemoryRouter>,
    );
    const pub = await screen.findByRole("button", { name: /open zoikostream guide/i });
    const op = await screen.findByRole("button", { name: /open guide/i });
    await act(async () => refreshGuideSafeArea());
    expect(op.style.getPropertyValue("--guide-stack")).toBe("0px");
    expect(pub.style.getPropertyValue("--guide-stack")).toBe("56px");
  });
});
