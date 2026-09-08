// Regression cover for the VIEWER half of the reaction system, in two layers, with no
// socket and no LiveKit needed for either:
//
//   1. ReactionBar — that the bar is tap targets and nothing else. It used to render a
//      running total per emoji (👍 11 ❤️ 0 🎉 0 🔥 0 👏 0); the counters are gone, and the
//      most valuable assertion in this file is that no digit can come back.
//   2. liveReducer — that a reaction envelope does NOT become panel state, and that the
//      chat/Q&A/poll/presence state it shares a socket with is untouched by one.
//
// What the tap actually produces is asserted elsewhere: server/test_viewer_reactions.py for
// the envelope and its fan-out, components/live/ReactionOverlay.test.jsx for the emoji that
// floats over the video.
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { LIVE_EMPTY, liveReducer } from "./EventWatch";
import ReactionBar from "../../components/watch/ReactionBar";
import { REACTIONS } from "../../data/reactions";

const snapshot = (data = {}) => ({ channel: "moderator", type: "snapshot", data });
const burst = (reaction, over = {}) => ({
  channel: "reactions",
  type: "reaction.burst",
  data: { event_id: "event-1", reaction, id: "srv-1", ts: 1757000000, ...over },
});

describe("ReactionBar — tap targets, no counters", () => {
  it("renders all five emoji and not a single digit", () => {
    // THE IMPORTANT ONE. "👍 11" was the reported problem; a regression that reintroduced
    // any total — per emoji or aggregate — puts a digit back in this bar and fails here.
    const { container } = render(<ReactionBar onReact={() => {}} />);

    expect(container.textContent).not.toMatch(/\d/);
    for (const r of REACTIONS) {
      const button = screen.getByRole("button", { name: r.label });
      expect(button.textContent).toBe(r.emoji);   // the emoji alone, nothing appended
    }
  });

  it("names each button by its reaction alone, with no count read out", () => {
    // The old accessible name was `${label} (${count})`, so a screen-reader user heard the
    // total too. Both the visible and the spoken label are now just the reaction.
    render(<ReactionBar onReact={() => {}} />);
    const names = screen.getAllByRole("button").map((b) => b.getAttribute("aria-label"));

    expect(names).toEqual(REACTIONS.map((r) => r.label));
    for (const name of names) expect(name).not.toMatch(/\d|\(|\)/);
  });

  it("exposes no pressed/selected state, because a reaction is not a toggle", () => {
    // aria-pressed would claim "your reaction is set", which no longer exists anywhere:
    // every tap is an independent event and there is nothing to un-react.
    render(<ReactionBar onReact={() => {}} />);
    for (const button of screen.getAllByRole("button")) {
      expect(button).not.toHaveAttribute("aria-pressed");
    }
  });

  it("renders all five emoji in the designed order", () => {
    render(<ReactionBar onReact={() => {}} />);
    const labels = screen.getAllByRole("button").map((b) => b.getAttribute("title"));
    expect(labels).toEqual(REACTIONS.map((r) => r.label));
  });

  it("sends the wire key the server expects when ❤️ is clicked", async () => {
    const onReact = vi.fn();
    render(<ReactionBar onReact={onReact} />);

    await userEvent.click(screen.getByRole("button", { name: "Love" }));

    // The wire key, not the label or the emoji — moderation.REACTION_KEYS.
    expect(onReact).toHaveBeenCalledWith("heart");
    expect(onReact).toHaveBeenCalledTimes(1);
  });

  it("sends the right key for every button", async () => {
    const onReact = vi.fn();
    render(<ReactionBar onReact={onReact} />);

    for (const r of REACTIONS) {
      await userEvent.click(screen.getByRole("button", { name: r.label }));
    }
    expect(onReact.mock.calls.flat()).toEqual(REACTIONS.map((r) => r.key));
  });

  it("sends a separate event for every tap, including repeats of the same emoji", async () => {
    // Each tap is its own reaction — three taps must reach the host as three floating
    // emoji, so three actions have to go out.
    const onReact = vi.fn();
    render(<ReactionBar onReact={onReact} />);
    const love = screen.getByRole("button", { name: "Love" });

    await userEvent.click(love);
    await userEvent.click(love);
    await userEvent.click(love);

    expect(onReact.mock.calls).toEqual([["heart"], ["heart"], ["heart"]]);
  });

  it("gives immediate local feedback on the tapped emoji", async () => {
    // The reaction itself comes back over the socket a moment later; this is what makes
    // the button feel like it responded to the finger, Meet-style.
    const onReact = vi.fn();
    render(<ReactionBar onReact={onReact} />);
    const love = screen.getByRole("button", { name: "Love" });
    const before = love.className;

    await userEvent.click(love);
    expect(love.className).not.toBe(before);
    expect(love.className).toContain("emerald");     // the "you just did that" flash
  });

  it("sends nothing while disabled", async () => {
    // disabled is liveStatus !== "open", or the host's Reactions toggle being off.
    const onReact = vi.fn();
    render(<ReactionBar onReact={onReact} disabled />);

    await userEvent.click(screen.getByRole("button", { name: "Like" }));
    expect(onReact).not.toHaveBeenCalled();
    for (const button of screen.getAllByRole("button")) expect(button).toBeDisabled();
  });
});

describe("ReactionBar — raise hand is unchanged", () => {
  // Raise-hand shares this bar with the reactions, so removing the counters must not have
  // touched it. It is a real toggle with real state, unlike a reaction.
  it("stays hidden unless the event enables it", () => {
    render(<ReactionBar onReact={() => {}} />);
    expect(screen.queryByRole("button", { name: /hand/i })).toBeNull();
    expect(screen.getAllByRole("button")).toHaveLength(REACTIONS.length);
  });

  it("still sends and still reports its own pressed state", async () => {
    const onToggleHand = vi.fn();
    const { rerender } = render(
      <ReactionBar onReact={() => {}} onToggleHand={onToggleHand} raiseHandVisible />
    );

    const raise = screen.getByRole("button", { name: "Raise your hand" });
    expect(raise).toHaveAttribute("aria-pressed", "false");
    await userEvent.click(raise);
    expect(onToggleHand).toHaveBeenCalledTimes(1);

    rerender(
      <ReactionBar onReact={() => {}} onToggleHand={onToggleHand} raiseHandVisible handRaised />
    );
    expect(screen.getByRole("button", { name: "Lower your hand" }))
      .toHaveAttribute("aria-pressed", "true");
  });
});

describe("liveReducer — a reaction is never panel state", () => {
  it("holds no reaction field at all", () => {
    // Nothing to accumulate, nothing to go stale, and nothing for a reconnect to replay.
    expect(LIVE_EMPTY).not.toHaveProperty("reactions");
  });

  it("leaves the state object completely untouched by a reaction envelope", () => {
    // Identity, not equality: dispatching a burst must not even produce a new object, or
    // every tap in the audience would re-render the player, the chat list and the panel.
    const seeded = liveReducer(LIVE_EMPTY, snapshot({
      messages: [{ id: "m1", text: "hi" }],
      questions: [{ id: "q1" }],
      polls: [{ id: "p1" }],
      participants: [{ identity: "v1", role: "viewer" }],
    }));

    for (const key of REACTIONS.map((r) => r.key)) {
      expect(liveReducer(seeded, burst(key))).toBe(seeded);
    }
  });

  it("ignores a reaction from another event just as completely", () => {
    // Event isolation is enforced twice: the bus is per-event server-side, and
    // EventWatch's own handler checks data.event_id before it reaches the overlay. Either
    // way the reducer holds nothing, so there is no cross-event state to corrupt.
    expect(liveReducer(LIVE_EMPTY, burst("heart", { event_id: "some-other-event" })))
      .toBe(LIVE_EMPTY);
  });

  it("takes no reaction data from the opening snapshot", () => {
    // The server sends none (server/test_viewer_reactions.py asserts that); this asserts
    // the client would not store it even if a stale deployment did.
    const next = liveReducer(LIVE_EMPTY, snapshot({ reactions: { like: 11, heart: 4 } }));
    expect(next).not.toHaveProperty("reactions");
  });

  it("ignores a legacy reaction.update from an older server", () => {
    // Belt and braces during a rolling deploy: the counting envelope is gone, and an old
    // one arriving must be a no-op rather than resurrecting a total.
    const stale = { channel: "reactions", type: "reaction.update",
                    data: { event_id: "event-1", reactions: { like: 11 } } };
    expect(liveReducer(LIVE_EMPTY, stale)).toBe(LIVE_EMPTY);
  });
});

describe("liveReducer — chat, Q&A, polls and presence still work", () => {
  // The requirement that nothing else changed. Reactions share ONE socket with all of
  // these, so a reaction envelope arriving between them must not disturb any of it.
  const seeded = () => liveReducer(LIVE_EMPTY, snapshot({
    messages: [{ id: "m1", text: "hello" }],
    questions: [{ id: "q1", text: "why?" }],
    polls: [{ id: "p1", question: "which?" }],
    participants: [{ identity: "v1", role: "viewer" }],
    slow_mode_seconds: 5,
    you: { identity: "v1", can_moderate: false, can_host: false },
  }));

  it("still seeds chat, Q&A, polls, presence, slow mode and identity from the snapshot", () => {
    const state = seeded();
    expect(state.messages).toHaveLength(1);
    expect(state.questions).toHaveLength(1);
    expect(state.polls).toHaveLength(1);
    expect(state.participants.v1.role).toBe("viewer");
    expect(state.slowModeSeconds).toBe(5);
    expect(state.you.identity).toBe("v1");
  });

  it("still applies chat, Q&A, poll and presence updates after a reaction", () => {
    let state = liveReducer(seeded(), burst("heart"));
    state = liveReducer(state, { channel: "chat", type: "message.new",
                                 data: { id: "m2", text: "second" } });
    state = liveReducer(state, burst("like"));
    state = liveReducer(state, { channel: "qa", type: "question.new", data: { id: "q2" } });
    state = liveReducer(state, { channel: "poll", type: "poll.update",
                                 data: { id: "p1", question: "updated" } });
    state = liveReducer(state, { channel: "participants", type: "participant.update",
                                 data: { identity: "v1", role: "viewer", hand: true } });
    state = liveReducer(state, burst("party"));

    expect(state.messages.map((m) => m.id)).toEqual(["m1", "m2"]);
    expect(state.questions.map((q) => q.id)).toEqual(["q1", "q2"]);
    expect(state.polls[0].question).toBe("updated");
    expect(state.participants.v1.hand).toBe(true);
    expect(state).not.toHaveProperty("reactions");
  });

  it("still surfaces a typing indicator and a removal between reactions", () => {
    let state = liveReducer(seeded(), burst("fire"));
    state = liveReducer(state, { channel: "chat", type: "typing",
                                 data: { identity: "v2", name: "Bo", typing: true } });
    expect(state.typing.v2.name).toBe("Bo");

    state = liveReducer(state, burst("clap"));
    state = liveReducer(state, { channel: "session", type: "removed",
                                 data: { identity: "v1", reason: "Removed by host" } });
    expect(state.removed.reason).toBe("Removed by host");
  });
});
