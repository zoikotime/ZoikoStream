// Log Support Ticket -> what the console says afterwards.
//
// The modal used to say "Ticket logged" whatever happened, while the server never emailed the
// requester at all. The server now sends the confirmation (SUP-001) and answers with what
// happened to it; these pin that the console repeats that answer and never improves on it:
// "sent" means the email service accepted the message, so nothing here says "delivered", and
// a failed email is reported as a warning beside a ticket that WAS logged.
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../api", () => ({
  default: { post: vi.fn() },
  errMsg: (e) => e?.response?.data?.detail ?? e?.message ?? "failed",
}));
vi.mock("../../ui/Toast", () => ({
  notify: { success: vi.fn(), error: vi.fn(), warning: vi.fn() },
}));

import api from "../../api";
import { notify } from "../../ui/Toast";
import SupportTicketModal from "./SupportTicketModal";

const ORGS = [{ id: "org-1", name: "Sree inc." }];
const REF = "SUP-2026-000123";

const show = () => {
  const onClose = vi.fn();
  const onSaved = vi.fn();
  render(<SupportTicketModal open organizations={ORGS} onClose={onClose} onSaved={onSaved} />);
  return { onClose, onSaved };
};

const fill = ({ email = "user@org.com" } = {}) => {
  fireEvent.change(screen.getByPlaceholderText("Can't start a stream"), { target: { value: "  Can't start a stream  " } });
  fireEvent.change(screen.getByPlaceholderText("Describe the issue…"), { target: { value: "Encoder drops after ten seconds." } });
  fireEvent.change(screen.getByPlaceholderText("user@org.com"), { target: { value: email } });
};

const logIt = () => fireEvent.click(screen.getByRole("button", { name: "Log ticket" }));
const answer = (confirmation) => vi.mocked(api.post).mockResolvedValue({
  data: { id: "t-1", case_reference: REF, confirmation, requester_email: "user@org.com" },
});

beforeEach(() => vi.clearAllMocks());

describe("Log Support Ticket — what the console reports", () => {
  it("sends the form as the server expects it", async () => {
    answer("sent");
    show();
    fill({ email: "  user@org.com " });
    logIt();
    await waitFor(() => expect(api.post).toHaveBeenCalledTimes(1));
    expect(api.post).toHaveBeenCalledWith("/admin/support-tickets", {
      org_id: "org-1", subject: "Can't start a stream", message: "Encoder drops after ten seconds.",
      priority: "normal", requester_email: "user@org.com",
    });
  });

  it("names the ticket and the address when the email service accepted the confirmation", async () => {
    answer("sent");
    const { onClose, onSaved } = show();
    fill();
    logIt();
    await waitFor(() => expect(notify.success).toHaveBeenCalledWith(
      `Ticket ${REF} logged. Confirmation email sent to user@org.com.`));
    expect(notify.success.mock.calls[0][0]).not.toMatch(/deliver/i);
    expect(onSaved).toHaveBeenCalled();
    expect(onClose).toHaveBeenCalled();
  });

  it("says the ticket was logged but the email could not be sent", async () => {
    answer("failed");
    const { onClose, onSaved } = show();
    fill();
    logIt();
    await waitFor(() => expect(notify.warning).toHaveBeenCalledWith(
      `Ticket ${REF} logged, but the confirmation email to user@org.com could not be sent.`));
    expect(notify.success).not.toHaveBeenCalled();
    // The ticket exists: the queue refreshes and the modal closes, as on any success.
    expect(onSaved).toHaveBeenCalled();
    expect(onClose).toHaveBeenCalled();
  });

  it("says when email delivery is not configured, rather than that it failed", async () => {
    answer("not_configured");
    show();
    fill();
    logIt();
    await waitFor(() => expect(notify.warning).toHaveBeenCalledWith(
      `Ticket ${REF} logged. No confirmation email was sent: email delivery is not configured on this server.`));
  });

  it("says nothing about email when no requester address was given", async () => {
    answer("not_requested");
    show();
    fill({ email: "" });
    logIt();
    await waitFor(() => expect(notify.success).toHaveBeenCalledWith(`Ticket ${REF} logged.`));
    expect(api.post.mock.calls[0][1].requester_email).toBeNull();
  });

  it("claims nothing about email it cannot vouch for", async () => {
    // An outcome the console does not recognise is neither "sent" nor "failed".
    answer(undefined);
    show();
    fill();
    logIt();
    await waitFor(() => expect(notify.success).toHaveBeenCalledWith(`Ticket ${REF} logged.`));
    expect(notify.warning).not.toHaveBeenCalled();
  });
});

describe("Log Support Ticket — failure and repeat submission", () => {
  it("shows the server's reason and keeps the form open when the ticket was not created", async () => {
    vi.mocked(api.post).mockRejectedValue({ response: { data: { detail: "value is not a valid email address" } } });
    const { onClose, onSaved } = show();
    fill({ email: "not-an-email" });
    logIt();
    await waitFor(() => expect(notify.error).toHaveBeenCalledWith("value is not a valid email address"));
    expect(notify.success).not.toHaveBeenCalled();
    expect(notify.warning).not.toHaveBeenCalled();
    expect(onSaved).not.toHaveBeenCalled();
    expect(onClose).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Log ticket" })).not.toBeDisabled();
  });

  it("logs one ticket however often the button is pressed while it is being logged", async () => {
    let finish;
    vi.mocked(api.post).mockReturnValue(new Promise((resolve) => { finish = resolve; }));
    show();
    fill();
    logIt();
    logIt();
    logIt();
    expect(api.post).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", { name: "Log ticket" })).toBeDisabled();
    finish({ data: { id: "t-1", case_reference: REF, confirmation: "sent" } });
    await waitFor(() => expect(notify.success).toHaveBeenCalledTimes(1));
  });
});
