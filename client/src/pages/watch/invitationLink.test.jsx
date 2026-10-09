// Secure invitation links on the viewer page (ZST-SPEC-VAP-001 §5.1).
//
//   5. the secret is sent only in the explicit validation POST — never on /watch
//   6. a successful validation strips the fragment from the address bar
//   7. an invalid secret is refused, stripped, and the normal flow carries on
//   9. the existing registration screen still follows for a public event
//  10. the existing Viewer Dashboard is unchanged afterwards (tabs, player)
// plus: links sent before this change (?reg= / ?link=) still work and are stripped too.
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

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
  return {
    resolveQuality,
    default: () => ({
      mediaRef, connected: true, reconnecting: false, hasVideo: true, hasAudio: true, error: null,
      videoLayers: [], hasVideoPublication: true, quality: "auto", selectQuality: vi.fn(),
      micOn: false, micError: null, toggleMic: vi.fn(), enableMic: vi.fn(), micLive: false,
    }),
  };
});
vi.mock("../../utils/sound", () => ({ playAlertChime: vi.fn(), unlockAudio: vi.fn() }));
vi.mock("../../ui/Toast", () => ({ notify: { error: vi.fn(), success: vi.fn(), alert: vi.fn(), info: vi.fn() } }));

import api from "../../api";
import { ThemeProvider } from "../../theme/ThemeContext";
import EventWatch from "./EventWatch";
import { readInvitation, stripInvitation } from "./invitationLink";

const EVENT_ID = "0b9a8c7d-6e5f-4a3b-9c2d-1e0f9a8b7c6d";
const SECRET = "s3cr3t-link-token-AAAAAAAAAAAAAAAAAAAAAAAAAAAA";
const PASS = "p.pass-for-this-browser";
const REG = "eyJ.reg.token";
const LIVE = {
  id: EVENT_ID, title: "Service", status: "live", visibility: "private", host_name: "Vihari",
  organization_name: "Northwind", chat_enabled: true, qa_enabled: true, polls_enabled: true,
  reactions_enabled: true, raise_hand_enabled: true, registered: true, registration_required: false,
  not_started: false, expired: false, media_status: "live", livekit_token: "lk", livekit_url: "wss://example",
  room: "event_1", admission: "admitted",
};

const at = (path) => window.history.replaceState({}, "", path);
const watchCalls = () => vi.mocked(api.get).mock.calls.filter(([url]) => url.endsWith("/watch"));

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

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  sessionStorage.clear();
  vi.mocked(api.get).mockResolvedValue({ data: LIVE });
});
afterEach(() => at("/"));

describe("a #link= invitation", () => {
  it("is exchanged once, before /watch, and the secret never reaches /watch", async () => {
    at(`/events/${EVENT_ID}/watch#link=${SECRET}`);
    let releaseExchange;
    vi.mocked(api.post).mockImplementation(() => new Promise((resolve) => {
      releaseExchange = () => resolve({ data: { credential: "link", token: PASS } });
    }));
    renderWatch();
    await waitFor(() => expect(api.post).toHaveBeenCalledTimes(1));
    expect(api.post).toHaveBeenCalledWith(`/events/${EVENT_ID}/invitation`, { kind: "link", secret: SECRET });
    expect(watchCalls()).toHaveLength(0);                    // nothing goes out before the exchange
    releaseExchange();
    await waitFor(() => expect(watchCalls().length).toBeGreaterThan(0));
    for (const [, config] of watchCalls()) {
      expect(JSON.stringify(config)).not.toContain(SECRET);
      expect(config.params.link).toBe(PASS);
    }
    expect(localStorage.getItem(`zk_link_${EVENT_ID}`)).toBe(PASS);
  });

  it("is removed from the address bar once validated", async () => {
    at(`/events/${EVENT_ID}/watch?utm=keep#link=${SECRET}`);
    vi.mocked(api.post).mockResolvedValue({ data: { credential: "link", token: PASS } });
    renderWatch();
    await waitFor(() => expect(watchCalls().length).toBeGreaterThan(0));
    expect(window.location.hash).toBe("");
    expect(window.location.href).not.toContain(SECRET);
    expect(window.location.search).toBe("?utm=keep");           // unrelated parameters survive
  });

  it("leads into the unchanged Viewer Dashboard: player, chat, Q&A, polls", async () => {
    at(`/events/${EVENT_ID}/watch#link=${SECRET}`);
    vi.mocked(api.post).mockResolvedValue({ data: { credential: "link", token: PASS } });
    renderWatch();
    await waitFor(() => expect(screen.getAllByText("LIVE").length).toBeGreaterThan(0));
    for (const name of ["Chat", "Q&A", "Polls"]) {
      expect(screen.getByRole("button", { name: new RegExp(`^${name}`) })).toBeInTheDocument();
    }
  });
});

describe("a #invite= invitation", () => {
  it("becomes the registration credential the existing flow already uses", async () => {
    at(`/events/${EVENT_ID}/watch#invite=${SECRET}`);
    vi.mocked(api.post).mockResolvedValue({ data: { credential: "reg", token: REG } });
    renderWatch();
    await waitFor(() => expect(watchCalls().length).toBeGreaterThan(0));
    expect(api.post).toHaveBeenCalledWith(`/events/${EVENT_ID}/invitation`, { kind: "invite", secret: SECRET });
    expect(watchCalls().at(-1)[1].params.reg).toBe(REG);
    expect(sessionStorage.getItem(`zk_reg_${EVENT_ID}`)).toBe(REG);
    expect(window.location.hash).toBe("");
  });
});

describe("an invalid invitation", () => {
  it("is refused, stripped, and the existing registration screen follows for a public event", async () => {
    at(`/events/${EVENT_ID}/watch#invite=${SECRET}`);
    vi.mocked(api.post).mockRejectedValue(Object.assign(new Error("404"), {
      response: { status: 404, data: { detail: "This invitation link isn't valid for this event." } },
    }));
    vi.mocked(api.get).mockResolvedValue({ data: { ...LIVE, visibility: "public", registered: false, registration_required: true, livekit_token: null } });
    renderWatch();
    expect(await screen.findByText(/This invitation link isn't valid/)).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: /enter your name/i })).toBeInTheDocument();
    expect(window.location.hash).toBe("");
    // No credential at all — only the page's own client hint (web / mobile / embedded).
    expect(watchCalls().at(-1)[1]).toEqual({ params: { client: "web" } });
  });
});

describe("links sent before this change", () => {
  it("?reg= still admits the viewer, and is stripped from the address bar", async () => {
    at(`/events/${EVENT_ID}/watch?reg=${REG}`);
    renderWatch();
    await waitFor(() => expect(watchCalls().length).toBeGreaterThan(0));
    expect(watchCalls()[0][1].params.reg).toBe(REG);
    expect(api.post).not.toHaveBeenCalled();
    await waitFor(() => expect(window.location.search).toBe(""));
  });

  it("?link= is exchanged for a pass, and is stripped", async () => {
    at(`/events/${EVENT_ID}/watch?link=${SECRET}`);
    vi.mocked(api.post).mockResolvedValue({ data: { credential: "link", token: PASS } });
    renderWatch();
    await waitFor(() => expect(watchCalls().length).toBeGreaterThan(0));
    expect(watchCalls()[0][1].params.link).toBe(PASS);
    expect(window.location.search).toBe("");
  });
});

describe("the URL helpers", () => {
  it("read without changing anything, and strip only credentials", () => {
    const loc = { pathname: "/events/x/watch", search: "?a=1&reg=R&link=L", hash: "#invite=I&b=2" };
    expect(readInvitation(loc)).toEqual({ fragment: { kind: "invite", secret: "I" }, legacyReg: "R", legacyLink: "L" });
    const history = { state: { usr: 1 }, replaceState: vi.fn() };
    stripInvitation(loc, history);
    expect(history.replaceState).toHaveBeenCalledWith({ usr: 1 }, "", "/events/x/watch?a=1#b=2");
  });
});
