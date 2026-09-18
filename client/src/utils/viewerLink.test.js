// The viewer link: copyable, never openable, never rendered.
//
// The organizer console's job is to hand the attendee link to somebody else. Before this,
// Playback & Access printed the full watch URL in a <code> block next to a button labelled
// "Open the attendee watch page" — so the console both displayed the link and walked its own
// operator into the attendee experience for their own event. These pin the rule that
// replaced it: exactly one affordance, and it copies.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { copyViewerLink, viewerLinkFor } from "./viewerLink";

const EVENT_ID = "d9c6e1a0-d38e-4aac-9307-eceac77f4d4d";

let writeText;

beforeEach(() => {
  writeText = vi.fn(() => Promise.resolve());
  Object.defineProperty(navigator, "clipboard", {
    value: { writeText },
    configurable: true,
    writable: true,
  });
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("the URL it builds", () => {
  it("is the real backend-wired watch route, absolute", () => {
    // /e/:id is the fully-mocked marketing page; the wired viewer page is this one.
    expect(viewerLinkFor(EVENT_ID)).toBe(
      `${window.location.origin}/events/${EVENT_ID}/watch`
    );
  });

  it("is per event", () => {
    expect(viewerLinkFor("a")).not.toBe(viewerLinkFor("b"));
  });
});

describe("copying", () => {
  it("writes exactly that URL to the clipboard", async () => {
    await expect(copyViewerLink(EVENT_ID)).resolves.toBe(true);
    expect(writeText).toHaveBeenCalledWith(
      `${window.location.origin}/events/${EVENT_ID}/watch`
    );
  });

  it("reports failure instead of claiming success when the clipboard refuses", async () => {
    // Insecure origin, denied permission, or an older browser. The old inline call used
    // `navigator.clipboard?.writeText(...)` with no await and toasted success regardless,
    // so a refusal looked like a copy.
    writeText.mockRejectedValueOnce(new Error("NotAllowedError"));
    await expect(copyViewerLink(EVENT_ID)).resolves.toBe(false);
  });

  it("reports failure when there is no clipboard API at all", async () => {
    Object.defineProperty(navigator, "clipboard", { value: undefined, configurable: true });
    await expect(copyViewerLink(EVENT_ID)).resolves.toBe(false);
  });

  it("never throws at the call site", async () => {
    writeText.mockImplementation(() => { throw new Error("boom"); });
    await expect(copyViewerLink(EVENT_ID)).resolves.toBe(false);
  });
});

describe("the module offers no way to OPEN the viewer page", () => {
  it("exports only the builder and the copier", async () => {
    const mod = await import("./viewerLink");
    expect(Object.keys(mod).sort()).toEqual(["copyViewerLink", "viewerLinkFor"]);
  });

  it("does not navigate or open a window", async () => {
    const open = vi.spyOn(window, "open").mockImplementation(() => null);
    await copyViewerLink(EVENT_ID);
    expect(open).not.toHaveBeenCalled();
    open.mockRestore();
  });
});
