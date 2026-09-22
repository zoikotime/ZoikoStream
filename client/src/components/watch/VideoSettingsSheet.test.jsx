// Video settings on a phone.
//
// ── THE BUG ────────────────────────────────────────────────────────────────────────────
// The gear opened a 224px popover positioned INSIDE the player — `absolute bottom-12
// right-0` on a container that is `overflow-hidden`. On a phone the video is ~200px tall,
// so the menu covered the picture, read as cramped, and was clipped by the element it lived
// in. Desktop, where the player is large, was always fine.
//
// A phone now gets a bottom sheet portalled to <body> instead. The assertion that carries
// the most weight is that the sheet is NOT inside the player element: that is the whole
// difference between "clipped by the video container" and "not".
//
// Second most important: the <video> node must be the SAME element before and after opening
// the sheet. Remounting it would restart the LiveKit subscription — a stream that reloads
// every time you check the quality menu.
import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../hooks/useLiveKitViewer", () => {
  const mediaRef = { current: null };
  return {
    default: () => ({
      mediaRef, connected: true, reconnecting: false, hasVideo: true, hasAudio: true,
      error: null,
      videoLayers: [
        { quality: "high", label: "1080p" },
        { quality: "medium", label: "720p" },
      ],
      hasVideoPublication: true, quality: "auto", selectQuality: vi.fn(),
      micOn: false, micError: null, toggleMic: vi.fn(), enableMic: vi.fn(), micLive: false,
    }),
  };
});
vi.mock("../../ui/Toast", () => ({
  notify: { error: vi.fn(), success: vi.fn(), alert: vi.fn(), info: vi.fn() },
}));

import VideoPlayer from "./VideoPlayer";
import { ThemeProvider } from "../../theme/ThemeContext";

const EVENT = { id: "e1", name: "Test 9", status: "Live", host: "Akshay", accent: "violet" };
const WATCH = {
  id: "e1", status: "live", livekit_token: "lk", livekit_url: "wss://x", room: "r1",
  reactions_enabled: true,
};

/** Drives the breakpoint the component reads, so a test states the viewport it means. */
function setViewport(phone) {
  window.matchMedia = vi.fn().mockImplementation((query) => ({
    media: query,
    // The component asks for (max-width: 639.98px); anything else is left unmatched.
    matches: phone && query.includes("max-width"),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    addListener: vi.fn(),
    removeListener: vi.fn(),
    dispatchEvent: vi.fn(),
  }));
}

const show = () =>
  render(
    <ThemeProvider>
      <VideoPlayer event={EVENT} viewers={39} watch={WATCH} />
    </ThemeProvider>
  );

const gear = () => screen.getByRole("button", { name: /^settings$/i });
const sheet = () => screen.queryByRole("dialog", { name: /video settings/i });

const realMatchMedia = window.matchMedia;
afterEach(() => { window.matchMedia = realMatchMedia; });
beforeEach(() => vi.clearAllMocks());

// ── the fix ────────────────────────────────────────────────────────────────────────────

describe("on a phone", () => {
  beforeEach(() => setViewport(true));

  it("opens a sheet, not the in-player popover", async () => {
    const u = userEvent.setup();
    show();
    expect(sheet()).not.toBeInTheDocument();

    await u.click(gear());

    expect(sheet()).toBeInTheDocument();
  });

  it("renders the sheet OUTSIDE the video container, so it cannot be clipped", async () => {
    const u = userEvent.setup();
    const { container } = show();
    await u.click(gear());

    // Portalled to <body>: the player subtree does not contain it. This is the bug, stated
    // as an assertion — the old popover was a descendant of an overflow-hidden ancestor.
    expect(container.contains(sheet())).toBe(false);
    expect(document.body.contains(sheet())).toBe(true);
  });

  it("keeps every option", async () => {
    const u = userEvent.setup();
    show();
    await u.click(gear());

    const panel = within(sheet());
    expect(panel.getByText("Quality")).toBeInTheDocument();
    expect(panel.getByText("Playback speed")).toBeInTheDocument();
    expect(panel.getByText("Picture-in-Picture")).toBeInTheDocument();
  });

  it("offers a close action", async () => {
    const u = userEvent.setup();
    show();
    await u.click(gear());

    await u.click(screen.getByRole("button", { name: /close settings/i }));

    expect(sheet()).not.toBeInTheDocument();
  });

  it("closes on a backdrop tap", async () => {
    const u = userEvent.setup();
    show();
    await u.click(gear());

    // The scrim ui/Overlay renders behind the panel.
    await u.click(document.querySelector(".bg-slate-950\\/60"));

    expect(sheet()).not.toBeInTheDocument();
  });

  it("closes on Escape", async () => {
    const u = userEvent.setup();
    show();
    await u.click(gear());

    fireEvent.keyDown(document, { key: "Escape" });

    expect(sheet()).not.toBeInTheDocument();
  });

  it("does not let a tap inside the sheet dismiss it", async () => {
    const u = userEvent.setup();
    show();
    await u.click(gear());

    await u.click(within(sheet()).getByText("Settings"));

    expect(sheet()).toBeInTheDocument();
  });

  it("scrolls internally and is capped to the visible viewport", async () => {
    const u = userEvent.setup();
    show();
    await u.click(gear());

    // svh rather than vh: iOS Safari's vh counts the retracted URL bar, so a vh cap puts
    // the last row under the browser chrome.
    expect(sheet().className).toMatch(/max-h-\[80svh\]/);
    expect(sheet().querySelector(".overflow-y-auto")).toBeTruthy();
    // Anchored to both edges, so nothing can push the page sideways.
    expect(sheet().className).toMatch(/inset-x-0/);
  });
});

// ── the sub-pages still work ───────────────────────────────────────────────────────────

describe("inside the sheet", () => {
  beforeEach(() => setViewport(true));

  it("opens Quality and lists the layers the publisher actually sent", async () => {
    const u = userEvent.setup();
    show();
    await u.click(gear());
    await u.click(within(sheet()).getByText("Quality"));

    const panel = within(sheet());
    expect(panel.getByText("Auto")).toBeInTheDocument();
    expect(panel.getByText("1080p")).toBeInTheDocument();
    expect(panel.getByText("720p")).toBeInTheDocument();
  });

  it("opens Playback speed", async () => {
    const u = userEvent.setup();
    show();
    await u.click(gear());
    await u.click(within(sheet()).getByText("Playback speed"));

    expect(within(sheet()).getByText("1.5x")).toBeInTheDocument();
  });

  it("goes back to the main page", async () => {
    const u = userEvent.setup();
    show();
    await u.click(gear());
    await u.click(within(sheet()).getByText("Quality"));
    await u.click(within(sheet()).getByRole("button", { name: /back to settings/i }));

    expect(within(sheet()).getByText("Picture-in-Picture")).toBeInTheDocument();
  });

  it("reopens on the main page after being closed mid-navigation", async () => {
    const u = userEvent.setup();
    show();
    await u.click(gear());
    await u.click(within(sheet()).getByText("Quality"));
    await u.click(screen.getByRole("button", { name: /close settings/i }));
    await u.click(gear());

    expect(within(sheet()).getByText("Picture-in-Picture")).toBeInTheDocument();
  });
});

// ── the stream is untouched ────────────────────────────────────────────────────────────

describe("opening settings", () => {
  beforeEach(() => setViewport(true));

  it("does not remount or move the video element", async () => {
    const u = userEvent.setup();
    show();
    const before = document.querySelector("video");
    const beforeParent = before?.parentElement;

    await u.click(gear());

    const after = document.querySelector("video");
    // Same node, same parent — nothing portalled the video, so LiveKit's subscription and
    // playback position are untouched.
    expect(after).toBe(before);
    expect(after?.parentElement).toBe(beforeParent);
  });

  it("leaves the video playing rather than pausing it", async () => {
    const u = userEvent.setup();
    show();
    const video = document.querySelector("video");
    const pause = vi.spyOn(video, "pause");

    await u.click(gear());

    expect(pause).not.toHaveBeenCalled();
  });
});

// ── desktop is untouched ───────────────────────────────────────────────────────────────

describe("on desktop", () => {
  beforeEach(() => setViewport(false));

  it("keeps the in-player popover", async () => {
    const u = userEvent.setup();
    const { container } = show();
    await u.click(gear());

    expect(sheet()).not.toBeInTheDocument();
    // Still a descendant of the player, exactly where it always was.
    expect(container.querySelector(".absolute.bottom-12.right-0")).toBeTruthy();
  });

  it("still shows every option there", async () => {
    const u = userEvent.setup();
    const { container } = show();
    await u.click(gear());

    const popover = within(container.querySelector(".absolute.bottom-12.right-0"));
    expect(popover.getByText("Quality")).toBeInTheDocument();
    expect(popover.getByText("Playback speed")).toBeInTheDocument();
    expect(popover.getByText("Picture-in-Picture")).toBeInTheDocument();
  });

  it("toggles shut on a second click of the gear", async () => {
    const u = userEvent.setup();
    const { container } = show();
    await u.click(gear());
    await u.click(gear());

    expect(container.querySelector(".absolute.bottom-12.right-0")).toBeFalsy();
  });
});

// ── never both at once ─────────────────────────────────────────────────────────────────

it("renders exactly one set of settings controls, whichever the viewport", async () => {
  for (const phone of [true, false]) {
    setViewport(phone);
    const u = userEvent.setup();
    const view = show();
    await u.click(gear());

    // Two copies would put every control in the accessibility tree twice.
    expect(screen.getAllByText("Picture-in-Picture")).toHaveLength(1);
    view.unmount();
  }
});
