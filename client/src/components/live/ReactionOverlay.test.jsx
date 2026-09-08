// Regression cover for the floating-reaction overlay — the component that satisfies the
// MANDATORY half of the feature: every viewer reaction has to become a visible emoji over
// the live video, on the host's Producer Console as well as on the viewer's own player.
//
// This is the same component on both surfaces (components/host/StudioStage.jsx mounts it
// over the monitor, pages/watch/EventWatch.jsx over the player), so what is asserted here
// holds for both: an item appears the moment an envelope arrives, several appear
// independently, each leaves the DOM on its own, and the layer never captures a click.
//
// Driven through createReactionChannel — the same seam the pages use — so no socket, no
// LiveKit and no page render is involved.
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { act, render } from "@testing-library/react";

import ReactionOverlay, { REACTION_DURATION_MS } from "./ReactionOverlay";
import { createReactionChannel } from "../../hooks/useReactionChannel";
import { REACTIONS, REACTION_EMOJI } from "../../data/reactions";

// The exact payload the server publishes (server/app/services/moderation.py::_reaction_add):
// an emoji key, the event id, a per-instance id and a timestamp. No identity, deliberately.
let seq = 0;
const burst = (reaction, over = {}) => ({
  event_id: "event-1",
  reaction,
  id: `srv-${++seq}`,
  ts: 1757000000 + seq,
  ...over,
});

const overlay = (props = {}) => {
  const channel = createReactionChannel();
  const view = render(<ReactionOverlay channel={channel} {...props} />);
  const layer = view.getByTestId("reaction-overlay");
  return {
    ...view,
    channel,
    layer,
    /** Deliver one or more reactions exactly as the socket would. */
    send: (...reactions) => act(() => reactions.forEach((r) => channel.emit(burst(r)))),
    items: () => Array.from(layer.querySelectorAll("[data-reaction]")),
  };
};

/** Let every float finish and be swept, without waiting in real time. */
const settle = () => act(() => vi.advanceTimersByTime(REACTION_DURATION_MS + 1000));

beforeEach(() => {
  vi.useFakeTimers();
  seq = 0;
});
afterEach(() => {
  vi.useRealTimers();
});

describe("ReactionOverlay — a reaction appears over the video", () => {
  it("starts empty, so nothing is painted over the video until someone reacts", () => {
    const { items } = overlay();
    expect(items()).toHaveLength(0);
  });

  it("renders the emoji as soon as a reaction envelope arrives", () => {
    // THE CORE REQUIREMENT: viewer clicks ❤️ -> this is what the host sees.
    const { items, send } = overlay();
    send("heart");

    expect(items()).toHaveLength(1);
    expect(items()[0]).toHaveAttribute("data-reaction", "heart");
    expect(items()[0].textContent).toBe(REACTION_EMOJI.heart);
  });

  it("renders the right glyph for every reaction the bar can send", () => {
    // Guards the wire contract end to end: a key the bar sends must resolve to an emoji
    // here, or the host sees nothing at all for that button.
    for (const r of REACTIONS) {
      const { items, send, unmount } = overlay();
      send(r.key);
      expect(items()[0].textContent).toBe(r.emoji);
      unmount();
    }
  });

  it("drops a reaction key it cannot resolve instead of painting `undefined`", () => {
    // A newer server, or a malformed frame. Rendering the raw key over the picture a
    // producer is monitoring would be worse than ignoring it.
    const { items, send } = overlay();
    send("rocket", "");
    expect(items()).toHaveLength(0);
  });

  it("ignores an empty emit rather than throwing on a live console", () => {
    const { items, channel } = overlay();
    act(() => {
      channel.emit(null);
      channel.emit(undefined);
      channel.emit({});
    });
    expect(items()).toHaveLength(0);
  });
});

describe("ReactionOverlay — several reactions at once", () => {
  it("shows multiple reactions independently, never merged into one", () => {
    // Viewer A 👍, viewer B ❤️, viewer C 🎉 — the host must see three floating emoji.
    const { items, send } = overlay();
    send("like", "heart", "party");

    expect(items()).toHaveLength(3);
    expect(items().map((el) => el.dataset.reaction)).toEqual(["like", "heart", "party"]);
  });

  it("keeps repeats of the SAME emoji as separate items", () => {
    // Ten viewers all sending ❤️ is ten hearts, not one heart and not a "10" badge.
    const { items, send } = overlay();
    send(...Array(10).fill("heart"));

    expect(items()).toHaveLength(10);
    // A shared key would have collapsed them into one element that never re-animates.
    expect(new Set(items().map((el) => el.id)).size).toBe(10);
  });

  it("gives every floating item its own unique DOM id", () => {
    const { items, send } = overlay();
    send("like", "like", "fire", "clap");
    const ids = items().map((el) => el.id);

    expect(new Set(ids).size).toBe(ids.length);
    expect(ids.every((id) => id.startsWith("zk-reaction-"))).toBe(true);
  });

  it("spreads simultaneous reactions out so they do not sit on top of each other", () => {
    // The Google-Meet feel: a small random horizontal offset and a small start delay, so a
    // burst reads as several people rather than one emoji rendered five times.
    const { items, send } = overlay();
    send(...Array(12).fill("party"));

    const lefts = new Set(items().map((el) => el.style.left));
    const delays = new Set(items().map((el) => el.style.animationDelay));
    expect(lefts.size).toBeGreaterThan(1);
    expect(delays.size).toBeGreaterThan(1);
    // Inset from both edges, so a wide emoji is never half off the frame.
    for (const el of items()) {
      const left = parseFloat(el.style.left);
      expect(left).toBeGreaterThanOrEqual(8);
      expect(left).toBeLessThanOrEqual(84);
    }
  });

  it("caps how many can cover the video at once, keeping the newest", () => {
    // A scripted flood is already bounded server-side; this is the second line of defence
    // so the picture a producer is monitoring can never be buried.
    const { items, send } = overlay();
    send(...Array(200).fill("fire"));

    expect(items().length).toBeLessThanOrEqual(28);
    expect(items().length).toBeGreaterThan(0);
  });
});

describe("ReactionOverlay — a reaction disappears on its own", () => {
  it("removes the item from the DOM once its animation has finished", () => {
    const { items, send } = overlay();
    send("heart");
    expect(items()).toHaveLength(1);

    settle();
    expect(items()).toHaveLength(0);
  });

  it("is still on screen part-way through the float", () => {
    // The other side of the assertion above: it must not vanish early, or the animation
    // would be cut off half-way up.
    const { items, send } = overlay();
    send("heart");

    act(() => vi.advanceTimersByTime(Math.floor(REACTION_DURATION_MS / 2)));
    expect(items()).toHaveLength(1);
  });

  it("expires each item on its own clock, not the newest one's", () => {
    const { items, send } = overlay();
    send("like");
    act(() => vi.advanceTimersByTime(REACTION_DURATION_MS - 200));
    send("heart");                                  // a second reaction arrives late

    act(() => vi.advanceTimersByTime(1000));
    // The first has gone, the second is still floating.
    expect(items().map((el) => el.dataset.reaction)).toEqual(["heart"]);

    settle();
    expect(items()).toHaveLength(0);
  });

  it("animates for a duration that matches the CSS it drives", () => {
    // The node's removal and the keyframes' fade are timed by the same constant; if they
    // drift apart, an emoji either vanishes mid-float or sits invisible over the video.
    const { items, send } = overlay();
    send("clap");

    expect(items()[0].style.animationDuration).toBe(`${REACTION_DURATION_MS}ms`);
    expect(items()[0].className).toContain("zk-reaction-float");
    // Inside the 2.5s-4s band a reaction should live for.
    expect(REACTION_DURATION_MS).toBeGreaterThanOrEqual(2500);
    expect(REACTION_DURATION_MS).toBeLessThanOrEqual(4000);
  });

  it("stops sweeping once the last reaction is gone", () => {
    // The sweep interval only runs while something is on screen, so an idle console (the
    // overwhelming majority of the time) has no timer ticking behind the video.
    const { send } = overlay();
    send("heart");
    settle();

    expect(vi.getTimerCount()).toBe(0);
  });
});

describe("ReactionOverlay — nothing is replayed, nothing is remembered", () => {
  it("shows nothing on a fresh mount, however many reactions happened before", () => {
    // A host reconnect remounts the console. There is no reaction history on the server
    // (the snapshot carries none) and none here, so the new overlay starts empty and only
    // NEW reactions animate.
    const { items, send, unmount } = overlay();
    send("like", "heart", "party", "fire");
    expect(items()).toHaveLength(4);
    unmount();

    const reconnected = overlay();
    expect(reconnected.items()).toHaveLength(0);
  });

  it("does not re-render old reactions when a new one arrives after they expired", () => {
    const { items, send } = overlay();
    send("like", "like", "like");
    settle();

    send("heart");
    expect(items().map((el) => el.dataset.reaction)).toEqual(["heart"]);
  });

  it("renders an inert layer when reactions are turned off for the event", () => {
    // EventWatch/StudioStage pass no channel when the host has reactions disabled (or for
    // a memorial event). That must be a quiet no-op, not a crash on a live console.
    const view = render(<ReactionOverlay channel={null} />);
    const layer = view.getByTestId("reaction-overlay");

    expect(layer.querySelectorAll("[data-reaction]")).toHaveLength(0);
    expect(layer.className).toContain("pointer-events-none");
  });
});

describe("ReactionOverlay — it must not get in the way", () => {
  it("never captures pointer events, on the layer or on any floating emoji", () => {
    // THE PRODUCER-CONTROLS REQUIREMENT. This layer covers the whole monitor, including
    // the fullscreen toggle and the publish-retry button underneath it.
    const { layer, items, send } = overlay();
    send("heart", "like");

    expect(layer.className).toContain("pointer-events-none");
    for (const el of items()) expect(el.className).toContain("pointer-events-none");
  });

  it("is taken out of the layout entirely, so reactions cannot move the video", () => {
    const { layer } = overlay();
    expect(layer.className).toContain("absolute");
    expect(layer.className).toContain("inset-0");
    // Clipped to the frame, so an emoji drifting sideways can never escape the player.
    expect(layer.className).toContain("overflow-hidden");
  });

  it("accepts the z-index its host surface assigns, rather than hard-coding one", () => {
    // Each surface slots the layer between its own video and its own chrome — see
    // StudioStage (z-[3], under the countdown at z-20) and EventWatch (z-30).
    const { layer } = overlay({ className: "z-[3]" });
    expect(layer.className).toContain("z-[3]");
  });

  it("shows no viewer name, email or id beside a reaction", () => {
    // Privacy requirement. The server does not send an identity, and even if a payload
    // arrived carrying one, the overlay renders the emoji and nothing else.
    const channel = createReactionChannel();
    const view = render(<ReactionOverlay channel={channel} />);
    act(() => channel.emit({
      ...burst("heart"),
      // None of these are sent by the real server — this asserts the overlay would not
      // render them even if they were.
      name: "Ada Lovelace", email: "ada@example.com", viewer_id: "user-42",
      identity: "user-42", watch_token: "tok-secret",
    }));

    const layer = view.getByTestId("reaction-overlay");
    expect(layer.textContent).toBe(REACTION_EMOJI.heart);
    for (const secret of ["Ada", "ada@example.com", "user-42", "tok-secret"]) {
      expect(layer.textContent).not.toContain(secret);
    }
  });

  it("is hidden from assistive technology, so it cannot bury chat and Q&A updates", () => {
    const { layer } = overlay();
    expect(layer).toHaveAttribute("aria-hidden", "true");
  });
});
