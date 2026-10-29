// The Developer Platform footer:
// - Feedback and Accessibility are action buttons opening dialogs.
// - Security and Status are links.
// - Terms is plain text (no destination exists yet).
//
// In the Android app the 44px touch-target floor (index.css) made each link 44px tall with its
// text pinned to the top, 13px above the plain text on the same row: the defect the Support &
// Status footer had. jsdom cannot lay that out; the geometry was measured in Chromium against
// the mobile build (320–768px: text level on every row, links still 44px; web unchanged).
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../api", () => ({
  default: {
    get: vi.fn(),
    post: vi.fn(),
  },
  errMsg: (e) => e?.response?.data?.detail ?? e?.message ?? "Something went wrong.",
}));

import api from "../../api";
import { ThemeProvider } from "../../theme/ThemeContext";
import DeveloperPlatform from "./DeveloperPlatform";

// What POST /organization/feedback really answers today (server/app/routers/organization.py):
// the message is validated, then refused — nothing stores or forwards console feedback yet.
const UNDELIVERED = {
  response: {
    status: 503,
    data: { detail: { code: "FEEDBACK_DELIVERY_UNAVAILABLE", message: "This message was not sent." } },
  },
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.get).mockResolvedValue({ data: {} });
  vi.mocked(api.post).mockRejectedValue(UNDELIVERED);
});

function renderPage() {
  return render(
    <ThemeProvider>
      <MemoryRouter initialEntries={["/organization/developers"]}>
        <Routes>
          <Route path="/organization/developers" element={<DeveloperPlatform />} />
          <Route path="/organization/support" element={<h1>Support &amp; Status</h1>} />
          <Route path="/contact" element={<h1>Contact support</h1>} />
        </Routes>
      </MemoryRouter>
    </ThemeProvider>
  );
}

const footer = async () => (await screen.findByText(/All rights reserved/)).closest("footer");

describe("the Developer Platform footer", () => {
  it("keeps its destinations as links, actions as buttons, and terms as text", async () => {
    renderPage();
    const f = await footer();
    const links = within(f).getAllByRole("link");
    expect(links.map((a) => [a.textContent.trim(), a.getAttribute("href")])).toEqual([
      ["Security", "/organization/settings?tab=security"],
      ["Status", "/organization/support"],
    ]);

    const feedbackBtn = within(f).getByRole("button", { name: "Feedback" });
    expect(feedbackBtn.tagName).toBe("BUTTON");
    expect(feedbackBtn.getAttribute("type")).toBe("button");

    const a11yBtn = within(f).getByRole("button", { name: "Accessibility" });
    expect(a11yBtn.tagName).toBe("BUTTON");
    expect(a11yBtn.getAttribute("type")).toBe("button");

    expect(within(f).getByText("Terms").tagName).toBe("SPAN");
  });

  it("centres every link and button's text in its touch target", async () => {
    renderPage();
    const f = await footer();
    for (const el of [...within(f).getAllByRole("link"), ...within(f).getAllByRole("button")]) {
      expect(el.className.split(/\s+/)).toEqual(expect.arrayContaining(["inline-flex", "items-center"]));
      expect(el.className).toMatch(/focus-visible:/);
    }
  });

  it("still navigates from the keyboard", async () => {
    const user = userEvent.setup();
    renderPage();
    within(await footer()).getByRole("link", { name: "Status" }).focus();
    await user.keyboard("{Enter}");
    expect(await screen.findByRole("heading", { name: "Support & Status" })).toBeInTheDocument();
  });

  it("opens Feedback dialog, validates min characters, says honestly it was not sent, and restores focus", async () => {
    const user = userEvent.setup();
    renderPage();
    const f = await footer();
    const feedbackBtn = within(f).getByRole("button", { name: "Feedback" });
    await user.click(feedbackBtn);

    const dialog = await screen.findByRole("dialog", { name: /Developer Platform Feedback/i });
    expect(dialog).toBeInTheDocument();

    const submitBtn = within(dialog).getByRole("button", { name: "Submit Feedback" });
    const textarea = within(dialog).getByRole("textbox", { name: /Your feedback/i });

    // Submit with empty message -> validation error
    await user.click(submitBtn);
    expect(await screen.findByRole("alert")).toHaveTextContent(/Please enter your feedback/i);
    expect(api.post).not.toHaveBeenCalled();

    // Type less than 5 characters -> validation error
    await user.type(textarea, "hey");
    await user.click(submitBtn);
    expect(await screen.findByRole("alert")).toHaveTextContent(/at least 5 characters/i);
    expect(api.post).not.toHaveBeenCalled();

    // Fill valid feedback and select category
    await user.type(textarea, " - Webhook payload signature is verified!");
    await user.click(submitBtn);

    expect(api.post).toHaveBeenCalledWith(
      "/organization/feedback",
      { category: "developer_platform", message: "hey - Webhook payload signature is verified!" },
      expect.objectContaining({ signal: expect.any(AbortSignal) })
    );

    // The server did not deliver it, so the dialog must not claim it did.
    const notice = await within(dialog).findByRole("alert");
    expect(notice).toHaveTextContent(/your feedback was not sent/i);
    expect(within(notice).getByRole("link", { name: "Contact support" })).toHaveAttribute("href", "/contact");
    expect(screen.queryByText(/thank you|received|submitted successfully/i)).not.toBeInTheDocument();
    expect(textarea).toHaveValue("hey - Webhook payload signature is verified!");   // nothing lost

    // Close via Cancel and ensure focus is restored to the feedback trigger
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));

    await waitFor(() => {
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    });
    expect(feedbackBtn).toHaveFocus();
  });

  it("displays server error when feedback submission fails", async () => {
    vi.mocked(api.post).mockRejectedValueOnce({
      response: { data: { detail: "Feedback submission rate limit exceeded." } },
    });

    const user = userEvent.setup();
    renderPage();
    const f = await footer();
    await user.click(within(f).getByRole("button", { name: "Feedback" }));

    const dialog = await screen.findByRole("dialog", { name: /Developer Platform Feedback/i });
    const textarea = within(dialog).getByRole("textbox", { name: /Your feedback/i });
    await user.type(textarea, "Testing failure response handling");
    await user.click(within(dialog).getByRole("button", { name: "Submit Feedback" }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Feedback submission rate limit exceeded.");

    // Dismiss with Cancel button
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await waitFor(() => {
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    });
  });

  it("opens Accessibility dialog with keyboard shortcuts and restores focus on close", async () => {
    const user = userEvent.setup();
    renderPage();
    const f = await footer();
    const a11yBtn = within(f).getByRole("button", { name: "Accessibility" });
    await user.click(a11yBtn);

    const dialog = await screen.findByRole("dialog", { name: /Accessibility & Keyboard Navigation/i });
    expect(dialog).toBeInTheDocument();

    expect(within(dialog).getByText(/Tab \/ Shift \+ Tab/i)).toBeInTheDocument();
    expect(within(dialog).getByText(/Escape \(Esc\)/i)).toBeInTheDocument();
    expect(within(dialog).getByText(/Visual & Contrast Standards/i)).toBeInTheDocument();
    expect(within(dialog).getByText(/Motion Sensitivity/i)).toBeInTheDocument();

    // Close via Close button
    const closeBtns = within(dialog).getAllByRole("button", { name: "Close" });
    expect(closeBtns.length).toBeGreaterThanOrEqual(1);
    await user.click(closeBtns[closeBtns.length - 1]);

    await waitFor(() => {
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    });
    expect(a11yBtn).toHaveFocus();
  });

  it("closes dialogs with Escape key and restores focus to the trigger", async () => {
    const user = userEvent.setup();
    renderPage();
    const f = await footer();
    const feedbackBtn = within(f).getByRole("button", { name: "Feedback" });
    await user.click(feedbackBtn);

    expect(await screen.findByRole("dialog", { name: /Developer Platform Feedback/i })).toBeInTheDocument();

    await user.keyboard("{Escape}");

    await waitFor(() => {
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    });
    expect(feedbackBtn).toHaveFocus();
  });

  it("moves focus into the dialog on open and keeps Tab inside it", async () => {
    const user = userEvent.setup();
    renderPage();
    const f = await footer();
    await user.click(within(f).getByRole("button", { name: "Feedback" }));
    const dialog = await screen.findByRole("dialog", { name: /Developer Platform Feedback/i });
    expect(dialog).toHaveFocus();                  // its name is announced, not the page's

    const closeX = within(dialog).getByRole("button", { name: "Close" });
    const submit = within(dialog).getByRole("button", { name: "Submit Feedback" });
    await user.tab();
    expect(closeX).toHaveFocus();                  // first control
    submit.focus();
    await user.tab();
    expect(closeX).toHaveFocus();                  // wraps from the last control to the first
    await user.tab({ shift: true });
    expect(submit).toHaveFocus();                  // and back the other way
    expect(dialog.contains(document.activeElement)).toBe(true);
  });

  it("closing during a submission cancels it, and a late answer cannot leave stale state", async () => {
    const pending = {};
    vi.mocked(api.post).mockImplementationOnce((url, body, config) => new Promise((resolve) => {
      pending.finish = () => resolve({ data: { status: "delivered" } });
      config.signal.addEventListener("abort", () => { pending.aborted = true; });
    }));
    const { notify } = await import("../../ui/Toast");
    const success = vi.spyOn(notify, "success");

    const user = userEvent.setup();
    renderPage();
    const f = await footer();
    const trigger = within(f).getByRole("button", { name: "Feedback" });
    await user.click(trigger);
    const dialog = await screen.findByRole("dialog", { name: /Developer Platform Feedback/i });
    await user.type(within(dialog).getByRole("textbox", { name: /Your feedback/i }), "Closing mid-request");
    await user.click(within(dialog).getByRole("button", { name: "Submit Feedback" }));
    expect(within(dialog).getByRole("button", { name: "Submitting..." })).toBeDisabled();

    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(pending.aborted).toBe(true);            // the request was cancelled
    expect(trigger).toHaveFocus();

    await act(async () => pending.finish());       // ...and its answer arrives anyway
    await user.click(trigger);
    const reopened = await screen.findByRole("dialog", { name: /Developer Platform Feedback/i });
    expect(within(reopened).getByRole("textbox", { name: /Your feedback/i })).toHaveValue("");
    expect(within(reopened).getByRole("button", { name: "Submit Feedback" })).toBeEnabled();
    expect(screen.queryByText(/was sent/i)).not.toBeInTheDocument();
    expect(success).not.toHaveBeenCalled();
  });

  it("confirms only when the server says the feedback was delivered", async () => {
    vi.mocked(api.post).mockResolvedValueOnce({ data: { status: "delivered" } });
    const user = userEvent.setup();
    renderPage();
    await user.click(within(await footer()).getByRole("button", { name: "Feedback" }));
    const dialog = await screen.findByRole("dialog", { name: /Developer Platform Feedback/i });
    await user.type(within(dialog).getByRole("textbox", { name: /Your feedback/i }), "Delivered for real");
    await user.click(within(dialog).getByRole("button", { name: "Submit Feedback" }));
    expect(await screen.findByRole("dialog", { name: "Feedback sent" })).toHaveTextContent(/was sent to the ZoikoStream team/i);
  });

  it("treats a success status that does not say delivered as not sent", async () => {
    vi.mocked(api.post).mockResolvedValueOnce({ data: { status: "received" } });
    const user = userEvent.setup();
    renderPage();
    await user.click(within(await footer()).getByRole("button", { name: "Feedback" }));
    const dialog = await screen.findByRole("dialog", { name: /Developer Platform Feedback/i });
    await user.type(within(dialog).getByRole("textbox", { name: /Your feedback/i }), "Ambiguous answer");
    await user.click(within(dialog).getByRole("button", { name: "Submit Feedback" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent(/was not sent/i);
    expect(screen.queryByRole("dialog", { name: "Feedback sent" })).not.toBeInTheDocument();
  });

  it("does not close the feedback form on a click outside it", async () => {
    const user = userEvent.setup();
    renderPage();
    await user.click(within(await footer()).getByRole("button", { name: "Feedback" }));
    const dialog = await screen.findByRole("dialog", { name: /Developer Platform Feedback/i });
    await user.click(dialog.parentElement);       // the overlay area around the panel
    expect(screen.getByRole("dialog", { name: /Developer Platform Feedback/i })).toBeInTheDocument();
  });
});
