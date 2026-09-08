// Regression cover for the HOST half of the reaction system — the mandatory requirement:
// every viewer reaction has to become a visible floating emoji over the Producer Console's
// live video, in real time.
//
// Two layers, no socket and no LiveKit:
//
//   1. useLiveEvent's reducer + the reaction channel — the console's data layer. Proves a
//      `reaction.burst` envelope reaches the overlay and does NOT become console state
//      (which is what makes a reconnect unable to replay old reactions, and what keeps a
//      busy audience from re-rendering the whole control room).
//   2. StudioStage — that the overlay is mounted over the MONITOR, layered above the video
//      but below the countdown and menus, and that it can never swallow a producer control.
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { act, render, screen } from "@testing-library/react";

import StudioStage from "./StudioStage";
import { reducer, INITIAL_LIVE_STATE } from "../../hooks/useLiveEvent";
import { createReactionChannel } from "../../hooks/useReactionChannel";
import { REACTION_EMOJI } from "../../data/reactions";

// The exact payload server/app/services/moderation.py::_reaction_add publishes.
let seq = 0;
const burst = (reaction, over = {}) => ({
  channel: "reactions",
  type: "reaction.burst",
  data: { event_id: "event-1", reaction, id: `srv-${++seq}`, ts: 1757000000, ...over },
});

// Enough of hooks/useMediaPreview's shape for the stage to render a running preview.
const media = {
  videoRef: () => {}, actual: { width: 1280, height: 720, frameRate: 30 },
  active: true, error: null, phase: "ready", videoTrack: {},
};

const stage = (props = {}) => {
  const channel = createReactionChannel();
  const view = render(
    <StudioStage
      media={media} analytics={{ viewers: 12 }} recording={null}
      broadcast={{ status: "live" }} isPublishing camera mic
      reactionChannel={channel}
      {...props}
    />
  );
  const layer = view.getByTestId("reaction-overlay");
  return {
    ...view,
    channel,
    layer,
    /** One or more viewers react, exactly as the socket would deliver it. */
    react: (...keys) => act(() => keys.forEach((k) => channel.emit(burst(k).data))),
    items: () => Array.from(layer.querySelectorAll("[data-reaction]")),
  };
};

beforeEach(() => {
  vi.useFakeTimers();
  seq = 0;
});
afterEach(() => {
  vi.useRealTimers();
});

describe("Producer Console — the host sees viewer reactions", () => {
  it("shows nothing over the monitor until a viewer reacts", () => {
    const { items } = stage();
    expect(items()).toHaveLength(0);
  });

  it("floats ❤️ over the live video the moment a viewer sends it", () => {
    // Viewer clicks ❤️ -> host sees ❤️. This is the requirement, at the console.
    const { items, react } = stage();
    react("heart");

    expect(items()).toHaveLength(1);
    expect(items()[0].textContent).toBe(REACTION_EMOJI.heart);
  });

  it("shows every reaction from a mixed audience, each on its own", () => {
    // Viewer A 👍, viewer B ❤️, viewer C 🎉 — three independent floats, never a number.
    const { items, react, layer } = stage();
    react("like", "heart", "party");

    expect(items().map((el) => el.dataset.reaction)).toEqual(["like", "heart", "party"]);
    expect(layer.textContent).not.toMatch(/\d/);   // no count crept onto the console
  });

  it("shows ten simultaneous reactions as ten emoji", () => {
    const { items, react } = stage();
    react(...Array(10).fill("fire"));
    expect(items()).toHaveLength(10);
  });

  it("clears them again without the host doing anything", () => {
    const { items, react } = stage();
    react("clap");
    act(() => vi.advanceTimersByTime(4200));
    expect(items()).toHaveLength(0);
  });

  it("does not disturb the monitor's own video elements", () => {
    // "Do not break LiveKit video rendering": the camera and screen-share <video> nodes
    // must still be there, and still be the same nodes, after a burst of reactions.
    const { container, react } = stage();
    const videosBefore = Array.from(container.querySelectorAll("video"));
    expect(videosBefore).toHaveLength(2);

    react("like", "heart", "party", "fire", "clap");
    expect(Array.from(container.querySelectorAll("video"))).toEqual(videosBefore);
  });

  it("leaves the rest of the console chrome exactly as it was", () => {
    // "Do not modify unrelated Producer Console UI" — the on-air chip, viewer count and
    // publish banner still read the same with reactions on screen.
    const { react } = stage();
    react("heart", "like");

    expect(screen.getByText("On air")).toBeInTheDocument();
    expect(screen.getByText("12")).toBeInTheDocument();
    expect(screen.getByText(/being published to viewers/)).toBeInTheDocument();
  });

  it("renders an inert layer when the event has reactions turned off", () => {
    // Dashboard passes the channel unconditionally, but a console attached before the
    // snapshot lands has none — that must not throw on a live broadcast.
    render(
      <StudioStage media={media} analytics={null} recording={null}
                   broadcast={{ status: "preview" }} camera mic />
    );
    expect(screen.getByTestId("reaction-overlay").querySelectorAll("[data-reaction]"))
      .toHaveLength(0);
  });
});

describe("Producer Console — the overlay stays out of the producer's way", () => {
  it("lives inside the monitor, so it follows the picture into fullscreen", () => {
    // Fullscreen is a class change on the monitor node itself (it becomes `fixed inset-0`),
    // never a re-parent — so the overlay only follows the video if it is a DESCENDANT of
    // that node rather than a sibling of the stage.
    const { layer, container } = stage();
    const monitor = container.querySelector("video").parentElement;

    expect(monitor).toContainElement(layer);
  });

  it("never captures a click meant for a producer control", () => {
    const { layer, items, react } = stage();
    react("heart", "party");

    expect(layer.className).toContain("pointer-events-none");
    for (const el of items()) expect(el.className).toContain("pointer-events-none");
  });

  it("keeps the fullscreen control reachable while reactions are floating", () => {
    // The layer covers the whole monitor including this button, which sits in a lower
    // layer — it stays enabled and clickable because nothing above it takes pointer events.
    const { react, container } = stage();
    react(...Array(20).fill("like"));

    const expand = screen.getByRole("button", { name: "Expand video to fullscreen" });
    expect(expand).toBeEnabled();
    act(() => expand.click());

    // Asserted through the EFFECT, not just a label: the monitor node itself becomes
    // viewport-anchored, which is what "the click got through" actually means here. (Both
    // ways out are labelled "Exit fullscreen" — the corner toggle and the explicit button —
    // so the label alone is ambiguous.)
    const monitor = container.querySelector("video").parentElement;
    expect(monitor.className).toContain("fixed");
    expect(screen.getAllByRole("button", { name: "Exit fullscreen" }).length)
      .toBeGreaterThan(0);
    // ...and the overlay went into fullscreen with it, still inert.
    const layer = screen.getByTestId("reaction-overlay");
    expect(monitor).toContainElement(layer);
    expect(layer.className).toContain("pointer-events-none");
  });

  it("sits above the video and its scrims, but below the go-live countdown", () => {
    // "above video, below important menus/modals". The countdown is the highest thing
    // inside the monitor (ui/Overlay's modals are higher still, at z-[60]); a reaction must
    // never obscure the number the host is counting down to.
    const future = new Date(Date.now() + 5000).toISOString();
    const { layer, container } = stage({ countdownUntil: future });

    const zOf = (el) => Number(/z-\[?(\d+)\]?/.exec(el.className)?.[1] ?? 0);
    const countdown = screen.getByText("Going live in").closest("div.absolute");
    const scrim = container.querySelector(".pointer-events-none.absolute.inset-x-0.top-0");

    expect(zOf(layer)).toBeGreaterThan(zOf(scrim));
    expect(zOf(layer)).toBeLessThan(zOf(countdown));
    expect(zOf(countdown)).toBe(20);
  });

  it("cannot push the video around, because it is out of the layout", () => {
    const { layer } = stage();
    expect(layer.className).toContain("absolute");
    expect(layer.className).toContain("inset-0");
    expect(layer.className).toContain("overflow-hidden");   // clipped to the frame
  });
});

describe("useLiveEvent reducer — reactions are not console state", () => {
  const snapshot = (data = {}) => ({
    channel: "moderator", type: "snapshot",
    data: { event: { id: "event-1", status: "live" }, can_host: true, can_moderate: true,
            broadcast: { status: "live" }, messages: [{ id: "m1" }], ...data },
  });

  it("holds no reaction field, so there is nothing to replay on reconnect", () => {
    expect(INITIAL_LIVE_STATE).not.toHaveProperty("reactions");
  });

  it("returns the identical state object for a reaction envelope", () => {
    // Identity, not equality. A new object here would re-render the monitor, the deck, the
    // KPI row and the whole panel rail once per tap in the audience.
    const live = reducer(INITIAL_LIVE_STATE, snapshot());
    for (const key of ["like", "heart", "party", "fire", "clap"]) {
      expect(reducer(live, burst(key))).toBe(live);
    }
  });

  it("takes no reaction data from the connect snapshot", () => {
    // A reconnect replaces state from the snapshot. The server sends no reaction data
    // (server/test_viewer_reactions.py), and this asserts the console would ignore it even
    // from a stale deployment — so a reconnecting host never re-animates old reactions.
    const reconnected = reducer(INITIAL_LIVE_STATE, snapshot({ reactions: { like: 11 } }));
    expect(reconnected).not.toHaveProperty("reactions");
    expect(reconnected.ready).toBe(true);
  });

  it("leaves broadcast, recording, analytics and chat state alone", () => {
    // Reactions share the console's one socket with every other channel.
    let state = reducer(INITIAL_LIVE_STATE, snapshot());
    state = reducer(state, burst("heart"));
    state = reducer(state, { channel: "broadcast", type: "broadcast.update",
                             data: { status: "paused" } });
    state = reducer(state, burst("like"));
    state = reducer(state, { channel: "recording", type: "recording.update",
                             data: { id: "r1", status: "recording" } });
    state = reducer(state, { channel: "analytics", type: "analytics.tick",
                             data: { viewers: 41 } });
    state = reducer(state, burst("party"));
    state = reducer(state, { channel: "chat", type: "message.new", data: { id: "m2" } });

    expect(state.broadcast.status).toBe("paused");
    expect(state.recording.id).toBe("r1");
    expect(state.analytics.viewers).toBe(41);
    expect(state.messages.map((m) => m.id)).toEqual(["m1", "m2"]);
  });
});

describe("the reaction channel — how the envelope reaches the overlay", () => {
  it("delivers to every mounted overlay and to none after it unmounts", () => {
    // The seam useLiveEvent emits into. Subscribing twice models the viewer page's own
    // second overlay; unsubscribing is what an unmounting console does.
    const channel = createReactionChannel();
    const host = vi.fn();
    const viewer = vi.fn();

    const stopHost = channel.subscribe(host);
    channel.subscribe(viewer);
    channel.emit(burst("heart").data);
    expect(host).toHaveBeenCalledTimes(1);
    expect(viewer).toHaveBeenCalledTimes(1);
    expect(host.mock.calls[0][0].reaction).toBe("heart");

    stopHost();
    channel.emit(burst("like").data);
    expect(host).toHaveBeenCalledTimes(1);      // detached cleanly
    expect(viewer).toHaveBeenCalledTimes(2);
  });

  it("remembers nothing, so a subscriber that attaches late replays no history", () => {
    const channel = createReactionChannel();
    channel.emit(burst("heart").data);
    channel.emit(burst("like").data);

    const late = vi.fn();
    channel.subscribe(late);
    expect(late).not.toHaveBeenCalled();
  });
});
