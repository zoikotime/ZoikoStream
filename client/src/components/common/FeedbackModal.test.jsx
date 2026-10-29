// FeedbackModal's two kinds of caller.
//
// Fire-and-forget (the host after "End Event", a viewer after "Leave Event"): the page is being
// left, so the dialog closes at once and claims nothing — unchanged by the post-event prompt.
// Awaited (a viewer when the event ends): onSubmit returns Promise<boolean>, and the dialog
// says "saved" only on true. PostEventFeedback.test.jsx drives that path through the watch page.
import { act, fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import FeedbackModal from "./FeedbackModal";

const rate = (n) => fireEvent.click(screen.getByRole("button", { name: `${n} star${n > 1 ? "s" : ""}` }));
const submit = () => fireEvent.click(screen.getByRole("button", { name: "Submit" }));

describe("fire-and-forget callers (Leave Event, End Event)", () => {
  it.each([[true], [false], [undefined]])("closes straight away when onSubmit returns %s", (ret) => {
    const onSubmit = vi.fn(() => ret);
    const onDone = vi.fn();
    render(<FeedbackModal open role="viewer" onSubmit={onSubmit} onDone={onDone} />);
    rate(4);
    submit();
    expect(onSubmit).toHaveBeenCalledWith({ rating: 4, comment: null });
    expect(onDone).toHaveBeenCalledTimes(1);
    expect(screen.queryByText(/was saved/)).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("sends nothing for an empty submission", () => {
    const onSubmit = vi.fn();
    const onDone = vi.fn();
    render(<FeedbackModal open role="host" onSubmit={onSubmit} onDone={onDone} />);
    submit();
    expect(onSubmit).not.toHaveBeenCalled();
    expect(onDone).toHaveBeenCalledTimes(1);
  });
});

describe("awaited callers (the event-ended prompt)", () => {
  it("waits, then says saved only when the outcome is true", async () => {
    let settle;
    const onDone = vi.fn();
    render(<FeedbackModal open onSubmit={() => new Promise((r) => { settle = r; })} onDone={onDone} />);
    rate(5);
    submit();
    expect(screen.getByRole("button", { name: "Saving…" })).toBeDisabled();
    expect(onDone).not.toHaveBeenCalled();
    await act(async () => settle(true));
    expect(screen.getByRole("status")).toHaveTextContent("your feedback was saved");
  });

  it("reports a failure (false or a rejection) and offers a retry with the input kept", async () => {
    const onSubmit = vi.fn()
      .mockImplementationOnce(() => Promise.resolve(false))
      .mockImplementationOnce(() => Promise.reject(new Error("socket closed")));
    render(<FeedbackModal open onSubmit={onSubmit} onDone={vi.fn()} />);
    rate(3);
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "Audio dropped" } });
    await act(async () => submit());
    expect(screen.getByRole("alert")).toHaveTextContent(/couldn.t be sent/);
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Try again" })));
    expect(onSubmit).toHaveBeenLastCalledWith({ rating: 3, comment: "Audio dropped" });
    expect(screen.getByRole("alert")).toHaveTextContent(/couldn.t be sent/);
    expect(screen.queryByText(/was saved/)).toBeNull();
  });

  it("ignores an answer that arrives after the dialog was dismissed", async () => {
    let settle;
    const { rerender } = render(
      <FeedbackModal open onSubmit={() => new Promise((r) => { settle = r; })} onDone={vi.fn()} />);
    rate(4);
    submit();
    fireEvent.click(screen.getByRole("button", { name: "Skip" }));
    await act(async () => settle(true));
    rerender(<FeedbackModal open onSubmit={vi.fn()} onDone={vi.fn()} />);
    expect(screen.queryByText(/was saved/)).toBeNull();
  });
});
