// The two Android behaviours a WebView does not get for free, and the one place in the
// mobile build where UNTRUSTED INPUT reaches the router.
//
// ── THE SECURITY CASE ────────────────────────────────────────────────────────────────────
// An Android App Link is not a message from our own server. Any app on the device can fire an
// intent at this one carrying any URL it likes, and the OS will deliver it. NativeShell's
// handler takes what arrives and hands it to navigate() — so without the origin check, a
// hostile app could aim our own shell at an arbitrary path, and `navigate("https://...")`
// with a foreign absolute URL is an open redirect inside a WebView that holds a live session.
//
// The tests below therefore care as much about what is IGNORED as about what works: a link
// from the wrong origin, a scheme that is not ours, and a string that is not a URL at all
// must each leave the router exactly where it was.
//
// ── THE BACK BUTTON ──────────────────────────────────────────────────────────────────────
// Android's back is a system-level promise: go back, and at the start of the stack leave the
// app. A WebView with no handler does neither, which reads as a frozen app. The decision is
// made from React Router's own history index rather than Capacitor's `canGoBack` — see the
// comment in NativeShell.jsx for why those two disagree in the case that matters.
import { act, render, waitFor } from "@testing-library/react";
import { BrowserRouter, MemoryRouter, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// The bridge is the whole boundary with the native side, so it is the whole mock. The real
// module reads window.Capacitor and no-ops without it, which would make every test below
// vacuously pass.
const listeners = { backButton: null, appUrlOpen: null };
const exitApp = vi.fn();
let launch = null;

vi.mock("./bridge", () => ({
  exitApp: (...args) => exitApp(...args),
  launchUrl: () => Promise.resolve(launch),
  onBackButton: (handler) => {
    listeners.backButton = handler;
    return () => { listeners.backButton = null; };
  },
  onAppUrlOpen: (handler) => {
    listeners.appUrlOpen = handler;
    return () => { listeners.appUrlOpen = null; };
  },
  openExternal: vi.fn(),
}));

vi.mock("../platform", () => ({
  IS_NATIVE: true,
  HAS_CONSOLES: false,
  SUPPORTS_SCREEN_SHARE: false,
  HAS_BRIDGE: () => true,
  WEB_APP_URL: "https://get.zoikostream.com",
}));

import NativeShell from "./NativeShell";
import { dismissTop, register } from "../ui/dismissStack";

function Probe() {
  const location = useLocation();
  return <div data-testid="path">{`${location.pathname}${location.search}`}</div>;
}

const mount = (initial = "/events/mine") =>
  render(
    <MemoryRouter initialEntries={[initial]}>
      <NativeShell />
      <Routes>
        <Route path="*" element={<Probe />} />
      </Routes>
    </MemoryRouter>,
  );

/** Deliver an App Link and let React settle, so nothing asserts against a half-applied update. */
const deliver = (url) => act(async () => { listeners.appUrlOpen({ url }); });

/** One macrotask, for the cases that assert nothing HAPPENED. */
const settle = () => act(async () => { await new Promise((r) => setTimeout(r, 0)); });

beforeEach(() => {
  listeners.backButton = null;
  listeners.appUrlOpen = null;
  launch = null;
  exitApp.mockClear();
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("deep links", () => {
  it("navigates to the path of a link on our own origin", async () => {
    const { getByTestId } = mount();

    await waitFor(() => expect(listeners.appUrlOpen).toBeTypeOf("function"));
    await deliver("https://get.zoikostream.com/events/abc123/watch");

    await waitFor(() => expect(getByTestId("path")).toHaveTextContent("/events/abc123/watch"));
  });

  it("keeps the query string, which is where the event id and the invite token live", async () => {
    const { getByTestId } = mount();

    await waitFor(() => expect(listeners.appUrlOpen).toBeTypeOf("function"));
    await deliver("https://get.zoikostream.com/host/dashboard?event=42");

    await waitFor(() => expect(getByTestId("path")).toHaveTextContent("/host/dashboard?event=42"));
  });

  it("IGNORES a link from another origin", async () => {
    // The open-redirect case. Any app on the device can send this intent.
    const { getByTestId } = mount("/events/mine");

    await waitFor(() => expect(listeners.appUrlOpen).toBeTypeOf("function"));
    await deliver("https://attacker.example/events/abc/watch");

    await settle();
    expect(getByTestId("path")).toHaveTextContent("/events/mine");
  });

  it("IGNORES a link that borrows our host under a different scheme", async () => {
    // `origin` comparison rather than `hostname` is what catches this: http:// and https://
    // on the same host are different origins, and honouring the first would accept a link
    // delivered over cleartext.
    const { getByTestId } = mount("/events/mine");

    await waitFor(() => expect(listeners.appUrlOpen).toBeTypeOf("function"));
    await deliver("http://get.zoikostream.com/admin/dashboard");

    await settle();
    expect(getByTestId("path")).toHaveTextContent("/events/mine");
  });

  it("IGNORES a host that merely ends with ours", async () => {
    const { getByTestId } = mount("/events/mine");

    await waitFor(() => expect(listeners.appUrlOpen).toBeTypeOf("function"));
    await deliver("https://evil-get.zoikostream.com.attacker.example/e/1");

    await settle();
    expect(getByTestId("path")).toHaveTextContent("/events/mine");
  });

  it("survives a payload that is not a URL at all", async () => {
    const { getByTestId } = mount("/events/mine");

    await waitFor(() => expect(listeners.appUrlOpen).toBeTypeOf("function"));
    await expect(deliver("not a url")).resolves.not.toThrow();

    await settle();
    expect(getByTestId("path")).toHaveTextContent("/events/mine");
  });

  it("honours the link that cold-started the app", async () => {
    // Arrives before any listener could exist, so it has to be asked for rather than waited
    // on — a launch link that is dropped is an invitation that opens the wrong screen.
    launch = "https://get.zoikostream.com/e/spring-summit";
    const { getByTestId } = mount("/");

    await waitFor(() => expect(getByTestId("path")).toHaveTextContent("/e/spring-summit"));
  });

  it("ignores a foreign cold-start link too", async () => {
    launch = "https://attacker.example/e/spring-summit";
    const { getByTestId } = mount("/events/mine");

    await settle();
    expect(getByTestId("path")).toHaveTextContent("/events/mine");
  });
});

describe("the back button", () => {
  // BrowserRouter, not MemoryRouter, and that is the point rather than a detail.
  //
  // NativeShell decides between "go back" and "leave the app" by reading
  // window.history.state.idx — the position within the ROUTER's own stack, which BrowserRouter
  // maintains and MemoryRouter (which keeps its stack in memory, as the name says) does not.
  // Testing this against MemoryRouter would read `undefined` on every entry, take the exit
  // branch every time, and report a green suite for a back button that always quit the app.
  function Pusher() {
    const navigate = useNavigate();
    return <button type="button" onClick={() => navigate("/e/summit")}>go</button>;
  }

  const mountBrowser = () => {
    window.history.replaceState(null, "", "/events/mine");
    return render(
      <BrowserRouter>
        <NativeShell />
        <Pusher />
        <Routes>
          <Route path="*" element={<Probe />} />
        </Routes>
      </BrowserRouter>,
    );
  };

  it("exits the app at the start of the stack", async () => {
    // idx 0 means "this is where the app started". Back from there has nowhere to go, and
    // Android's contract says close the app rather than sit there doing nothing — which is
    // what an unhandled back in a WebView looks like, and reads as a frozen app.
    mountBrowser();

    await waitFor(() => expect(listeners.backButton).toBeTypeOf("function"));
    expect(window.history.state?.idx).toBe(0);

    listeners.backButton({ canGoBack: false });

    expect(exitApp).toHaveBeenCalledTimes(1);
  });

  it("goes back rather than exiting once there is history to go back to", async () => {
    const { getByText, getByTestId } = mountBrowser();

    await waitFor(() => expect(listeners.backButton).toBeTypeOf("function"));
    await act(async () => { getByText("go").click(); });
    await waitFor(() => expect(getByTestId("path")).toHaveTextContent("/e/summit"));
    expect(window.history.state?.idx).toBe(1);

    await act(async () => { listeners.backButton({ canGoBack: true }); });

    await waitFor(() => expect(getByTestId("path")).toHaveTextContent("/events/mine"));
    expect(exitApp).not.toHaveBeenCalled();
  });

  it("ignores Capacitor's own canGoBack and trusts the router's index", async () => {
    // The two disagree in the case that matters. canGoBack reports the WEBVIEW's history,
    // which still counts entries from before a logout wiped the session — honouring it walks
    // a signed-out user backwards into pages their session can no longer load. At idx 0 the
    // answer is "leave", whatever the WebView thinks it remembers.
    mountBrowser();

    await waitFor(() => expect(listeners.backButton).toBeTypeOf("function"));
    listeners.backButton({ canGoBack: true });

    expect(exitApp).toHaveBeenCalledTimes(1);
  });
});

describe("the back button, with a layer open", () => {
  // The sidebar, a modal and a confirm dialog are React state, not history entries. Nothing
  // in Android or the router knows they exist, so Back navigates the page BEHIND them and
  // leaves them on screen — the user sees the thing they tried to close still there, over
  // content that silently changed. This is the most-reported way a WebView app feels broken,
  // and it became reachable here the moment the organization console started shipping: its
  // sidebar is the app's most-used control.
  function Pusher() {
    const navigate = useNavigate();
    return <button type="button" onClick={() => navigate("/e/summit")}>go</button>;
  }

  const mountBrowser = () => {
    window.history.replaceState(null, "", "/events/mine");
    return render(
      <BrowserRouter>
        <NativeShell />
        <Pusher />
        <Routes>
          <Route path="*" element={<Probe />} />
        </Routes>
      </BrowserRouter>,
    );
  };

  // Layers are module state; anything a test leaves open is visible to the next one.
  beforeEach(() => { while (dismissTop()) { /* drain */ } });

  it("closes the layer instead of leaving the app", async () => {
    mountBrowser();
    await waitFor(() => expect(listeners.backButton).toBeTypeOf("function"));

    const close = vi.fn();
    register(close);

    // At idx 0, so without a layer open this press would have quit the app. That is what
    // makes it the right press to assert on: the layer has to win over the exit branch too,
    // not just over navigation.
    listeners.backButton({ canGoBack: false });

    expect(close).toHaveBeenCalledTimes(1);
    expect(exitApp).not.toHaveBeenCalled();
  });

  it("does not navigate the page underneath", async () => {
    const { getByText, getByTestId } = mountBrowser();
    await waitFor(() => expect(listeners.backButton).toBeTypeOf("function"));
    await act(async () => { getByText("go").click(); });
    await waitFor(() => expect(getByTestId("path")).toHaveTextContent("/e/summit"));

    const close = vi.fn();
    register(close);

    await act(async () => { listeners.backButton({ canGoBack: true }); });

    // The route is unchanged: the press was spent on the layer.
    expect(getByTestId("path")).toHaveTextContent("/e/summit");
    expect(close).toHaveBeenCalledTimes(1);
  });

  it("goes back again once the layer has closed", async () => {
    const { getByText, getByTestId } = mountBrowser();
    await waitFor(() => expect(listeners.backButton).toBeTypeOf("function"));
    await act(async () => { getByText("go").click(); });
    await waitFor(() => expect(getByTestId("path")).toHaveTextContent("/e/summit"));

    const layer = register(vi.fn());
    layer.release();

    await act(async () => { listeners.backButton({ canGoBack: true }); });

    // A closed layer must not keep swallowing presses — the failure that would leave Back
    // permanently dead after the first modal of the session.
    await waitFor(() => expect(getByTestId("path")).toHaveTextContent("/events/mine"));
  });

  it("takes one press per layer, innermost first", async () => {
    mountBrowser();
    await waitFor(() => expect(listeners.backButton).toBeTypeOf("function"));

    const drawer = vi.fn();
    const dialog = vi.fn();
    register(drawer);
    const inner = register(dialog);

    listeners.backButton({ canGoBack: false });
    expect(dialog).toHaveBeenCalledTimes(1);
    expect(drawer).not.toHaveBeenCalled();

    // A real component closes on dismissal; the stack does not unregister for it.
    inner.release();

    listeners.backButton({ canGoBack: false });
    expect(drawer).toHaveBeenCalledTimes(1);
    expect(exitApp).not.toHaveBeenCalled();
  });
});
