// Answering a question from the Producer Console, and the asker reading that answer.
//
// ── WHAT WAS MISSING ───────────────────────────────────────────────────────────────────
// The Q&A tab had approve, bookmark/on-air, "Answered", assign, dismiss and delete — six
// controls, not one of which let the host write anything. "Answered" was a status tick
// with no text behind it, so a viewer was told their question had been answered and given
// nothing to read.
//
// The two behaviours worth guarding hardest, because both are ways of quietly lying to
// the audience:
//   * a send that never left the browser must NOT leave the question looking answered, and
//   * the "Answered" tick on its own must NOT render as though a reply exists.
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { QATab } from "./ChatQAPanel";
import WatchPanel from "../watch/WatchPanel";

const QUESTION = {
  id: "q1",
  name: "NAVEEN",
  text: "hey hai",
  votes: 0,
  status: "approved",
  pinned: false,
  assigned_name: null,
  flags: [],
  created_at: "2026-09-21T10:59:00Z",
  answer: null,
  answered_at: null,
  answered_by_name: null,
};

const q = (over = {}) => ({ ...QUESTION, ...over });

// `send` returns true from useEventStream when the frame reached an open socket.
const okSend = () => vi.fn(() => true);

const console_ = (questions, send, canModerate = true) =>
  render(<QATab questions={questions} speakers={[]} canModerate={canModerate} send={send} />);

const answerButton = () => screen.queryByRole("button", { name: /^answer$/i });
const editButton = () => screen.queryByRole("button", { name: /edit answer/i });
const box = () => screen.getByRole("textbox", { name: /answer the question/i });
const sendButton = () => screen.getByRole("button", { name: /send answer|sending/i });

beforeEach(() => vi.clearAllMocks());

// ── 1. the host can see there is something to do ───────────────────────────────────────

describe("the Answer action", () => {
  it("is offered on a question", () => {
    console_([q()], okSend());
    expect(answerButton()).toBeInTheDocument();
  });

  it("is offered on an approved question, which is the case the brief names", () => {
    console_([q({ status: "approved" })], okSend());
    expect(answerButton()).toBeInTheDocument();
  });

  it("opens a text box under that question when clicked", async () => {
    const u = userEvent.setup();
    console_([q()], okSend());
    expect(screen.queryByRole("textbox", { name: /answer the question/i })).not.toBeInTheDocument();

    await u.click(answerButton());

    expect(box()).toBeInTheDocument();
    expect(sendButton()).toBeInTheDocument();
  });

  it("is not offered to someone who cannot moderate", () => {
    console_([q()], okSend(), false);
    expect(answerButton()).not.toBeInTheDocument();
  });

  it("closes again on Cancel without sending anything", async () => {
    const u = userEvent.setup();
    const send = okSend();
    console_([q()], send);

    await u.click(answerButton());
    await u.type(box(), "never mind");
    await u.click(screen.getByRole("button", { name: /cancel/i }));

    expect(screen.queryByRole("textbox", { name: /answer the question/i })).not.toBeInTheDocument();
    expect(send).not.toHaveBeenCalled();
  });
});

// ── 2. the host can submit an answer ───────────────────────────────────────────────────

describe("submitting an answer", () => {
  it("sends the typed text on the qa.respond action", async () => {
    const u = userEvent.setup();
    const send = okSend();
    console_([q()], send);

    await u.click(answerButton());
    await u.type(box(), "Doors open at nine.");
    await u.click(sendButton());

    expect(send).toHaveBeenCalledWith("qa.respond", { id: "q1", answer: "Doors open at nine." });
  });

  it("trims the text before sending", async () => {
    const u = userEvent.setup();
    const send = okSend();
    console_([q()], send);

    await u.click(answerButton());
    await u.type(box(), "   spaced   ");
    await u.click(sendButton());

    expect(send).toHaveBeenCalledWith("qa.respond", { id: "q1", answer: "spaced" });
  });

  it("refuses to send an empty box, so it cannot become a silent mark-as-answered", async () => {
    const u = userEvent.setup();
    const send = okSend();
    console_([q()], send);

    await u.click(answerButton());
    expect(sendButton()).toBeDisabled();

    await u.type(box(), "   ");
    expect(sendButton()).toBeDisabled();
    expect(send).not.toHaveBeenCalled();
  });

  it("leaves the other Q&A actions alone", async () => {
    const u = userEvent.setup();
    const send = okSend();
    console_([q({ status: "pending" })], send);

    for (const [name, action] of [
      [/approve/i, "qa.approve"], [/on air/i, "qa.pin"],
      [/dismiss/i, "qa.dismiss"], [/delete/i, "qa.delete"],
    ]) {
      await u.click(screen.getByRole("button", { name }));
      expect(send).toHaveBeenCalledWith(action, { id: "q1" });
    }
  });
});

// ── 3. the saved answer comes back and is shown ────────────────────────────────────────

describe("an answer that the server saved", () => {
  const ANSWERED = q({
    status: "answered",
    answer: "Doors open at nine.",
    answered_at: "2026-09-21T11:02:00Z",
    answered_by_name: "Vihari",
  });

  it("is displayed under the question in the console", () => {
    console_([ANSWERED], okSend());
    expect(screen.getByText("Doors open at nine.")).toBeInTheDocument();
  });

  it("credits whoever answered it", () => {
    console_([ANSWERED], okSend());
    expect(screen.getByText(/answered by vihari/i)).toBeInTheDocument();
  });

  it("offers Edit answer rather than Answer", () => {
    console_([ANSWERED], okSend());
    expect(editButton()).toBeInTheDocument();
    expect(answerButton()).not.toBeInTheDocument();
  });

  it("pre-fills the existing text when editing", async () => {
    const u = userEvent.setup();
    console_([ANSWERED], okSend());

    await u.click(editButton());
    expect(box()).toHaveValue("Doors open at nine.");
  });

  it("sends the edited text on the same action", async () => {
    const u = userEvent.setup();
    const send = okSend();
    console_([ANSWERED], send);

    await u.click(editButton());
    await u.clear(box());
    await u.type(box(), "Correction: eight.");
    await u.click(sendButton());

    expect(send).toHaveBeenCalledWith("qa.respond", { id: "q1", answer: "Correction: eight." });
  });

  it("collapses the composer once the server echoes the answer back", async () => {
    // The socket is fire-and-forget, so the composer waits for the question.update that
    // carries its own text rather than congratulating itself on submit.
    const u = userEvent.setup();
    const { rerender } = console_([q()], okSend());

    await u.click(answerButton());
    await u.type(box(), "Doors open at nine.");
    await u.click(sendButton());
    expect(screen.getByRole("textbox", { name: /answer the question/i })).toBeInTheDocument();

    rerender(<QATab questions={[ANSWERED]} speakers={[]} canModerate send={okSend()} />);

    expect(screen.queryByRole("textbox", { name: /answer the question/i })).not.toBeInTheDocument();
    expect(screen.getByText("Doors open at nine.")).toBeInTheDocument();
  });
});

// ── 5. a send that failed must not look like a success ─────────────────────────────────

describe("when the answer could not be sent", () => {
  const deadSocket = () => vi.fn(() => false);   // useEventStream: socket not OPEN

  it("says so instead of closing the box", async () => {
    const u = userEvent.setup();
    console_([q()], deadSocket());

    await u.click(answerButton());
    await u.type(box(), "Doors open at nine.");
    await u.click(sendButton());

    expect(screen.getByText(/not connected/i)).toBeInTheDocument();
    expect(box()).toBeInTheDocument();
  });

  it("keeps the host's words so they are not retyped", async () => {
    const u = userEvent.setup();
    console_([q()], deadSocket());

    await u.click(answerButton());
    await u.type(box(), "Doors open at nine.");
    await u.click(sendButton());

    expect(box()).toHaveValue("Doors open at nine.");
  });

  it("does not show the question as answered", async () => {
    const u = userEvent.setup();
    console_([q()], deadSocket());

    await u.click(answerButton());
    await u.type(box(), "Doors open at nine.");
    await u.click(sendButton());

    // The status badge still reads what the server last said it was.
    expect(screen.getByText("approved")).toBeInTheDocument();
    expect(screen.queryByText(/^answered by/i)).not.toBeInTheDocument();
  });

  it("lets the host try again", async () => {
    const u = userEvent.setup();
    const send = deadSocket();
    console_([q()], send);

    await u.click(answerButton());
    await u.type(box(), "Doors open at nine.");
    await u.click(sendButton());
    await u.click(sendButton());

    // Not blocked by the in-flight guard: nothing is in flight, the send never left.
    expect(send).toHaveBeenCalledTimes(2);
  });
});

// ── 6. one answer per click ────────────────────────────────────────────────────────────

describe("duplicate submission", () => {
  it("is blocked while the first send is still unconfirmed", async () => {
    const u = userEvent.setup();
    const send = okSend();
    console_([q()], send);

    await u.click(answerButton());
    await u.type(box(), "Doors open at nine.");
    await u.click(sendButton());
    await u.click(sendButton());
    await u.click(sendButton());

    expect(send).toHaveBeenCalledTimes(1);
  });

  it("disables the button and says it is sending", async () => {
    const u = userEvent.setup();
    console_([q()], okSend());

    await u.click(answerButton());
    await u.type(box(), "Doors open at nine.");
    await u.click(sendButton());

    expect(screen.getByRole("button", { name: /sending/i })).toBeDisabled();
  });

  it("locks the text box too, so the sent text cannot drift from the typed text", async () => {
    const u = userEvent.setup();
    console_([q()], okSend());

    await u.click(answerButton());
    await u.type(box(), "Doors open at nine.");
    await u.click(sendButton());

    expect(box()).toBeDisabled();
  });
});

// ── the tick is not an answer ──────────────────────────────────────────────────────────

describe("a question only ticked as answered", () => {
  const TICKED = q({ status: "answered", answer: null });

  it("is called out in the console as having no written response", () => {
    console_([TICKED], okSend());
    expect(screen.getByText(/marked answered — no written response/i)).toBeInTheDocument();
  });

  it("still offers Answer, not Edit answer", () => {
    console_([TICKED], okSend());
    expect(answerButton()).toBeInTheDocument();
    expect(editButton()).not.toBeInTheDocument();
  });

  it("shows the audience nothing to read", () => {
    watch([TICKED]);
    // The existing "Answered" chip is untouched; what must not appear is an answer body.
    expect(screen.getByText(/answered/i)).toBeInTheDocument();
    expect(screen.queryByText(/host answered/i)).not.toBeInTheDocument();
  });
});

// ── 4. the viewer's Q&A panel ──────────────────────────────────────────────────────────

function watch(questions) {
  return render(
    <WatchPanel
      messages={[]}
      typing={{}}
      questions={questions}
      polls={[]}
      send={vi.fn()}
      connected
      identified
      eventId="e1"
      onIdentified={vi.fn()}
      enabledTabs={{ chat: false, qa: true, polls: false }}
    />
  );
}

describe("the viewer Q&A panel", () => {
  it("shows the host's answer under the question", () => {
    watch([q({ status: "answered", answer: "Doors open at nine.", answered_by_name: "Vihari" })]);

    expect(screen.getByText("hey hai")).toBeInTheDocument();
    expect(screen.getByText("Doors open at nine.")).toBeInTheDocument();
  });

  it("names who answered", () => {
    watch([q({ status: "answered", answer: "Doors open at nine.", answered_by_name: "Vihari" })]);
    expect(screen.getByText(/vihari answered/i)).toBeInTheDocument();
  });

  it("falls back to the host when the name is absent", () => {
    watch([q({ status: "answered", answer: "Doors open at nine." })]);
    expect(screen.getByText(/host answered/i)).toBeInTheDocument();
  });

  it("shows no answer block on a question nobody has answered", () => {
    watch([q()]);
    expect(screen.getByText("hey hai")).toBeInTheDocument();
    expect(screen.queryByText(/answered/i)).not.toBeInTheDocument();
  });

  it("leaves asking and upvoting working", async () => {
    const u = userEvent.setup();
    const send = vi.fn();
    render(
      <WatchPanel
        messages={[]} typing={{}} questions={[q()]} polls={[]} send={send} connected identified
        eventId="e1" onIdentified={vi.fn()} enabledTabs={{ chat: false, qa: true, polls: false }}
      />
    );

    await u.click(screen.getByRole("button", { name: /upvote/i }));
    expect(send).toHaveBeenCalledWith("qa.vote", { id: "q1" });
  });
});

// ── search and filters keep working alongside the new control ──────────────────────────

describe("the Q&A filters and search", () => {
  const TWO = [q(), q({ id: "q2", name: "ASHA", text: "when does it start", status: "answered" })];

  it("still filter by status", async () => {
    const u = userEvent.setup();
    console_(TWO, okSend());

    await u.selectOptions(screen.getByLabelText(/filter questions/i), "answered");

    expect(screen.getByText("when does it start")).toBeInTheDocument();
    expect(screen.queryByText("hey hai")).not.toBeInTheDocument();
  });

  it("still search by text and author", async () => {
    const u = userEvent.setup();
    console_(TWO, okSend());

    await u.type(screen.getByLabelText(/search questions/i), "asha");

    expect(screen.getByText("when does it start")).toBeInTheDocument();
    expect(screen.queryByText("hey hai")).not.toBeInTheDocument();
  });

  it("keeps each question's composer to itself", async () => {
    const u = userEvent.setup();
    console_(TWO, okSend());

    const cards = screen.getAllByRole("button", { name: /^answer$/i });
    await u.click(cards[0]);

    // One open box, not two.
    expect(screen.getAllByRole("textbox", { name: /answer the question/i })).toHaveLength(1);
  });
});

// ── the answer belongs to the question it was typed under ──────────────────────────────

it("sends the id of the question whose box was opened", async () => {
  const u = userEvent.setup();
  const send = okSend();
  console_([q({ id: "qA", votes: 5 }), q({ id: "qB", text: "second", votes: 1 })], send);

  // Sorted by votes, so qA is first; open the SECOND card's box.
  const second = screen.getByText("second").closest("div.flex.gap-2\\.5");
  await u.click(within(second).getByRole("button", { name: /^answer$/i }));
  await u.type(within(second).getByRole("textbox", { name: /answer the question/i }), "for B");
  await u.click(within(second).getByRole("button", { name: /send answer/i }));

  expect(send).toHaveBeenCalledWith("qa.respond", { id: "qB", answer: "for B" });
});
