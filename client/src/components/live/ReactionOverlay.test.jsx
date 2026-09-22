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
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { act, render } from "@testing-library/react";

import ReactionOverlay, { REACTION_DURATION_MS, REACTION_MAX_LIFETIME_MS } from "./ReactionOverlay";
import { createReactionChannel } from "../../hooks/useReactionChannel";
import { REACTIONS, REACTION_BY_KEY } from "../../data/reactions";

// The floating item renders artwork, so identity is read off the asset rather than the
// character. `src` is the bundled URL Vite hands data/reactions.js — the same value the
// picker uses, which is what "picker and float are the same visual" means concretely.
const glyphSrc = (node) => node.querySelector("[data-reaction-glyph]")?.getAttribute("src");

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

/** Let every float finish and be swept, without waiting in real time. Driven by the
 *  overlay's own worst case (the slowest float, started at the largest delay) rather than a
 *  hard-coded figure, because the viewer's lane gives each item its own duration. */
const settle = () => act(() => vi.advanceTimersByTime(REACTION_MAX_LIFETIME_MS + 400));

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
    expect(glyphSrc(items()[0])).toBe(REACTION_BY_KEY.heart.asset);
  });

  it("renders the right glyph for every reaction the bar can send", () => {
    // Guards the wire contract end to end: a key the bar sends must resolve to an emoji
    // here, or the host sees nothing at all for that button.
    for (const r of REACTIONS) {
      const { items, send, unmount } = overlay();
      send(r.key);
      expect(glyphSrc(items()[0])).toBe(r.asset);
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

describe("ReactionOverlay — the viewer's left-hand stream (lane=\"left\")", () => {
  // The approved viewer treatment (pages/watch/EventWatch.jsx): reactions rise out of the
  // lower-left corner in a controlled column instead of scattering over the whole frame.
  // Everything asserted here is presentation — the payload, the channel and the item's
  // lifetime are the same ones covered above, and the Producer Console does NOT opt in.
  const left = (props = {}) => overlay({ lane: "left", ...props });
  /** A per-item custom property, as the keyframes in index.css read it. */
  const cssVar = (el, name) => el.style.getPropertyValue(name).trim();

  it("spawns every reaction inside the left-hand lane, never across the video", () => {
    const { items, send } = left();
    send(...Array(24).fill("heart"));

    for (const el of items()) {
      const x = parseFloat(el.style.left);
      // The lane is 3%-19% of the player, plus at most 1.2% of jitter either side.
      expect(x).toBeGreaterThanOrEqual(1.8);
      expect(x).toBeLessThanOrEqual(20.2);
    }
  });

  it("spawns near the bottom of the player, so the emoji has the frame to climb", () => {
    const { items, send } = left();
    send(...Array(16).fill("fire"));

    for (const el of items()) {
      const y = parseFloat(el.style.bottom);
      expect(y).toBeGreaterThanOrEqual(3);
      expect(y).toBeLessThanOrEqual(12);
    }
  });

  it("puts consecutive reactions well apart, so a burst cannot stack in one place", () => {
    // Ten viewers tapping at once is the case this exists for: each one has to be its own
    // visible emoji, not ten drawn on top of each other. The golden-ratio step guarantees
    // it for EVERY adjacent pair, not just on average.
    const { items, send } = left();
    send(...Array(8).fill("clap"));
    const lefts = items().map((el) => parseFloat(el.style.left));

    for (let i = 1; i < lefts.length; i += 1) expect(lefts[i]).not.toBe(lefts[i - 1]);
    expect(new Set(lefts).size).toBeGreaterThanOrEqual(4);
  });

  it("never cycles back through a small fixed set of spawn points", () => {
    // What the old `column % 4` rotation did: spread a burst evenly, then repeat the same
    // four positions forever, which is visible as a pattern past the fourth reaction. An
    // irrational step has no cycle to fall into.
    //
    // The bound is loose on purpose. Positions are rounded to 0.1%, so across a ~14%-wide
    // lane a handful of coincidental repeats in 20 draws is expected and means nothing;
    // what this has to catch is a spawn point that is one of a FEW slots, which would put
    // this number at or near 4 however many reactions arrive.
    const { items, send } = left();
    send(...Array(20).fill("heart"));
    const lefts = items().map((el) => parseFloat(el.style.left));

    expect(new Set(lefts).size).toBeGreaterThan(12);
    // And no one spot is a magnet.
    const busiest = Math.max(...lefts.map((x) => lefts.filter((y) => y === x).length));
    expect(busiest).toBeLessThanOrEqual(3);
  });

  it("spreads the burst across the lane instead of clumping at one end", () => {
    // Controlled randomness, not raw randomness: 24 reactions should touch the near edge,
    // the far edge and the middle of the lane rather than piling into one third of it.
    const { items, send } = left();
    send(...Array(24).fill("party"));
    const lefts = items().map((el) => parseFloat(el.style.left));

    expect(Math.min(...lefts)).toBeLessThan(7);
    expect(Math.max(...lefts)).toBeGreaterThan(15);
  });

  it("varies each item's path, timing, size and tilt so the stream is not a rigid line", () => {
    const { items, send } = left();
    send(...Array(12).fill("party"));

    for (const key of [
      "--zk-reaction-drift", "--zk-reaction-sway", "--zk-reaction-scale",
      "--zk-reaction-rot", "--zk-reaction-travel",
    ]) {
      expect(new Set(items().map((el) => cssVar(el, key))).size).toBeGreaterThan(1);
    }
    expect(new Set(items().map((el) => el.style.animationDelay)).size).toBeGreaterThan(1);
  });

  it("gives each emoji its own climb speed, so a burst never moves in lockstep", () => {
    // The single biggest difference between "a crowd reacting" and "one animation played
    // twelve times": at a flat duration every emoji rises at exactly the same rate.
    const { items, send } = left();
    send(...Array(12).fill("like"));
    const durations = items().map((el) => parseFloat(el.style.animationDuration));

    expect(new Set(durations).size).toBeGreaterThan(1);
    // Bounded, and inside the 2.5s-4s band a reaction should live for.
    for (const d of durations) {
      expect(d).toBeGreaterThanOrEqual(2500);
      expect(d).toBeLessThanOrEqual(4000);
    }
  });

  it("sends some reactions almost straight up, some left and some right", () => {
    // The reference stream is a mix of personalities, not a uniform spray — a symmetric
    // random range alone averages every emoji into the same gentle wobble.
    const { items, send } = left();
    send(...Array(28).fill("heart"));
    const drifts = items().map((el) => parseFloat(cssVar(el, "--zk-reaction-drift")));

    expect(drifts.some((d) => Math.abs(d) <= 4)).toBe(true);    // near-vertical
    expect(drifts.some((d) => d <= -9)).toBe(true);             // leans left
    expect(drifts.some((d) => d >= 9)).toBe(true);              // leans right
  });

  it("keeps the sideways wander small, so a reaction stays in its lane all the way up", () => {
    // The net displacement at the top is px, on top of a lane that is already only ~19%
    // of the player wide: an emoji must never finish over the middle of the picture.
    const { items, send } = left();
    send(...Array(24).fill("like"));

    for (const el of items()) {
      expect(Math.abs(parseFloat(cssVar(el, "--zk-reaction-drift")))).toBeLessThanOrEqual(22);
      expect(Math.abs(parseFloat(cssVar(el, "--zk-reaction-sway")))).toBeLessThanOrEqual(14);
    }
  });

  it("keeps the tilt, scale and travel inside their bounds, so nothing tumbles or looms", () => {
    const { items, send } = left();
    send(...Array(24).fill("fire"));

    for (const el of items()) {
      expect(Math.abs(parseFloat(cssVar(el, "--zk-reaction-rot")))).toBeLessThanOrEqual(9);
      const scale = parseFloat(cssVar(el, "--zk-reaction-scale"));
      expect(scale).toBeGreaterThanOrEqual(0.85);
      expect(scale).toBeLessThanOrEqual(1.15);
      const travel = parseFloat(cssVar(el, "--zk-reaction-travel"));
      expect(travel).toBeGreaterThanOrEqual(0.82);
      expect(travel).toBeLessThanOrEqual(1.16);
    }
  });

  it("drives the stream keyframes, on the same clock as the node's removal", () => {
    const { items, send } = left();
    send("heart");

    expect(items()[0].className).toContain("zk-reaction-stream");
    expect(items()[0].className).not.toContain("zk-reaction-float");
    // Its own duration, not the nominal one — but still timed by the same constant, so the
    // fade and the node's removal cannot drift apart.
    const ms = parseFloat(items()[0].style.animationDuration);
    expect(ms).toBeGreaterThan(0);
    expect(ms).toBeLessThanOrEqual(REACTION_MAX_LIFETIME_MS);
    settle();
    expect(items()).toHaveLength(0);           // and it still leaves the DOM on its own
  });

  it("lets a slow float finish its fade before the sweep takes its node", () => {
    // The trap in giving every item its own duration: a single shared expiry would sweep
    // the slowest emoji away mid-climb. Nothing may disappear before its own animation ends.
    const { items, send } = left();
    send(...Array(16).fill("clap"));
    const slowest = Math.max(...items().map((el) => parseFloat(el.style.animationDuration)));

    // One tick short of the slowest float: that emoji must still be on screen.
    act(() => vi.advanceTimersByTime(slowest - 100));
    expect(items().length).toBeGreaterThan(0);

    settle();
    expect(items()).toHaveLength(0);
  });

  it("marks the layer as the size container the rise is measured against", () => {
    // index.css sizes the climb in cqh off this class, which is what makes the stream
    // scale with the PLAYER rather than the browser window — resize, breakpoint or
    // fullscreen alike.
    const { layer } = left();
    expect(layer.className).toContain("zk-reaction-lane");
    expect(layer).toHaveAttribute("data-lane", "left");
  });

  it("leaves the Producer Console's full-width scatter alone", () => {
    // The host's monitor does not pass `lane`, and must keep the behaviour it had. The
    // reference this enhancement follows is a VIEWER frame, so none of it reaches here.
    const { layer, items, send } = overlay();
    send(...Array(20).fill("heart"));

    expect(layer.className).not.toContain("zk-reaction-lane");
    expect(layer).toHaveAttribute("data-lane", "spread");
    expect(items()[0].className).toContain("zk-reaction-float");
    // Spread across the frame, not confined to the left-hand lane.
    expect(Math.max(...items().map((el) => parseFloat(el.style.left)))).toBeGreaterThan(17.6);
    // One shared clock, and none of the viewer-lane per-item properties.
    const durations = new Set(items().map((el) => el.style.animationDuration));
    expect([...durations]).toEqual([`${REACTION_DURATION_MS}ms`]);
    for (const el of items()) {
      expect(el.style.getPropertyValue("--zk-reaction-rot").trim()).toBe("0deg");
      expect(el.style.getPropertyValue("--zk-reaction-sway").trim()).toBe("0px");
    }
  });
});

describe("ReactionOverlay — reduced motion", () => {
  // The float itself is CSS, and jsdom applies no stylesheet, so the behaviour is asserted
  // against the rule that implements it. It is worth the unusual coupling: the failure this
  // guards is silent (a reaction that keeps flying for someone who asked their OS for less
  // animation), and the a11y override is easy to forget when the keyframes are edited.
  const css = readFileSync(resolve(process.cwd(), "src/index.css"), "utf8");
  const reduced = css.slice(css.indexOf("@media (prefers-reduced-motion: reduce)"));

  it("swaps both reaction animations onto the opacity-only keyframes", () => {
    expect(reduced).toMatch(/\.zk-reaction-float,\s*\n\s*\.zk-reaction-stream\s*\{[^}]*animation-name:\s*zk-reaction-hold/);
  });

  it("keeps the emoji on screen long enough to recognise, rather than hiding it", () => {
    // zk-reaction-hold holds full opacity across the middle of the item's life. The
    // reaction is information — the host has no other view of it — so "animation: none"
    // (what every other animated class gets) would be the wrong answer here.
    const hold = css.match(/@keyframes zk-reaction-hold\s*\{([^}]*\}[^}]*)\}/)[1];
    expect(hold).toContain("opacity: 1");
    expect(hold).toMatch(/0%,\s*100%\s*\{\s*opacity:\s*0/);
    // Opacity only: nothing that travels, tilts or scales.
    expect(hold).not.toContain("transform");
  });

  it("still renders and still removes the reaction, so the feature is not switched off", () => {
    // The component is motion-agnostic: it sets up the same node and the same expiry, and
    // the media query decides only how that node moves.
    const { items, send } = overlay({ lane: "left" });
    send("heart");
    expect(items()).toHaveLength(1);
    settle();
    expect(items()).toHaveLength(0);
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
    expect(glyphSrc(layer)).toBe(REACTION_BY_KEY.heart.asset);
    // Still no text of any kind on the layer — the privacy assertion below is what this
    // test is really for, and an empty textContent makes it airtight.
    expect(layer.textContent).toBe("");
    for (const secret of ["Ada", "ada@example.com", "user-42", "tok-secret"]) {
      expect(layer.textContent).not.toContain(secret);
    }
  });

  it("is hidden from assistive technology, so it cannot bury chat and Q&A updates", () => {
    const { layer } = overlay();
    expect(layer).toHaveAttribute("aria-hidden", "true");
  });
});
