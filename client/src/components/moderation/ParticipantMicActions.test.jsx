// The host's microphone action depends on whether there is a microphone to act on.
//
// The reported bug: a viewer showing "Publishing media: No" was still offered "Unmute", the
// server dutifully muted nothing and reported success, and the console showed them live
// while the room stayed silent. A host can grant the RIGHT to speak and can mute a live
// track; turning somebody else's microphone on is not a thing a server can do — it can only
// be asked for.
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ThemeProvider } from "../../theme/ThemeContext";
import ParticipantsPanel from "./ParticipantsPanel";

const BASE = {
  identity: "viewer-1",
  name: "Nani",
  role: "viewer",
  publishing: false,
  on_stage: false,
  muted: false,
  speaking: false,
  quality: "excellent",
};

const send = vi.fn();

beforeEach(() => vi.clearAllMocks());

// Open the drawer for a participant in the given state.
async function openDrawer(overrides = {}) {
  const p = { ...BASE, ...overrides };
  render(
    <ThemeProvider>
      <ParticipantsPanel participants={[p]} send={send} canModerate canHost />
    </ThemeProvider>
  );
  const user = userEvent.setup();
  // The roster row and its inline mic button both carry the person's name; this one is
  // unambiguous and is the documented way in to the full action set.
  await user.click(screen.getByRole("button", { name: /more actions for/i }));
  return user;
}

const sentActions = () => send.mock.calls.map(([action]) => action);

describe("CASE A — a viewer who is not on stage", () => {
  it("is never offered Unmute", async () => {
    await openDrawer({ role: "viewer", on_stage: false, publishing: false, muted: true });
    // This is the exact state from the bug report.
    expect(screen.getByText(/publishing media/i).parentElement).toHaveTextContent("No");
    expect(screen.queryByRole("button", { name: /^unmute$/i })).not.toBeInTheDocument();
  });

  it("is offered a real publish grant instead", async () => {
    const user = await openDrawer({ role: "viewer", on_stage: false, publishing: false });
    const invite = screen.getByRole("button", { name: /invite to speak/i });
    await user.click(invite);

    // participant.stage is the call that reaches livekit.set_stage — a permission change,
    // not a label change.
    expect(sentActions()).toContain("participant.stage");
    expect(send.mock.calls[0][1]).toMatchObject({ identity: "viewer-1", on_stage: true });
  });
});

describe("CASE B — on stage, but publishing nothing", () => {
  it("asks for the microphone rather than claiming to take it", async () => {
    const user = await openDrawer({ role: "speaker", on_stage: true, publishing: false });
    expect(screen.queryByRole("button", { name: /^unmute$/i })).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /request microphone/i }));
    expect(sentActions()).toContain("participant.request_unmute");
    // A request carries no state change — nothing here pretends the person is now live.
    expect(sentActions()).not.toContain("participant.mute");
  });
});

describe("CASE C — publishing, but muted", () => {
  it("requests an unmute instead of asserting one", async () => {
    const user = await openDrawer({ role: "speaker", on_stage: true, publishing: true, muted: true });
    await user.click(screen.getByRole("button", { name: /request to unmute/i }));

    expect(sentActions()).toContain("participant.request_unmute");
    expect(sentActions()).not.toContain("participant.mute");
  });
});

describe("CASE D — a live track", () => {
  it("offers Mute, which the host really can do", async () => {
    const user = await openDrawer({ role: "speaker", on_stage: true, publishing: true, muted: false });
    await user.click(screen.getByRole("button", { name: /^mute$/i }));

    expect(send).toHaveBeenCalledWith("participant.mute", { identity: "viewer-1", muted: true });
  });
});

describe("across every state", () => {
  // The one call that could not work: a host unmuting a remote microphone is not a
  // capability that exists at any permission level. Each state gets the action that can
  // actually succeed, and none of them is "Unmute".
  it.each([
    ["viewer, off stage, silent", { on_stage: false, publishing: false, muted: true }, /invite to speak/i],
    ["on stage, silent", { on_stage: true, publishing: false, muted: true }, /request microphone/i],
    ["publishing, muted", { on_stage: true, publishing: true, muted: true }, /request to unmute/i],
    ["publishing, live", { on_stage: true, publishing: true, muted: false }, /^mute$/i],
  ])("%s offers the action that works", async (_name, state, expected) => {
    await openDrawer(state);
    expect(screen.getByRole("button", { name: expected })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^unmute$/i })).not.toBeInTheDocument();
  });
});
