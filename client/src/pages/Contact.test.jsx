// Contact form submission tests.
//
// The point of this suite: the enquiry must be posted to OUR API and must never hand the
// browser to an external mail handler. The old implementation navigated to a mail-protocol URL,
// which on an externally-hosted mail domain dropped the visitor on the mail host's sign-in
// page — it looked like the product had redirected them off-site, and the enquiry was never
// actually sent unless they finished composing it by hand.
//
// The api module is mocked at its boundary, so no backend is needed.
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../api", () => ({
  default: { get: vi.fn(), post: vi.fn() },
  errMsg: (e) => e?.message ?? "Something went wrong.",
}));

import api from "../api";
import Contact from "./Contact";

function renderAt(path = "/contact") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Contact />
    </MemoryRouter>,
  );
}

/** Fill the text fields; the native selects are completed separately. */
async function fillForm(user, { message = "Need help" } = {}) {
  await user.type(screen.getByLabelText(/first name/i), "Ada");
  await user.type(screen.getByLabelText(/last name/i), "Lovelace");
  await user.type(screen.getByLabelText(/work email/i), "ada@example.com");
  const box = screen.getByLabelText(/how can we help/i);
  await user.clear(box);
  await user.type(box, message);
}

let assignSpy;
let hrefSetter;

beforeEach(() => {
  vi.clearAllMocks();
  api.post.mockResolvedValue({ data: { received: true } });
  // Detect ANY attempt to navigate the browser away, however it is spelled.
  assignSpy = vi.fn();
  hrefSetter = vi.fn();
  delete window.location;
  window.location = {
    assign: assignSpy,
    replace: vi.fn(),
    get href() { return "http://localhost/contact"; },
    set href(v) { hrefSetter(v); },
    search: "",
    pathname: "/contact",
    hash: "",
  };
  window.open = vi.fn();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("Contact form", () => {
  it("renders the form", () => {
    renderAt();
    expect(screen.getByLabelText(/first name/i)).toBeInTheDocument();
    expect(screen.getByTestId("contact-submit")).toBeInTheDocument();
  });

  it("blocks submission when required fields are empty", async () => {
    const user = userEvent.setup();
    renderAt();
    await user.click(screen.getByTestId("contact-submit"));
    expect(api.post).not.toHaveBeenCalled();
    expect(await screen.findAllByText("Required")).not.toHaveLength(0);
  });

  it("rejects a malformed email without calling the API", async () => {
    const user = userEvent.setup();
    renderAt();
    await user.type(screen.getByLabelText(/first name/i), "Ada");
    await user.type(screen.getByLabelText(/last name/i), "Lovelace");
    await user.type(screen.getByLabelText(/work email/i), "not-an-email");
    await user.click(screen.getByTestId("contact-submit"));
    expect(await screen.findByText(/valid email address/i)).toBeInTheDocument();
    expect(api.post).not.toHaveBeenCalled();
  });

  it("never navigates the browser to an external mail handler", async () => {
    const user = userEvent.setup();
    renderAt();
    await fillForm(user);
    await user.click(screen.getByTestId("contact-submit"));
    // Whether or not validation passed, nothing may leave the SPA.
    expect(assignSpy).not.toHaveBeenCalled();
    expect(window.open).not.toHaveBeenCalled();
    for (const call of hrefSetter.mock.calls) {
      expect(String(call[0]).toLowerCase()).not.toContain("mail");
    }
  }, 20000);

  it("sends no destination address — the backend decides where it goes", async () => {
    const user = userEvent.setup();
    renderAt();
    await fillForm(user);
    // Country and topic are required; submit may be blocked, so assert on any call made.
    await user.click(screen.getByTestId("contact-submit"));
    for (const [, body] of api.post.mock.calls) {
      for (const forbidden of ["to", "recipient", "bcc", "cc", "smtp_host", "from"]) {
        expect(body).not.toHaveProperty(forbidden);
      }
    }
  }, 20000);

  async function completeForm(user) {
    await fillForm(user);
    await user.selectOptions(document.getElementById("ct-country"), "GB");
    await user.selectOptions(screen.getByRole("combobox", { name: /what can we help with/i }), "platform");
  }

  it("uses a native, labeled topic select with responsive full-width styling", () => {
    renderAt();
    const topic = screen.getByRole("combobox", { name: /what can we help with/i });

    expect(topic.tagName).toBe("SELECT");
    expect(topic).toHaveAttribute("aria-required", "true");
    expect(topic).toHaveClass("w-full", "appearance-none", "rounded-xl", "bg-white");
    expect(topic.closest(".grid")).toHaveClass("sm:grid-cols-2");
    expect(topic).toHaveValue("");
    expect(within(topic).getByRole("option", { name: "Platform evaluation & enterprise sales" }))
      .toHaveValue("platform");
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
  });

  it("keeps the topic control fluid within the form's mobile, tablet, and desktop layouts", () => {
    renderAt();
    const form = document.querySelector("main form");
    const topic = screen.getByRole("combobox", { name: /what can we help with/i });

    expect(form.closest("section")).toHaveClass("grid", "lg:grid-cols-[minmax(0,1fr)_360px]");
    expect(topic.closest(".grid")).toHaveClass("grid", "sm:grid-cols-2");
    expect(topic).toHaveClass("w-full");
    expect(topic).not.toHaveClass("absolute", "fixed");
    expect(topic.parentElement).toHaveClass("relative");
    expect(topic.parentElement.parentElement).toHaveClass("min-w-0");
  });

  it("exposes topic validation to assistive technology and clears it after selection", async () => {
    const user = userEvent.setup();
    renderAt();
    const topic = screen.getByRole("combobox", { name: /what can we help with/i });

    await user.click(screen.getByTestId("contact-submit"));
    expect(topic).toHaveAttribute("aria-invalid", "true");
    expect(topic).toHaveAttribute("aria-describedby", "ct-topic-error");
    expect(document.getElementById("ct-topic-error")).toHaveTextContent("Required");

    await user.selectOptions(topic, "platform");
    expect(topic).not.toHaveAttribute("aria-invalid");
    expect(topic).not.toHaveAttribute("aria-describedby");
  });

  it("posts to the contact endpoint when the form is complete", async () => {
    const user = userEvent.setup();
    renderAt();
    await completeForm(user);
    await user.click(screen.getByTestId("contact-submit"));
    await waitFor(() => expect(api.post).toHaveBeenCalledTimes(1));
    const [url, body] = api.post.mock.calls[0];
    expect(url).toBe("/contact");
    expect(body.email).toBe("ada@example.com");
    expect(body.first).toBe("Ada");
    expect(body).not.toHaveProperty("to");
    // Nothing left the SPA.
    expect(assignSpy).not.toHaveBeenCalled();
    expect(window.open).not.toHaveBeenCalled();
  }, 20000);

  it("shows a success message and clears the form", async () => {
    const user = userEvent.setup();
    renderAt();
    await completeForm(user);
    await user.click(screen.getByTestId("contact-submit"));
    expect(await screen.findByTestId("contact-success")).toHaveTextContent(/we.ve received your inquiry/i);
    expect(screen.getByLabelText(/first name/i)).toHaveValue("");
  }, 20000);

  it("says a confirmation will go to the submitted address, masked", async () => {
    const user = userEvent.setup();
    renderAt();
    await completeForm(user);
    await user.click(screen.getByTestId("contact-submit"));
    const success = await screen.findByTestId("contact-success");
    expect(success).toHaveTextContent("We’ll send a confirmation to a•••@example.com.");
    expect(success).not.toHaveTextContent("ada@example.com");
  }, 20000);

  it("never claims an email was sent — delivery happens after the API answers", async () => {
    // The API's 202 means "received"; the provider is called afterwards, so the page cannot
    // know whether either email went out and must not say so.
    const user = userEvent.setup();
    renderAt();
    await completeForm(user);
    await user.click(screen.getByTestId("contact-submit"));
    const success = await screen.findByTestId("contact-success");
    expect(success).not.toHaveTextContent(/has been sent|was sent|email sent/i);
  }, 20000);

  it("shows an error and keeps the entered data when the API fails", async () => {
    const user = userEvent.setup();
    api.post.mockRejectedValue(new Error("Too many attempts."));
    renderAt();
    await completeForm(user);
    await user.click(screen.getByTestId("contact-submit"));
    expect(await screen.findByTestId("contact-error")).toHaveTextContent(/too many attempts/i);
    // Data survives so the visitor can retry without retyping.
    expect(screen.getByLabelText(/first name/i)).toHaveValue("Ada");
    expect(screen.queryByTestId("contact-success")).not.toBeInTheDocument();
    // And nothing on the page promises a confirmation for a submission that failed.
    expect(screen.queryByText(/confirmation/i)).not.toBeInTheDocument();
  }, 20000);

  it("disables the button while sending, preventing a double submit", async () => {
    const user = userEvent.setup();
    let release;
    api.post.mockReturnValue(new Promise((res) => { release = res; }));
    renderAt();
    await completeForm(user);
    const btn = screen.getByTestId("contact-submit");
    await user.click(btn);
    await waitFor(() => expect(btn).toBeDisabled());
    expect(btn).toHaveTextContent(/sending/i);
    await user.click(btn);                       // second click must be a no-op
    expect(api.post).toHaveBeenCalledTimes(1);
    release({ data: { received: true } });
    await waitFor(() => expect(btn).not.toBeDisabled());
  }, 20000);

  it("preserves the plan from the query string", () => {
    renderAt("/contact?plan=Pro");
    expect(screen.getByLabelText(/how can we help/i).value).toContain("Requested plan: Pro");
  });

  it("shows no plan text on a plain visit", () => {
    renderAt("/contact");
    expect(screen.getByLabelText(/how can we help/i).value).not.toContain("Requested plan");
  });
});
