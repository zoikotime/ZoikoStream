import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mockSelectQuality = vi.fn();
const mockToggleMic = vi.fn();

vi.mock("../../hooks/useLiveKitViewer", async (importOriginal) => {
  const mediaRef = { current: null };
  const { resolveQuality } = await importOriginal();
  return {
    resolveQuality,
    default: () => ({
      mediaRef,
      connected: true,
      reconnecting: false,
      hasVideo: true,
      hasAudio: true,
      error: null,
      videoLayers: [
        { quality: "high", label: "1080p" },
        { quality: "medium", label: "720p" },
      ],
      hasVideoPublication: true,
      quality: "auto",
      selectQuality: mockSelectQuality,
      micOn: false,
      micError: null,
      toggleMic: mockToggleMic,
      enableMic: vi.fn(),
      micLive: false,
      captions: { available: true, text: "Live captions active" },
    }),
  };
});

vi.mock("../../ui/Toast", () => ({
  notify: { error: vi.fn(), success: vi.fn(), alert: vi.fn(), info: vi.fn() },
}));

import VideoPlayer from "./VideoPlayer";
import { ThemeProvider } from "../../theme/ThemeContext";

const EVENT = { id: "e1", name: "Viewer Test Stream", status: "Live", host: "Akshay", accent: "violet" };
const WATCH = {
  id: "e1",
  status: "live",
  livekit_token: "lk-token",
  livekit_url: "wss://lk.test",
  room: "r1",
  reactions_enabled: true,
};

function setViewport(phone) {
  window.matchMedia = vi.fn().mockImplementation((query) => ({
    media: query,
    matches: phone && query.includes("max-width"),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    addListener: vi.fn(),
    removeListener: vi.fn(),
    dispatchEvent: vi.fn(),
  }));
}

const show = (props = {}) =>
  render(
    <ThemeProvider>
      <div data-testid="outside-area" style={{ padding: "20px" }}>
        <p>Outside of player</p>
        <VideoPlayer event={EVENT} viewers={42} watch={WATCH} {...props} />
      </div>
    </ThemeProvider>
  );

const gear = () => screen.getByRole("button", { name: /^settings$/i });
const popover = () => document.getElementById("video-player-settings-menu");
const sheet = () => screen.queryByRole("dialog", { name: /video settings/i });

const realMatchMedia = window.matchMedia;
afterEach(() => {
  window.matchMedia = realMatchMedia;
});
beforeEach(() => {
  vi.clearAllMocks();
  setViewport(false);
});

describe("Viewer VideoPlayer Settings Popup", () => {
  // 1. click Settings → opens
  it("1. click Settings opens the popup with proper ARIA attributes", async () => {
    const u = userEvent.setup();
    show();

    const btn = gear();
    expect(btn).toHaveAttribute("aria-expanded", "false");
    expect(btn).toHaveAttribute("aria-haspopup", "menu");
    expect(popover()).toBeNull();

    await u.click(btn);

    expect(btn).toHaveAttribute("aria-expanded", "true");
    const menu = popover();
    expect(menu).toBeInTheDocument();
    expect(menu).toHaveAttribute("role", "menu");
    expect(menu).toHaveAttribute("aria-label", "Settings menu");
    expect(within(menu).getByText("Quality")).toBeInTheDocument();
  });

  // 2. click Settings again → closes
  it("2. click Settings again toggles it closed", async () => {
    const u = userEvent.setup();
    show();

    const btn = gear();
    await u.click(btn);
    expect(popover()).toBeInTheDocument();
    expect(btn).toHaveAttribute("aria-expanded", "true");

    await u.click(btn);
    expect(popover()).toBeNull();
    expect(btn).toHaveAttribute("aria-expanded", "false");
  });

  // 3. click outside → closes
  it("3. click outside the popup closes it", async () => {
    const u = userEvent.setup();
    show();

    await u.click(gear());
    expect(popover()).toBeInTheDocument();

    // Click on the outside area element
    await u.click(screen.getByTestId("outside-area"));
    expect(popover()).toBeNull();
    expect(gear()).toHaveAttribute("aria-expanded", "false");
  });

  // 4. click inside → stays open
  it("4. click inside the popup keeps it open", async () => {
    const u = userEvent.setup();
    show();

    await u.click(gear());
    const menu = popover();
    expect(menu).toBeInTheDocument();

    // Click on "Quality" row inside the menu
    await u.click(within(menu).getByText("Quality"));

    // Menu should still be open, showing sub-page options
    const openMenu = popover();
    expect(openMenu).toBeInTheDocument();
    expect(within(openMenu).getByRole("button", { name: /back to settings/i })).toBeInTheDocument();
    expect(within(openMenu).getByText("1080p")).toBeInTheDocument();
  });

  // 5. press Escape → closes and returns focus
  it("5. press Escape closes the popup and restores focus to Settings button", async () => {
    const u = userEvent.setup();
    show();

    const btn = gear();
    await u.click(btn);
    expect(popover()).toBeInTheDocument();

    fireEvent.keyDown(document, { key: "Escape" });

    expect(popover()).toBeNull();
    expect(btn).toHaveAttribute("aria-expanded", "false");
    expect(document.activeElement).toBe(btn);
  });

  // 6. fullscreen state does not leave popup stuck
  it("6. fullscreen state change closes popup so it does not remain stuck", async () => {
    const u = userEvent.setup();
    show();

    await u.click(gear());
    expect(popover()).toBeInTheDocument();

    // Dispatch fullscreenchange
    fireEvent(document, new Event("fullscreenchange"));

    expect(popover()).toBeNull();
    expect(gear()).toHaveAttribute("aria-expanded", "false");
  });

  // 7. mobile/pointer outside interaction works
  it("7. pointerdown / touch outside dismissal works across desktop and mobile", async () => {
    // Desktop pointerdown outside
    const u = userEvent.setup();
    const view1 = show();

    await u.click(gear());
    expect(popover()).toBeInTheDocument();

    fireEvent.pointerDown(document.body);
    expect(popover()).toBeNull();
    view1.unmount();

    // Mobile sheet outside backdrop tap
    setViewport(true);
    const view2 = show();
    await u.click(gear());
    expect(sheet()).toBeInTheDocument();

    const backdrop = document.querySelector(".bg-slate-950\\/60");
    expect(backdrop).toBeInTheDocument();
    await u.click(backdrop);
    expect(sheet()).toBeNull();
    view2.unmount();
  });

  // 8. another player menu closes Settings if applicable
  it("8. clicking another player control (captions CC) closes Settings", async () => {
    const u = userEvent.setup();
    show();

    await u.click(gear());
    expect(popover()).toBeInTheDocument();

    const captionsBtn = screen.getByRole("button", { name: /captions/i });
    expect(captionsBtn).toBeInTheDocument();

    // Click captions button
    await u.click(captionsBtn);

    // Settings must be dismissed
    expect(popover()).toBeNull();
  });

  // 9. repeated open/close does not leave stale state
  it("9. repeated open/close cycles reset to main settings page cleanly", async () => {
    const u = userEvent.setup();
    show();

    const btn = gear();

    // Cycle 1: Open -> navigate into Quality -> dismiss via outside click
    await u.click(btn);
    await u.click(within(popover()).getByText("Quality"));
    expect(within(popover()).getByText("1080p")).toBeInTheDocument();
    fireEvent.pointerDown(document.body);
    expect(popover()).toBeNull();

    // Cycle 2: Open -> must be on "main" page, not stale "Quality" page
    await u.click(btn);
    expect(within(popover()).getByText("Quality")).toBeInTheDocument();
    expect(within(popover()).getByText("Playback speed")).toBeInTheDocument();

    // Cycle 3: Navigate to speed -> press Escape
    await u.click(within(popover()).getByText("Playback speed"));
    expect(within(popover()).getByText("1.5x")).toBeInTheDocument();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(popover()).toBeNull();

    // Cycle 4: Open again -> resets to main page
    await u.click(btn);
    expect(within(popover()).getByText("Quality")).toBeInTheDocument();
  });

  // 10. viewer video continues playing
  it("10. video playback is uninterrupted during open, toggle, and dismiss", async () => {
    const u = userEvent.setup();
    show();

    const video = document.querySelector("video");
    expect(video).toBeInTheDocument();
    const pauseSpy = vi.spyOn(video, "pause");

    // Open
    await u.click(gear());
    expect(pauseSpy).not.toHaveBeenCalled();

    // Toggle close
    await u.click(gear());
    expect(pauseSpy).not.toHaveBeenCalled();

    // Re-open
    await u.click(gear());
    expect(pauseSpy).not.toHaveBeenCalled();

    // Dismiss via Escape
    fireEvent.keyDown(document, { key: "Escape" });
    expect(pauseSpy).not.toHaveBeenCalled();

    // Video element reference remains unchanged
    expect(document.querySelector("video")).toBe(video);
  });
});
