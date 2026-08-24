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

/** Fill every required field. `country` and `topic` are custom Dropdowns, so they are chosen
 *  by clicking their option rather than by selectOptions. */
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

  /** Country is a native <select>; topic is the custom Dropdown (button + role=listbox).
   *
   *  The topic option MUST be queried inside the listbox: native <option> elements carry an
   *  implicit role="option", so a bare getAllByRole("option") also matches all ~250 country
   *  entries and picks a country instead of a topic, leaving the form invalid. */
  async function completeForm(user) {
    await fillForm(user);
    await user.selectOptions(document.getElementById("ct-country"), "GB");
    await user.click(screen.getByRole("button", { name: /what can we help with/i }));
    const listbox = screen.getByRole("listbox");
    await user.click(within(listbox).getAllByRole("option")[0]);
  }

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
    expect(await screen.findByTestId("contact-success")).toHaveTextContent(/has been sent/i);
    expect(screen.getByLabelText(/first name/i)).toHaveValue("");
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
