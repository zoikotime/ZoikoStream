// Remembering a viewer across events on one device — and keeping that strictly separate
// from what actually grants access.
//
// ── THE TWO THINGS ─────────────────────────────────────────────────────────────────────
//   device profile     the viewer's name, ONE key for the whole browser (zk_viewer_profile).
//                      Saves typing. Grants nothing.
//   event credential   zk_reg_<eventId>, a server-signed token bound to ONE event.
//                      Grants access to that event and no other.
//
// The assertions that carry the most weight here are the ones about the boundary: opening a
// second event with a remembered profile still POSTs a real registration for THAT event,
// and event A's token is never presented to event B. A convenience that quietly became an
// access grant would be the serious version of this feature going wrong.
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
vi.mock("../../hooks/useLiveKitViewer", () => {
  const mediaRef = { current: null };
  return {
    default: () => ({
      mediaRef, connected: true, reconnecting: false, hasVideo: true, hasAudio: true,
      error: null, videoLayers: [], hasVideoPublication: true, quality: "auto",
      selectQuality: vi.fn(), micOn: false, micError: null, toggleMic: vi.fn(),
      enableMic: vi.fn(), micLive: false,
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

const EVENT_A = "aaaaaaaa-0000-4000-8000-000000000001";
const EVENT_B = "bbbbbbbb-0000-4000-8000-000000000002";
const TOKEN_A = "token-for-event-a";
const TOKEN_B = "token-for-event-b";
const PROFILE_KEY = "zk_viewer_profile";

const NAME = "Naveen";

const base = (id) => ({
  id, title: "TEST", status: "live", visibility: "public",
  host_name: "Vihari", organization_name: "Northwind",
  chat_enabled: true, qa_enabled: true, polls_enabled: true,
  reactions_enabled: true, raise_hand_enabled: false,
  room: `event_${id}`, livekit_token: "lk", livekit_url: "wss://example",
});

/** Each event accepts ONLY its own token — which is what the real server does, and the only
 *  way a test can tell "registered for B" apart from "carried A's token to B". */
const TOKEN_FOR = { [EVENT_A]: TOKEN_A, [EVENT_B]: TOKEN_B };

function serve() {
  vi.mocked(api.get).mockImplementation((url, config) => {
    const id = url.split("/")[2];
    if (!url.includes("/watch")) return Promise.resolve({ data: {} });
    const registered = config?.params?.reg === TOKEN_FOR[id];
    return Promise.resolve({ data: { ...base(id), registered } });
  });
  vi.mocked(api.post).mockImplementation((url) => {
    const id = url.split("/")[2];
    return Promise.resolve({ data: { token: TOKEN_FOR[id] } });
  });
}

const postsTo = (id) =>
  vi.mocked(api.post).mock.calls.filter(([url]) => url === `/events/${id}/register`);
const watchCalls = (id) =>
  vi.mocked(api.get).mock.calls.filter(([url]) => url === `/events/${id}/watch`);

function renderWatch(eventId) {
  return render(
    <ThemeProvider>
      <MemoryRouter initialEntries={[`/events/${eventId}/watch`]}>
        <Routes>
          <Route path="/events/:eventId/watch" element={<EventWatch />} />
        </Routes>
      </MemoryRouter>
    </ThemeProvider>
  );
}

const gate = () => screen.queryByRole("heading", { name: /enter your name/i });
const nameField = () => screen.getByPlaceholderText(/enter your name/i);
const rememberBox = () => screen.getByRole("checkbox", { name: /remember me/i });
const continueButton = () => screen.queryByRole("button", { name: /^continue$/i });
const changeDetails = () => screen.getByRole("button", { name: /not you\? change details/i });
const savedProfile = () => JSON.parse(localStorage.getItem(PROFILE_KEY) || "null");

const awaitGate = () => screen.findByRole("heading", { name: /enter your name/i });

async function registerFresh(u, { remember = true, name = NAME } = {}) {
  await u.type(nameField(), name);
  if (remember) await u.click(rememberBox());
  await u.click(screen.getByRole("button", { name: /continue to watch/i }));
}

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  sessionStorage.clear();
  serve();
});

// ── 1. the first registration remembers the viewer ─────────────────────────────────────

describe("registering for the first event", () => {
  it("saves the name when Remember me is ticked", async () => {
    const u = userEvent.setup();
    renderWatch(EVENT_A);
    await awaitGate();

    await registerFresh(u);

    await waitFor(() => expect(savedProfile()).toEqual({ name: NAME }));
  });

  it("saves nothing when Remember me is left unticked", async () => {
    const u = userEvent.setup();
    renderWatch(EVENT_A);
    await awaitGate();

    await registerFresh(u, { remember: false });

    await waitFor(() => expect(gate()).not.toBeInTheDocument());
    expect(localStorage.getItem(PROFILE_KEY)).toBeNull();
  });

  it("saves nothing when the registration fails", async () => {
    const u = userEvent.setup();
    vi.mocked(api.post).mockRejectedValue({
      response: { status: 400, data: { detail: "Not allowed." } },
    });
    renderWatch(EVENT_A);
    await awaitGate();

    await registerFresh(u);

    expect(await screen.findByText(/not allowed/i)).toBeInTheDocument();
    expect(localStorage.getItem(PROFILE_KEY)).toBeNull();
  });

  it("stores only a name — never a token", async () => {
    const u = userEvent.setup();
    renderWatch(EVENT_A);
    await awaitGate();

    await registerFresh(u);

    await waitFor(() => expect(savedProfile()).not.toBeNull());
    expect(Object.keys(savedProfile()).sort()).toEqual(["name"]);
    expect(JSON.stringify(savedProfile())).not.toContain(TOKEN_A);
  });
});

// ── 2 & 7. the next event greets them, and one tap gets them in ────────────────────────

describe("opening a second event on the same device", () => {
  beforeEach(() => {
    localStorage.setItem(PROFILE_KEY, JSON.stringify({ name: NAME }));
  });

  it("offers Continue as {name}, not an empty form", async () => {
    renderWatch(EVENT_B);
    await awaitGate();

    expect(screen.getByText(`Continue as ${NAME}`)).toBeInTheDocument();
    // The card shows who we think you are; there is no address left to confirm.
    expect(screen.getByText(new RegExp(`Continue as ${NAME}`, "i"))).toBeInTheDocument();
    expect(screen.queryByPlaceholderText(/enter your name/i)).not.toBeInTheDocument();
  });

  it("offers a way out of it", async () => {
    renderWatch(EVENT_B);
    await awaitGate();
    expect(changeDetails()).toBeInTheDocument();
  });

  it("does NOT count them as already registered", async () => {
    // The whole point of keeping profile and credential apart: a remembered name is not
    // admission. The gate is still up and nothing has been posted.
    renderWatch(EVENT_B);
    await awaitGate();

    expect(gate()).toBeInTheDocument();
    expect(postsTo(EVENT_B)).toHaveLength(0);
  });

  it("enters the event immediately on Continue, with no second click", async () => {
    const u = userEvent.setup();
    renderWatch(EVENT_B);
    await awaitGate();

    await u.click(continueButton());

    await waitFor(() => expect(gate()).not.toBeInTheDocument());
  });
});

// ── 3. exactly one registration, for the event actually being opened ───────────────────

describe("what Continue sends", () => {
  beforeEach(() => {
    localStorage.setItem(PROFILE_KEY, JSON.stringify({ name: NAME }));
  });

  it("makes exactly one registration request, for the current event", async () => {
    const u = userEvent.setup();
    renderWatch(EVENT_B);
    await awaitGate();

    await u.click(continueButton());

    await waitFor(() => expect(postsTo(EVENT_B)).toHaveLength(1));
    expect(postsTo(EVENT_B)[0][1]).toEqual({ name: NAME });
    expect(postsTo(EVENT_A)).toHaveLength(0);
  });

  it("ignores repeated taps while the request is in flight", async () => {
    const u = userEvent.setup();
    let release;
    vi.mocked(api.post).mockImplementation(() => new Promise((r) => { release = r; }));
    renderWatch(EVENT_B);
    await awaitGate();

    await u.click(continueButton());
    await u.click(continueButton());
    await u.click(continueButton());

    expect(postsTo(EVENT_B)).toHaveLength(1);
    release({ data: { token: TOKEN_B } });
  });

  it("still honours a capacity refusal", async () => {
    const u = userEvent.setup();
    vi.mocked(api.post).mockRejectedValue({ response: { status: 409 } });
    renderWatch(EVENT_B);
    await awaitGate();

    await u.click(continueButton());

    expect(await screen.findByText(/this event is full/i)).toBeInTheDocument();
  });
});

// ── 4. a credential never crosses from one event to another ────────────────────────────

describe("the boundary between events", () => {
  it("never presents event A's token to event B", async () => {
    const u = userEvent.setup();
    // A viewer fully set up on event A: remembered profile AND a live credential for A.
    localStorage.setItem(PROFILE_KEY, JSON.stringify({ name: NAME }));
    localStorage.setItem(`zk_reg_${EVENT_A}`, TOKEN_A);

    renderWatch(EVENT_B);
    await awaitGate();

    // Before registering: every request for B went out without A's token…
    for (const [, config] of watchCalls(EVENT_B)) {
      expect(config?.params?.reg).not.toBe(TOKEN_A);
    }

    await u.click(continueButton());
    await waitFor(() => expect(gate()).not.toBeInTheDocument());

    // …and after registering, B is opened with B's own token, never A's.
    for (const [, config] of watchCalls(EVENT_B)) {
      expect(config?.params?.reg).not.toBe(TOKEN_A);
    }
    expect(watchCalls(EVENT_B).some(([, c]) => c?.params?.reg === TOKEN_B)).toBe(true);
  });

  it("keeps each event's credential under its own key", async () => {
    const u = userEvent.setup();
    localStorage.setItem(PROFILE_KEY, JSON.stringify({ name: NAME }));
    localStorage.setItem(`zk_reg_${EVENT_A}`, TOKEN_A);

    renderWatch(EVENT_B);
    await awaitGate();
    await u.click(continueButton());

    await waitFor(() => expect(localStorage.getItem(`zk_reg_${EVENT_B}`)).toBe(TOKEN_B));
    // A's credential is neither reused nor disturbed.
    expect(localStorage.getItem(`zk_reg_${EVENT_A}`)).toBe(TOKEN_A);
  });

  it("does not let a remembered profile alone open an event", async () => {
    // No credential for B anywhere — only the convenience profile. The server is asked, and
    // its "registered: false" is what the page acts on.
    localStorage.setItem(PROFILE_KEY, JSON.stringify({ name: NAME }));

    renderWatch(EVENT_B);

    expect(await awaitGate()).toBeInTheDocument();
    expect(localStorage.getItem(`zk_reg_${EVENT_B}`)).toBeNull();
  });
});

// ── 5. change details ──────────────────────────────────────────────────────────────────

describe("Not you? Change details", () => {
  beforeEach(() => {
    localStorage.setItem(PROFILE_KEY, JSON.stringify({ name: NAME }));
  });

  it("reveals the editable form, pre-filled with the saved details", async () => {
    const u = userEvent.setup();
    renderWatch(EVENT_B);
    await awaitGate();

    await u.click(changeDetails());

    expect(nameField()).toHaveValue(NAME);
    expect(continueButton()).not.toBeInTheDocument();
  });

  it("forgets the old details immediately, so an abandoned visit leaves nothing behind", async () => {
    const u = userEvent.setup();
    renderWatch(EVENT_B);
    await awaitGate();

    await u.click(changeDetails());

    expect(localStorage.getItem(PROFILE_KEY)).toBeNull();
  });

  it("registers the NEW details and remembers those instead", async () => {
    const u = userEvent.setup();
    renderWatch(EVENT_B);
    await awaitGate();

    await u.click(changeDetails());
    await u.clear(nameField());
    await u.type(nameField(), "Asha");
    await u.click(screen.getByRole("button", { name: /continue to watch/i }));

    await waitFor(() => expect(gate()).not.toBeInTheDocument());
    expect(postsTo(EVENT_B)).toHaveLength(1);
    expect(postsTo(EVENT_B)[0][1]).toEqual({ name: "Asha" });
    expect(savedProfile()).toEqual({ name: "Asha" });
  });

  it("leaves nothing remembered if Remember me is turned off", async () => {
    // The consent is revocable: the checkbox starts on for a returning viewer, and
    // unticking it is how they stop being remembered.
    const u = userEvent.setup();
    renderWatch(EVENT_B);
    await awaitGate();

    await u.click(changeDetails());
    expect(rememberBox()).toBeChecked();
    await u.click(rememberBox());
    await u.click(screen.getByRole("button", { name: /continue to watch/i }));

    await waitFor(() => expect(gate()).not.toBeInTheDocument());
    expect(localStorage.getItem(PROFILE_KEY)).toBeNull();
  });

  it("keeps the existing validation", async () => {
    const u = userEvent.setup();
    renderWatch(EVENT_B);
    await awaitGate();

    await u.click(changeDetails());
    // "Not you?" clears the remembered name, so the form is empty and the button is shut
    // until a real name is typed. That is the whole of the validation now.
    await u.clear(nameField());

    expect(screen.getByRole("button", { name: /continue to watch/i })).toBeDisabled();
    expect(postsTo(EVENT_B)).toHaveLength(0);
  });
});

// ── 6. no profile means the ordinary form ──────────────────────────────────────────────

describe("a device that remembers nobody", () => {
  it("shows the normal name form", async () => {
    // What a private/incognito window gets: its own empty store, so no greeting.
    renderWatch(EVENT_B);
    await awaitGate();

    expect(nameField()).toHaveValue("");
    expect(continueButton()).not.toBeInTheDocument();
    expect(screen.queryByText(/continue as/i)).not.toBeInTheDocument();
  });

  it("leaves Remember me unticked, as it always was", async () => {
    renderWatch(EVENT_B);
    await awaitGate();
    expect(rememberBox()).not.toBeChecked();
  });

  it("falls back to the form when the stored profile is unusable", async () => {
    // A corrupt or hand-edited key must read as "nobody", never crash the gate.
    localStorage.setItem(PROFILE_KEY, "{not json");

    renderWatch(EVENT_B);
    await awaitGate();

    expect(nameField()).toBeInTheDocument();
    expect(continueButton()).not.toBeInTheDocument();
  });

  it("still greets a viewer whose profile was written before the email field was removed", async () => {
    // INVERTED DELIBERATELY. This used to assert that a profile carrying a bad address was
    // discarded wholesale. Now that the address is neither read nor written, doing that would
    // have thrown away the one field that still matters and shown an empty form to every
    // returning viewer on the day this shipped. The stale address is ignored, the name is
    // honoured, and the next save drops the address for good.
    localStorage.setItem(PROFILE_KEY, JSON.stringify({ name: NAME, email: "nope" }));

    renderWatch(EVENT_B);
    await awaitGate();

    expect(continueButton()).toBeInTheDocument();
    expect(screen.getByText(new RegExp(`Continue as ${NAME}`, "i"))).toBeInTheDocument();
    // And nothing anywhere is still showing the stale address.
    expect(screen.queryByText("nope")).not.toBeInTheDocument();
  });

  it("ignores a stored profile with no usable name", async () => {
    localStorage.setItem(PROFILE_KEY, JSON.stringify({ name: "   " }));

    renderWatch(EVENT_B);
    await awaitGate();

    expect(nameField()).toBeInTheDocument();
    expect(continueButton()).not.toBeInTheDocument();
  });
});

// ── the full journey, end to end ───────────────────────────────────────────────────────

it("registers for one event, then is greeted and admitted at the next", async () => {
  const u = userEvent.setup();

  const first = renderWatch(EVENT_A);
  await awaitGate();
  await registerFresh(u);
  await waitFor(() => expect(gate()).not.toBeInTheDocument());
  expect(postsTo(EVENT_A)).toHaveLength(1);
  first.unmount();

  renderWatch(EVENT_B);
  await awaitGate();
  expect(screen.getByText(`Continue as ${NAME}`)).toBeInTheDocument();

  await u.click(continueButton());
  await waitFor(() => expect(gate()).not.toBeInTheDocument());

  // One registration per event, each with its own credential.
  expect(postsTo(EVENT_A)).toHaveLength(1);
  expect(postsTo(EVENT_B)).toHaveLength(1);
  expect(localStorage.getItem(`zk_reg_${EVENT_A}`)).toBe(TOKEN_A);
  expect(localStorage.getItem(`zk_reg_${EVENT_B}`)).toBe(TOKEN_B);
});
