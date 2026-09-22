// Registering for an event that requires it — in ONE click.
//
// ── THE BUG ────────────────────────────────────────────────────────────────────────────
// Filling in name + email and pressing Register registered the viewer but left them looking
// at the same form. Only a SECOND press let them in — which also sent a second POST and
// created a second attendee record for one person.
//
// Two defects, both about a credential that existed but was not yet in React state:
//
//   1. onRegistered called fetchWatch(), which read `regToken` out of a closure React had
//      not updated yet (setState is not synchronous). So the request meant to prove the
//      registration went out with NO credential, and the server answered "not registered".
//
//   2. the stale-credential guard then compared the freshly issued token against the
//      anonymous `watch` payload from before it existed — which says registered=false — and
//      concluded the new token was refused, so it DELETED it from storage and from state.
//      That is why one click was not merely useless but actively undone, and why "Remember
//      me" never appeared to stick.
//
// The assertion that matters most is the pair: exactly one POST, and the player visible
// without a second click.
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../api", () => ({
  default: { get: vi.fn(), post: vi.fn() },
  errMsg: (e, fallback) => e?.response?.data?.detail || e?.message || fallback || "Error",
}));
vi.mock("../../auth/AuthContext", () => ({ useAuth: () => ({ user: null }) }));
vi.mock("../../hooks/useEventStream", () => ({
  default: () => ({ status: "open", closeReason: null, latency: 12, attempt: 0,
                    send: vi.fn(() => true), disconnect: vi.fn() }),
}));
vi.mock("../../hooks/useKeepAwake", () => ({ default: () => {} }));
// Shape matches the real hook's return (hooks/useLiveKitViewer.js) — VideoPlayer attaches
// mediaRef to its <video>, so a stand-in has to supply a real ref object.
vi.mock("../../hooks/useLiveKitViewer", () => {
  // A plain object, not useRef: React accepts one as a ref, and a hook call inside a
  // non-component factory is not a legal hook call.
  const mediaRef = { current: null };
  return {
    default: () => ({
      mediaRef, connected: true, reconnecting: false,
      hasVideo: true, hasAudio: true, error: null,
      videoLayers: [], hasVideoPublication: true, quality: "auto", selectQuality: vi.fn(),
      micOn: false, micError: null, toggleMic: vi.fn(), enableMic: vi.fn(), micLive: false,
    }),
  };
});
vi.mock("../../utils/sound", () => ({ playAlertChime: vi.fn(), unlockAudio: vi.fn() }));
vi.mock("../../ui/Toast", () => ({
  notify: { error: vi.fn(), success: vi.fn(), alert: vi.fn() },
}));

import api from "../../api";
import { ThemeProvider } from "../../theme/ThemeContext";
import EventWatch from "./EventWatch";

const EVENT_ID = "45127972-6aaf-4d1a-adb5-17602dbfac16";
const TOKEN = "reg-token-abc123";

const base = {
  id: EVENT_ID, title: "TEST", status: "live", visibility: "public",
  host_name: "Vihari", organization_name: "Northwind",
  chat_enabled: true, qa_enabled: true, polls_enabled: true,
  reactions_enabled: true, raise_hand_enabled: false,
  room: "event_1", livekit_token: "lk", livekit_url: "wss://example",
};
// What the server returns to a visitor it does not recognise…
const ANONYMOUS = { ...base, registered: false };
// …and to one presenting a valid registration credential.
const REGISTERED = { ...base, registered: true };

/** Answers /watch based on whether the request actually carried a credential — which is the
 *  behaviour the first defect hid, so the test can only catch it by modelling it. */
function serveWatch() {
  vi.mocked(api.get).mockImplementation((url, config) => {
    if (!url.includes("/watch")) return Promise.resolve({ data: {} });
    const reg = config?.params?.reg;
    return Promise.resolve({ data: reg === TOKEN ? REGISTERED : ANONYMOUS });
  });
}

const registerCalls = () =>
  vi.mocked(api.post).mock.calls.filter(([url]) => url === `/events/${EVENT_ID}/register`);

const watchCallsWithToken = () =>
  vi.mocked(api.get).mock.calls.filter(([, config]) => config?.params?.reg === TOKEN);

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

const gate = () => screen.queryByRole("heading", { name: /registration required/i });
const registerButton = () => screen.getByRole("button", { name: /register/i });

async function fillForm(u) {
  await u.type(screen.getByPlaceholderText(/jane doe/i), "naveen");
  await u.type(screen.getByPlaceholderText(/jane@company\.com/i), "mail@gmail.com");
}

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  sessionStorage.clear();
  serveWatch();
  vi.mocked(api.post).mockResolvedValue({ data: { token: TOKEN } });
});

// ── the regression ─────────────────────────────────────────────────────────────────────

describe("one click on Register", () => {
  it("sends exactly one registration request", async () => {
    const u = userEvent.setup();
    renderWatch();
    await screen.findByRole("heading", { name: /registration required/i });

    await fillForm(u);
    await u.click(registerButton());

    await waitFor(() => expect(registerCalls()).toHaveLength(1));
    expect(registerCalls()[0][1]).toEqual({ name: "naveen", email: "mail@gmail.com" });
  });

  it("admits the viewer immediately — no second click", async () => {
    const u = userEvent.setup();
    renderWatch();
    await screen.findByRole("heading", { name: /registration required/i });

    await fillForm(u);
    await u.click(registerButton());

    await waitFor(() => expect(gate()).not.toBeInTheDocument());
    // Still exactly one POST: nothing retried its way in.
    expect(registerCalls()).toHaveLength(1);
  });

  it("presents the new credential on the very next /watch request", async () => {
    // The heart of defect 1: this request used to go out with no reg param at all, because
    // it read a state value React had not committed.
    const u = userEvent.setup();
    renderWatch();
    await screen.findByRole("heading", { name: /registration required/i });

    await fillForm(u);
    await u.click(registerButton());

    await waitFor(() => expect(watchCallsWithToken().length).toBeGreaterThan(0));
  });

  it("keeps the credential instead of discarding it", async () => {
    // Defect 2: the stale-credential guard deleted the token it had just been handed.
    const u = userEvent.setup();
    renderWatch();
    await screen.findByRole("heading", { name: /registration required/i });

    await fillForm(u);
    await u.click(registerButton());

    await waitFor(() => expect(gate()).not.toBeInTheDocument());
    expect(sessionStorage.getItem(`zk_reg_${EVENT_ID}`)).toBe(TOKEN);
  });
});

// ── no duplicate attendee records ──────────────────────────────────────────────────────

describe("while the request is in flight", () => {
  it("disables the button", async () => {
    const u = userEvent.setup();
    let release;
    vi.mocked(api.post).mockImplementation(() => new Promise((r) => { release = r; }));

    renderWatch();
    await screen.findByRole("heading", { name: /registration required/i });
    await fillForm(u);
    await u.click(registerButton());

    expect(registerButton()).toBeDisabled();
    expect(screen.getByRole("button", { name: /registering/i })).toBeInTheDocument();

    release({ data: { token: TOKEN } });
  });

  it("ignores extra clicks, so one person never becomes two attendees", async () => {
    const u = userEvent.setup();
    let release;
    vi.mocked(api.post).mockImplementation(() => new Promise((r) => { release = r; }));

    renderWatch();
    await screen.findByRole("heading", { name: /registration required/i });
    await fillForm(u);

    await u.click(registerButton());
    await u.click(registerButton());
    await u.click(registerButton());

    expect(registerCalls()).toHaveLength(1);
    release({ data: { token: TOKEN } });
  });
});

// ── Remember me ────────────────────────────────────────────────────────────────────────

describe("Remember me", () => {
  it("persists the credential when ticked", async () => {
    const u = userEvent.setup();
    renderWatch();
    await screen.findByRole("heading", { name: /registration required/i });

    await fillForm(u);
    await u.click(screen.getByRole("checkbox", { name: /remember me/i }));
    await u.click(registerButton());

    await waitFor(() => expect(localStorage.getItem(`zk_reg_${EVENT_ID}`)).toBe(TOKEN));
    // Never both, or an unticked re-registration would leave a persistent copy behind.
    expect(sessionStorage.getItem(`zk_reg_${EVENT_ID}`)).toBeNull();
  });

  it("keeps the credential to the session when left unticked", async () => {
    const u = userEvent.setup();
    renderWatch();
    await screen.findByRole("heading", { name: /registration required/i });

    await fillForm(u);
    await u.click(registerButton());

    await waitFor(() => expect(sessionStorage.getItem(`zk_reg_${EVENT_ID}`)).toBe(TOKEN));
    expect(localStorage.getItem(`zk_reg_${EVENT_ID}`)).toBeNull();
  });

  it("is unticked by default", async () => {
    renderWatch();
    await screen.findByRole("heading", { name: /registration required/i });
    expect(screen.getByRole("checkbox", { name: /remember me/i })).not.toBeChecked();
  });
});

// ── failure stays on the form ──────────────────────────────────────────────────────────

describe("when registration fails", () => {
  it("keeps the form up and shows the server's reason", async () => {
    const u = userEvent.setup();
    vi.mocked(api.post).mockRejectedValue({
      response: { status: 400, data: { detail: "That email is blocked for this event." } },
    });

    renderWatch();
    await screen.findByRole("heading", { name: /registration required/i });
    await fillForm(u);
    await u.click(registerButton());

    expect(await screen.findByText(/that email is blocked for this event/i)).toBeInTheDocument();
    expect(gate()).toBeInTheDocument();
  });

  it("stores no credential and lets the viewer retry", async () => {
    const u = userEvent.setup();
    vi.mocked(api.post).mockRejectedValueOnce({ response: { status: 500 }, message: "boom" });

    renderWatch();
    await screen.findByRole("heading", { name: /registration required/i });
    await fillForm(u);
    await u.click(registerButton());

    await waitFor(() => expect(registerButton()).toBeEnabled());
    expect(sessionStorage.getItem(`zk_reg_${EVENT_ID}`)).toBeNull();
    expect(localStorage.getItem(`zk_reg_${EVENT_ID}`)).toBeNull();

    vi.mocked(api.post).mockResolvedValue({ data: { token: TOKEN } });
    await u.click(registerButton());
    await waitFor(() => expect(gate()).not.toBeInTheDocument());
  });

  it("shows the capacity notice on a 409 rather than a generic error", async () => {
    const u = userEvent.setup();
    vi.mocked(api.post).mockRejectedValue({ response: { status: 409 } });

    renderWatch();
    await screen.findByRole("heading", { name: /registration required/i });
    await fillForm(u);
    await u.click(registerButton());

    expect(await screen.findByText(/this event is full/i)).toBeInTheDocument();
  });
});

// ── validation is unchanged ────────────────────────────────────────────────────────────

describe("validation", () => {
  it("keeps Register disabled until both fields are valid", async () => {
    const u = userEvent.setup();
    renderWatch();
    await screen.findByRole("heading", { name: /registration required/i });

    expect(registerButton()).toBeDisabled();

    await u.type(screen.getByPlaceholderText(/jane doe/i), "naveen");
    expect(registerButton()).toBeDisabled();

    await u.type(screen.getByPlaceholderText(/jane@company\.com/i), "not-an-email");
    expect(registerButton()).toBeDisabled();

    await u.type(screen.getByPlaceholderText(/jane@company\.com/i), "@gmail.com");
    expect(registerButton()).toBeEnabled();
    expect(registerCalls()).toHaveLength(0);
  });
});

// ── a genuinely refused credential is still cleaned up ─────────────────────────────────

describe("a saved credential the server rejects", () => {
  it("is dropped, so the next visit starts clean", async () => {
    // The behaviour the stale-credential guard exists for, which the fix narrows rather
    // than removes: here the server IS shown the token and still says not registered.
    localStorage.setItem(`zk_reg_${EVENT_ID}`, "expired-token");
    vi.mocked(api.get).mockResolvedValue({ data: ANONYMOUS });

    renderWatch();
    await screen.findByRole("heading", { name: /registration required/i });

    await waitFor(() => expect(localStorage.getItem(`zk_reg_${EVENT_ID}`)).toBeNull());
  });

  it("admits a viewer whose saved credential is still good, with no form at all", async () => {
    localStorage.setItem(`zk_reg_${EVENT_ID}`, TOKEN);

    renderWatch();

    await waitFor(() => expect(gate()).not.toBeInTheDocument());
    expect(registerCalls()).toHaveLength(0);
    expect(localStorage.getItem(`zk_reg_${EVENT_ID}`)).toBe(TOKEN);
  });
});
