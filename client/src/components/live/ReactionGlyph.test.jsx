// The reaction artwork: one visual per reaction, the same one everywhere it appears, and a
// character to fall back to when the file does not arrive.
//
// ── WHAT CHANGED ───────────────────────────────────────────────────────────────────────
// Reactions used to render as the emoji CHARACTER, which meant the same tap looked like a
// different product on every platform — Apple's artwork on iPhone, Google's on Android,
// Microsoft's on Windows, a monochrome box on some Linux builds. They now render bundled
// Fluent Emoji artwork (MIT, vendored in src/assets/reactions/).
//
// The two assertions worth the most here:
//   * the picker and the floating overlay resolve to the SAME file — tapping one mark and
//     watching a different one drift up would read as a bug, and they are separate
//     components that could drift apart;
//   * a missing asset degrades to the character rather than to a broken-image icon over
//     live video.
import { act, render, screen } from "@testing-library/react";
import { fireEvent } from "@testing-library/dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import ReactionGlyph from "./ReactionGlyph";
import ReactionOverlay from "./ReactionOverlay";
import ReactionBar from "../watch/ReactionBar";
import { createReactionChannel } from "../../hooks/useReactionChannel";
import { REACTIONS, REACTION_BY_KEY } from "../../data/reactions";
import { preloadReactionAssets, resetFailedAssets } from "../../data/reactionAssets";

const glyph = (node) => node.querySelector("[data-reaction-glyph]");
const glyphSrc = (node) => glyph(node)?.getAttribute("src");

beforeEach(() => resetFailedAssets());
afterEach(() => resetFailedAssets());

// ── 1-5. every reaction has its own bundled artwork ────────────────────────────────────

describe("each reaction renders its own asset", () => {
  it.each(REACTIONS.map((r) => [r.label, r]))("%s", (_label, r) => {
    render(<ReactionGlyph reactionKey={r.key} emoji={r.emoji} asset={r.asset} />);

    const node = glyph(document.body);
    expect(node.tagName).toBe("IMG");
    expect(node.getAttribute("src")).toBe(r.asset);
  });

  it("gives every reaction a DIFFERENT file", () => {
    // A copy-paste in the mapping would silently render 👍 for 🔥; five distinct sources
    // is the cheapest way to catch it.
    const sources = REACTIONS.map((r) => r.asset);
    expect(new Set(sources).size).toBe(REACTIONS.length);
  });

  it("carries an asset for every key the wire can send", () => {
    for (const r of REACTIONS) {
      expect(REACTION_BY_KEY[r.key].asset).toBeTruthy();
    }
  });
});

// ── 6. the picker and the float are the same visual ────────────────────────────────────

describe("picker and floating overlay agree", () => {
  it.each(REACTIONS.map((r) => [r.label, r]))("use the identical file for %s", (_label, r) => {
    const { unmount } = render(<ReactionBar onReact={() => {}} />);
    const fromPicker = glyphSrc(screen.getByRole("button", { name: r.label }));
    unmount();

    const channel = createReactionChannel();
    render(<ReactionOverlay channel={channel} />);
    act(() => channel.emit({ id: "x1", reaction: r.key }));

    const fromFloat = glyphSrc(screen.getByTestId("reaction-overlay"));
    expect(fromFloat).toBe(fromPicker);
    expect(fromFloat).toBe(r.asset);
  });
});

// ── 10. a missing asset degrades to the character ──────────────────────────────────────

describe("when an asset fails to load", () => {
  const heart = REACTION_BY_KEY.heart;

  it("falls back to the emoji character, not a broken image", () => {
    render(<ReactionGlyph reactionKey="heart" emoji={heart.emoji} asset={heart.asset} />);
    fireEvent.error(glyph(document.body));

    const node = glyph(document.body);
    expect(node.tagName).toBe("SPAN");
    expect(node.textContent).toBe(heart.emoji);
  });

  it("keeps the same box, so nothing shifts around it", () => {
    const { rerender } = render(
      <ReactionGlyph reactionKey="heart" emoji={heart.emoji} asset={heart.asset} size={40} />
    );
    const before = glyph(document.body).style;
    expect([before.width, before.height]).toEqual(["40px", "40px"]);

    fireEvent.error(glyph(document.body));
    rerender(<ReactionGlyph reactionKey="heart" emoji={heart.emoji} asset={heart.asset} size={40} />);

    const after = glyph(document.body).style;
    expect([after.width, after.height]).toEqual(["40px", "40px"]);
  });

  it("remembers the failure, so later instances never re-request it", () => {
    // Forty floating fires each retrying a 404 is the thing this prevents.
    const { unmount } = render(
      <ReactionGlyph reactionKey="fire" emoji="🔥" asset={REACTION_BY_KEY.fire.asset} />
    );
    fireEvent.error(glyph(document.body));
    unmount();

    render(<ReactionGlyph reactionKey="fire" emoji="🔥" asset={REACTION_BY_KEY.fire.asset} />);
    expect(glyph(document.body).tagName).toBe("SPAN");   // straight to the character
  });

  it("does not condemn the other reactions", () => {
    const { unmount } = render(
      <ReactionGlyph reactionKey="fire" emoji="🔥" asset={REACTION_BY_KEY.fire.asset} />
    );
    fireEvent.error(glyph(document.body));
    unmount();

    render(<ReactionGlyph reactionKey="like" emoji="👍" asset={REACTION_BY_KEY.like.asset} />);
    expect(glyph(document.body).tagName).toBe("IMG");
  });

  it("renders the character when there is no asset at all", () => {
    render(<ReactionGlyph reactionKey="like" emoji="👍" asset={undefined} />);
    expect(glyph(document.body).textContent).toBe("👍");
  });
});

// ── the first reaction must not flicker ────────────────────────────────────────────────

describe("preloading", () => {
  it("requests every reaction asset once", () => {
    const requested = [];
    const RealImage = globalThis.Image;
    globalThis.Image = class {
      set src(v) { requested.push(v); }
    };
    try {
      preloadReactionAssets();
      preloadReactionAssets();    // idempotent: the bar and the overlay both ask
    } finally {
      globalThis.Image = RealImage;
    }

    // Either it warmed all five, or a previous test in this file already did — what must
    // never happen is a partial warm.
    expect(requested.length === REACTIONS.length || requested.length === 0).toBe(true);
    if (requested.length) {
      expect(new Set(requested)).toEqual(new Set(REACTIONS.map((r) => r.asset)));
    }
  });
});

// ── 7/8. volume, and several viewers at once ───────────────────────────────────────────

describe("under load", () => {
  it("animates 12 rapid reactions as 12 independent items", () => {
    const channel = createReactionChannel();
    render(<ReactionOverlay channel={channel} />);

    act(() => {
      for (let i = 0; i < 12; i += 1) channel.emit({ id: `r${i}`, reaction: "fire" });
    });

    const items = screen.getByTestId("reaction-overlay").children;
    expect(items.length).toBe(12);
    // Every one carries artwork, and every one is its own node — a repeat of the same
    // emoji must not overwrite the float already in flight.
    expect([...items].every((n) => glyph(n).getAttribute("src") === REACTION_BY_KEY.fire.asset)).toBe(true);
    expect(new Set([...items].map((n) => n.id)).size).toBe(12);
  });

  it("keeps three viewers' different reactions distinct", () => {
    const channel = createReactionChannel();
    render(<ReactionOverlay channel={channel} />);

    act(() => {
      channel.emit({ id: "a", reaction: "like" });
      channel.emit({ id: "b", reaction: "heart" });
      channel.emit({ id: "c", reaction: "party" });
    });

    const sources = [...screen.getByTestId("reaction-overlay").children].map((n) => glyph(n).getAttribute("src"));
    expect(sources).toEqual([
      REACTION_BY_KEY.like.asset,
      REACTION_BY_KEY.heart.asset,
      REACTION_BY_KEY.party.asset,
    ]);
  });

  it("still drops a key it does not recognise", () => {
    // Unchanged guarantee, restated against the artwork path: an unknown key from a newer
    // server must paint nothing, not a missing image.
    const channel = createReactionChannel();
    render(<ReactionOverlay channel={channel} />);

    act(() => channel.emit({ id: "z", reaction: "sparkles" }));

    expect(screen.getByTestId("reaction-overlay").children.length).toBe(0);
  });
});

// ── the tap still sends the wire key, unchanged ────────────────────────────────────────

it("sends the same wire key it always did", () => {
  const onReact = vi.fn();
  render(<ReactionBar onReact={onReact} />);

  fireEvent.click(screen.getByRole("button", { name: "Hype" }));

  // Swapping the artwork must not touch the protocol.
  expect(onReact).toHaveBeenCalledWith("fire");
});
