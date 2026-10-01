// Accessibility on the existing viewer page (ZST-SPEC-VAP-001 §6.4).
//
//   * browser language is the initial language; a manual choice is remembered and wins;
//     languages are named in their own script; <html lang> follows the choice
//   * a persistent read-aloud control speaks the instruction on screen, in that language,
//     and every supported language has matching spoken text for every state
//   * captions get a control AT PLAYER LEVEL when the stream carries them
//   * critical controls are at least 48×48 CSS px, and fields/states have accessible names
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const lk = vi.hoisted(() => ({ state: {} }));

vi.mock("../../api", () => ({
  default: { get: vi.fn(), post: vi.fn() },
  errMsg: (e, fallback) => e?.response?.data?.detail || e?.message || fallback || "Error",
}));
vi.mock("../../auth/AuthContext", () => ({ useAuth: () => ({ user: null }) }));
vi.mock("../../hooks/useEventStream", () => ({
  default: () => ({ status: "open", closeReason: null, latency: 12, attempt: 0, send: vi.fn(() => true), disconnect: vi.fn() }),
}));
vi.mock("../../hooks/useKeepAwake", () => ({ default: () => {} }));
// The REAL resolveQuality: VideoPlayer imports it from this module to decide which quality
// row is checked, so a mock without it breaks the player (same pattern as the other tests).
vi.mock("../../hooks/useLiveKitViewer", async (importOriginal) => {
  const { resolveQuality } = await importOriginal();
  const mediaRef = { current: null };
  return { resolveQuality, default: () => ({ mediaRef, ...lk.state }) };
});
vi.mock("../../utils/sound", () => ({ playAlertChime: vi.fn(), unlockAudio: vi.fn() }));
vi.mock("../../ui/Toast", () => ({ notify: { error: vi.fn(), success: vi.fn(), alert: vi.fn(), info: vi.fn() } }));

import api from "../../api";
import { ThemeProvider } from "../../theme/ThemeContext";
import EventWatch from "./EventWatch";
import { LANGUAGES, LANGUAGE_STORAGE_KEY, detectLanguage, translate } from "./viewerLanguage";
import RegistrationGate from "../../components/watch/RegistrationGate";

const EVENT_ID = "1f0e2d3c-4b5a-4968-8776-655443322110";
const BASE = {
  id: EVENT_ID, title: "Service", visibility: "private", host_name: "Vihari", organization_name: "Northwind",
  chat_enabled: true, qa_enabled: true, polls_enabled: true, reactions_enabled: true, raise_hand_enabled: true,
  registered: true, registration_required: false, not_started: true, expired: false, media_status: "live",
  start_time: new Date(Date.now() + 3600e3).toISOString(), end_time: null, status: "scheduled",
  livekit_token: null, livekit_url: null, room: null, admission: null,
};
const LIVE = { ...BASE, status: "live", not_started: false, livekit_token: "lk", livekit_url: "wss://example", room: "event_1", admission: "admitted" };

function renderWatch() {
  return render(
    <ThemeProvider>
      <MemoryRouter initialEntries={[`/events/${EVENT_ID}/watch`]}>
        <Routes>
          <Route path="/events/:eventId/watch" element={<EventWatch />} />
        </Routes>
      </MemoryRouter>
    </ThemeProvider>
  );
}

function stubSpeech() {
  const spoken = [];
  class Utterance {
    constructor(text) { this.text = text; this.lang = ""; }
  }
  vi.stubGlobal("SpeechSynthesisUtterance", Utterance);
  vi.stubGlobal("speechSynthesis", {
    speak: (u) => spoken.push({ text: u.text, lang: u.lang }),
    cancel: vi.fn(),
    getVoices: () => [],
  });
  return spoken;
}

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  sessionStorage.clear();
  document.documentElement.setAttribute("lang", "en");
  lk.state = {
    connected: true, reconnecting: false, hasVideo: true, hasAudio: true, error: null,
    videoLayers: [], hasVideoPublication: true, quality: "auto", selectQuality: vi.fn(),
    micOn: false, micError: null, toggleMic: vi.fn(), enableMic: vi.fn(), micLive: false,
    captions: { available: false, text: "" },
  };
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("language", () => {
  it("starts in the browser's language", async () => {
    vi.spyOn(window.navigator, "languages", "get").mockReturnValue(["es-ES", "en"]);
    vi.mocked(api.get).mockResolvedValue({ data: BASE });
    renderWatch();
    expect(await screen.findByRole("heading", { name: "El servicio aún no ha comenzado" })).toBeInTheDocument();
    expect(screen.getByText("El servicio comenzará aquí automáticamente.")).toBeInTheDocument();
    expect(document.documentElement.getAttribute("lang")).toBe("es");
  });

  it("falls back to English for a language it does not have", () => {
    expect(detectLanguage(["ja-JP", "ko"])).toBe("en");
    expect(detectLanguage(["pt-BR"])).toBe("pt");
  });

  it("remembers a manual choice, which then wins over the browser", async () => {
    vi.mocked(api.get).mockResolvedValue({ data: BASE });
    const u = userEvent.setup();
    const { unmount } = renderWatch();
    await screen.findByTestId("state-pre-event");
    const [picker] = screen.getAllByLabelText("Language");
    await u.selectOptions(picker, "fr");
    expect(await screen.findByRole("heading", { name: "Le service n'a pas encore commencé" })).toBeInTheDocument();
    expect(localStorage.getItem(LANGUAGE_STORAGE_KEY)).toBe("fr");
    unmount();
    renderWatch();
    expect(await screen.findByRole("heading", { name: "Le service n'a pas encore commencé" })).toBeInTheDocument();
  });

  it("names every language in its own script", async () => {
    vi.mocked(api.get).mockResolvedValue({ data: BASE });
    renderWatch();
    await screen.findByTestId("state-pre-event");
    const [picker] = screen.getAllByLabelText("Language");
    const names = within(picker).getAllByRole("option").map((o) => o.textContent);
    expect(names).toEqual(["English", "Español", "Français", "Deutsch", "Português", "हिन्दी"]);
  });

  it("has every message — which is also its spoken text — in every language", () => {
    const keys = Object.keys(
      LANGUAGES.reduce((acc, { code }) => ({ ...acc, [code]: true }), {})
    );
    expect(keys).toHaveLength(6);
    const english = ["preEventTitle", "preEventBody", "paused", "reconnecting", "replayProcessing",
                     "replayExpired", "capacityTitle", "capacityBody", "errorTitle", "errorBody",
                     "windowClosedTitle", "livePlaying", "registerPrompt", "readAloud", "inAppTitle"];
    for (const { code } of LANGUAGES) {
      for (const key of english) {
        const text = translate(code, key);
        expect(text, `${code}.${key}`).not.toBe(key);
        if (code !== "en") expect(text, `${code}.${key}`).not.toBe(translate("en", key));
      }
    }
  });
});

describe("audio assist", () => {
  it("reads the instruction on screen aloud, in the chosen language", async () => {
    const spoken = stubSpeech();
    vi.mocked(api.get).mockResolvedValue({ data: BASE });
    const u = userEvent.setup();
    renderWatch();
    await screen.findByTestId("state-pre-event");
    const [button] = screen.getAllByTestId("audio-assist");
    await u.click(button);
    expect(spoken.at(-1)).toEqual({
      text: "The service hasn't started yet. Service will begin here automatically.", lang: "en",
    });
    expect(button).toHaveAttribute("aria-pressed", "true");
    await u.selectOptions(screen.getAllByLabelText("Language")[0], "hi");
    await u.click(screen.getAllByTestId("audio-assist")[0]);
    expect(spoken.at(-1)).toEqual({ text: "सेवा अभी शुरू नहीं हुई है. सेवा यहीं अपने आप शुरू हो जाएगी।", lang: "hi" });
  });

  it("speaks the paused instruction while the service is paused", async () => {
    const spoken = stubSpeech();
    vi.mocked(api.get).mockResolvedValue({ data: { ...LIVE, status: "degraded", media_status: "reconnecting" } });
    const u = userEvent.setup();
    renderWatch();
    await screen.findByTestId("player-status-notice");
    await u.click(screen.getAllByTestId("audio-assist")[0]);
    expect(spoken.at(-1).text).toBe("The service is paused. Please stay on this page.");
  });

  it("is not offered where the browser cannot speak", async () => {
    vi.mocked(api.get).mockResolvedValue({ data: BASE });
    renderWatch();
    await screen.findByTestId("state-pre-event");
    expect(screen.queryByTestId("audio-assist")).toBeNull();
  });
});

describe("captions", () => {
  it("puts the caption control on the player itself when the stream carries captions", async () => {
    lk.state = { ...lk.state, captions: { available: true, text: "Welcome, everyone." } };
    vi.mocked(api.get).mockResolvedValue({ data: LIVE });
    const u = userEvent.setup();
    renderWatch();
    const cc = await screen.findByRole("button", { name: "Turn captions off" });
    expect(cc).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByTestId("live-captions")).toHaveTextContent("Welcome, everyone.");
    // At player level — the settings menu was never opened.
    expect(screen.queryByText("Playback speed")).toBeNull();
    await u.click(cc);
    expect(screen.queryByTestId("live-captions")).toBeNull();
  });

  it("offers no caption control when there are no captions", async () => {
    vi.mocked(api.get).mockResolvedValue({ data: LIVE });
    renderWatch();
    await waitFor(() => expect(screen.getAllByText("LIVE").length).toBeGreaterThan(0));
    expect(screen.queryByRole("button", { name: /captions/ })).toBeNull();
  });
});

describe("targets, focus and names", () => {
  const big = (el) => {
    const c = el.className || "";
    return /\b(h-12|min-h-12)\b/.test(c) && /\b(w-12|min-w-12|w-full|px-\d)\b/.test(c);
  };

  it("gives every critical player and page control a 48×48 target", async () => {
    stubSpeech();
    vi.mocked(api.get).mockResolvedValue({ data: LIVE });
    renderWatch();
    await waitFor(() => expect(screen.getAllByText("LIVE").length).toBeGreaterThan(0));
    const critical = [
      ...screen.getAllByRole("button", { name: /^(Play|Pause)$/ }).filter((b) => b.className.includes("h-12")),
      screen.getByRole("button", { name: /^(Mute|Unmute)$/ }),
      screen.getByRole("button", { name: "Settings" }),
      screen.getByRole("button", { name: /Fullscreen/ }),
      screen.getByRole("button", { name: "Leave Event" }),
      screen.getByRole("button", { name: /Switch to (light|dark) theme/ }),
      ...screen.getAllByTestId("audio-assist"),
      ...screen.getAllByLabelText("Language"),
      screen.getByRole("button", { name: "Raise your hand" }),
      ...["Like", "Love"].map((name) => screen.getByRole("button", { name })),
    ];
    for (const el of critical) expect(big(el), el.outerHTML.slice(0, 120)).toBe(true);
  });

  it("gives the player controls a visible focus ring", async () => {
    vi.mocked(api.get).mockResolvedValue({ data: LIVE });
    renderWatch();
    await waitFor(() => expect(screen.getAllByText("LIVE").length).toBeGreaterThan(0));
    expect(screen.getByRole("button", { name: "Settings" }).className).toMatch(/focus-visible:ring-2/);
  });

  it("names the registration field for screen readers", () => {
    render(<RegistrationGate eventId="e1" eventTitle="Service" onRegistered={vi.fn()} />);
    expect(screen.getByLabelText("Full name")).toHaveAttribute("placeholder", "Enter your name");
  });

  it("announces state changes with an icon AND text, never colour alone", async () => {
    vi.mocked(api.get).mockResolvedValue({ data: BASE });
    renderWatch();
    const card = await screen.findByTestId("state-pre-event");
    expect(card).toHaveAttribute("aria-live", "polite");
    expect(card).toHaveAccessibleName("The service hasn't started yet");
    expect(card.querySelector("svg")).not.toBeNull();
  });
});
