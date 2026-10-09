// The organization's logo in the viewer page's brand bar, for the viewer's theme.
//
// The bar used to show only the ZoikoStream mark. It now shows the organization's logo — the
// dark-theme one on the dark theme, falling back to the light one, then to ZoikoStream — and
// follows the page's own theme toggle and the payload's next poll.
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../api", () => ({
  default: { get: vi.fn(), post: vi.fn() },
  errMsg: (e, fallback) => e?.response?.data?.detail || e?.message || fallback || "Error",
}));
vi.mock("../../auth/AuthContext", () => ({ useAuth: () => ({ user: null }) }));
vi.mock("../../hooks/useEventStream", () => ({
  default: () => ({ status: "idle", closeReason: null, latency: null, attempt: 0, send: vi.fn(() => false), disconnect: vi.fn() }),
}));
vi.mock("../../hooks/useKeepAwake", () => ({ default: () => {} }));
vi.mock("../../hooks/useLiveKitViewer", async (importOriginal) => {
  const { resolveQuality } = await importOriginal();
  return {
    resolveQuality,
    default: () => ({
      mediaRef: { current: null }, connected: false, reconnecting: false, hasVideo: false, hasAudio: false,
      error: null, videoLayers: [], hasVideoPublication: false, quality: "auto", selectQuality: vi.fn(),
      micOn: false, micError: null, toggleMic: vi.fn(), enableMic: vi.fn(), micLive: false,
    }),
  };
});
vi.mock("../../utils/sound", () => ({ playAlertChime: vi.fn(), unlockAudio: vi.fn() }));
vi.mock("../../ui/Toast", () => ({ notify: { error: vi.fn(), success: vi.fn(), alert: vi.fn(), info: vi.fn() } }));

import api from "../../api";
import { ThemeProvider } from "../../theme/ThemeContext";
import EventWatch from "./EventWatch";

const EVENT_ID = "6e1f2a3b-4c5d-4e6f-8a9b-0c1d2e3f4a5c";
const LIGHT = "https://cdn.acme.com/logo.png";
const DARK = "https://cdn.acme.com/logo-dark.svg";

// A scheduled event: the page polls it, which is how a saved logo reaches an open page.
const WATCH = {
  id: EVENT_ID, title: "Spring Gala", status: "scheduled", visibility: "public", registered: true,
  registration_required: false, chat_enabled: true, qa_enabled: true, polls_enabled: true,
  reactions_enabled: true, raise_hand_enabled: false, not_started: true, expired: false,
  media_status: "waiting_for_host", livekit_token: null,
  organization_name: "Acme Live", organization_logo_url: LIGHT, organization_logo_url_dark: DARK,
};

const serve = (payload) => vi.mocked(api.get).mockResolvedValue({ data: payload });

function open(theme = "light") {
  localStorage.setItem("theme", theme);
  return render(
    <ThemeProvider>
      <MemoryRouter initialEntries={[`/events/${EVENT_ID}/watch`]}>
        <Routes><Route path="/events/:eventId/watch" element={<EventWatch />} /></Routes>
      </MemoryRouter>
    </ThemeProvider>
  );
}

// The brand bar is the page's first <header>; it renders once the payload has arrived.
const brandBar = () => document.querySelector("header");
const orgLogo = () => waitFor(() => within(brandBar()).getByAltText("Acme Live logo"));

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  sessionStorage.clear();
});
afterEach(() => vi.useRealTimers());

describe("the viewer brand bar", () => {
  it("light theme shows the organization's light logo", async () => {
    serve(WATCH);
    open("light");
    expect(await orgLogo()).toHaveAttribute("src", LIGHT);
  });

  it("dark theme shows the organization's dark logo", async () => {
    serve(WATCH);
    open("dark");
    expect(await orgLogo()).toHaveAttribute("src", DARK);
  });

  it("dark theme without a dark logo falls back to the light logo (a single-logo organization)", async () => {
    serve({ ...WATCH, organization_logo_url_dark: null });
    open("dark");
    expect(await orgLogo()).toHaveAttribute("src", LIGHT);
  });

  it("an organization with no logo keeps the ZoikoStream mark", async () => {
    serve({ ...WATCH, organization_logo_url: null, organization_logo_url_dark: null });
    open("dark");
    await screen.findAllByText("Spring Gala");
    expect(within(brandBar()).getByAltText("ZoikoStream")).toHaveAttribute("src", "/zoiko-logo.png");
    expect(within(brandBar()).queryByAltText("Acme Live logo")).not.toBeInTheDocument();
  });

  it("an older payload without the logo fields keeps the ZoikoStream mark", async () => {
    const legacy = { ...WATCH };
    delete legacy.organization_logo_url;
    delete legacy.organization_logo_url_dark;
    serve(legacy);
    open("light");
    await screen.findAllByText("Spring Gala");
    expect(within(brandBar()).getByAltText("ZoikoStream")).toBeInTheDocument();
  });
});

describe("theme switching on the viewer page", () => {
  it("the page's own theme toggle swaps the logo", async () => {
    const user = userEvent.setup();
    serve(WATCH);
    open("light");
    expect(await orgLogo()).toHaveAttribute("src", LIGHT);

    await user.click(screen.getByRole("button", { name: "Switch to dark theme" }));
    expect(await orgLogo()).toHaveAttribute("src", DARK);

    await user.click(screen.getByRole("button", { name: "Switch to light theme" }));
    expect(await orgLogo()).toHaveAttribute("src", LIGHT);
  });
});

describe("a broken logo", () => {
  it("a broken dark logo falls back to the light one and the bar keeps its shape", async () => {
    serve(WATCH);
    open("dark");
    const img = await orgLogo();
    const barClass = brandBar().firstElementChild.className;
    fireEvent.error(img);

    expect(await orgLogo()).toHaveAttribute("src", LIGHT);
    expect((await orgLogo()).className).toMatch(/h-6/);
    expect(brandBar().firstElementChild.className).toBe(barClass);
    expect(screen.getAllByText("Spring Gala").length).toBeGreaterThan(0);
  });
});

describe("after an admin saves branding", () => {
  it("an open viewer page picks up the new dark logo on its next poll", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    serve({ ...WATCH, organization_logo_url_dark: null });
    open("dark");
    expect(await orgLogo()).toHaveAttribute("src", LIGHT);

    serve(WATCH); // the admin saved a dark logo
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000);
    });
    await waitFor(async () => expect(await orgLogo()).toHaveAttribute("src", DARK));
  });

  it("and clearing it puts the light logo back", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    serve(WATCH);
    open("dark");
    expect(await orgLogo()).toHaveAttribute("src", DARK);

    serve({ ...WATCH, organization_logo_url_dark: null });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000);
    });
    await waitFor(async () => expect(await orgLogo()).toHaveAttribute("src", LIGHT));
  });
});
