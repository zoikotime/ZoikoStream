// "Remember me for this event" — the checkbox, and what it does and does not store.
//
// The credential itself is unchanged: POST /register returns an opaque server-signed token
// bound to this event (server/test_remembered_registration.py pins that half). All the
// checkbox decides is how long this browser KEEPS it — localStorage when ticked, sessionStorage
// when not. What is never stored either way is the name AS PROOF: that is
// registration DATA, and treating them as evidence of access is the failure this design
// exists to avoid. They are kept, with consent, as a typing convenience — see
// utils/viewerProfile.js and pages/watch/ViewerProfile.test.jsx.
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../api", () => ({
  default: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
  errMsg: (e, fallback) => fallback ?? "Something went wrong.",
}));

import api from "../../api";
import { ThemeProvider } from "../../theme/ThemeContext";
import RegistrationGate from "./RegistrationGate";

const EVENT_ID = "7ac5caa2-2a78-42d3-b3ed-49e0f61d7d13";
const TOKEN = "opaque.server.signed.credential";
const NAME = "NANI";
const EMAIL = "gdbdata3@gmail.com";

const onRegistered = vi.fn();

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  sessionStorage.clear();
  vi.mocked(api.post).mockResolvedValue({ data: { token: TOKEN } });
});

const renderGate = () =>
  render(
    <ThemeProvider>
      <RegistrationGate eventId={EVENT_ID} eventTitle="TESTING" onRegistered={onRegistered} />
    </ThemeProvider>
  );

const checkbox = () => screen.getByRole("checkbox", { name: /remember me for this event/i });

async function fillForm(user) {
  await user.type(screen.getByPlaceholderText(/enter your name|jane doe|full name|name/i), NAME);
}

const register = (user) =>
  user.click(screen.getByRole("button", { name: /continue to watch/i }));

describe("the checkbox", () => {
  it("appears with its helper text", () => {
    renderGate();
    expect(checkbox()).toBeInTheDocument();
    expect(screen.getByText(/skip this form next time on this device/i)).toBeInTheDocument();
  });

  it("is unticked by default", () => {
    renderGate();
    // Keeping a credential past this visit is a choice, not a default.
    expect(checkbox()).not.toBeChecked();
  });

  it("sits below the name field and above the submit button", () => {
    const { container } = renderGate();
    const order = [...container.querySelectorAll("input, button")];
    const name = order.findIndex((el) => el.getAttribute("type") !== "checkbox"
      && el.tagName === "INPUT");
    const box = order.findIndex((el) => el.getAttribute("type") === "checkbox");
    const submit = order.findIndex((el) => el.getAttribute("type") === "submit");
    expect(name).toBeGreaterThanOrEqual(0);
    expect(box).toBeGreaterThan(name);
    expect(submit).toBeGreaterThan(box);
  });

  it("asks for a name and nothing else", () => {
    const { container } = renderGate();
    // No email input, and no field that could collect one.
    expect(container.querySelector('input[type="email"]')).toBeNull();
    expect(screen.queryByText(/email/i)).toBeNull();
    expect(screen.queryByText(/registration required/i)).toBeNull();
    // Heading and description are asserted separately — "enter your name" also matches the
    // input's own placeholder, so an unscoped query would match three nodes.
    expect(screen.getByRole("heading", { name: /^enter your name$/i })).toBeTruthy();
    expect(screen.getByText("Enter your name to watch the live event.")).toBeTruthy();
  });

  it("refuses an empty name", async () => {
    const user = userEvent.setup();
    renderGate();
    await user.click(screen.getByRole("button", { name: /continue to watch/i }));
    expect(api.post).not.toHaveBeenCalled();
  });
});

describe("registering", () => {
  it("works with the box unticked, and reports that choice", async () => {
    const user = userEvent.setup();
    renderGate();
    await fillForm(user);
    await register(user);

    await waitFor(() => expect(onRegistered).toHaveBeenCalled());
    expect(api.post).toHaveBeenCalledWith(`/events/${EVENT_ID}/register`, {
      name: NAME,
    });
    expect(onRegistered).toHaveBeenCalledWith(TOKEN, false);
  });

  it("reports the choice when the box is ticked", async () => {
    const user = userEvent.setup();
    renderGate();
    await fillForm(user);
    await user.click(checkbox());
    await register(user);

    await waitFor(() => expect(onRegistered).toHaveBeenCalledWith(TOKEN, true));
  });

  it("persists nothing itself, whichever way the box is set", async () => {
    // This component used to write localStorage unconditionally, which meant a viewer who
    // declined was remembered anyway. Storage is now EventWatch's single decision.
    const user = userEvent.setup();
    renderGate();
    await fillForm(user);
    await user.click(checkbox());
    await register(user);

    await waitFor(() => expect(onRegistered).toHaveBeenCalled());
    expect(localStorage.getItem(`zk_reg_${EVENT_ID}`)).toBeNull();
    expect(sessionStorage.getItem(`zk_reg_${EVENT_ID}`)).toBeNull();
  });

  it("never writes the name as PROOF of anything", async () => {
    // Narrowed, not relaxed. This used to assert that the name appeared in no
    // store at all, which was a fair proxy while nothing remembered the viewer. The device
    // profile (utils/viewerProfile) now deliberately keeps exactly those two fields to save
    // a returning viewer from retyping them — a convenience that grants nothing.
    //
    // The invariant underneath is unchanged and is what is asserted here: identity is never
    // stored as evidence. No store may carry a "registered" flag, the CREDENTIAL keys hold
    // the opaque token and nothing else, and the profile holds a name and
    // nothing else — in particular never the token, which is what would turn a convenience
    // into a second way in.
    const user = userEvent.setup();
    renderGate();
    await fillForm(user);
    await user.click(checkbox());
    await register(user);

    await waitFor(() => expect(onRegistered).toHaveBeenCalled());

    for (const store of [localStorage, sessionStorage]) {
      const dump = JSON.stringify(Object.entries({ ...store }));
      expect(dump.toLowerCase()).not.toContain("registered");
    }
    // The credential keys carry the token alone — no identity smuggled in beside it.
    for (const store of [localStorage, sessionStorage]) {
      const credential = store.getItem(`zk_reg_${EVENT_ID}`);
      if (credential !== null) {
        expect(credential).not.toContain(NAME);
        expect(credential).not.toContain(EMAIL);
      }
    }
    // The profile carries identity alone — and no credential.
    const profile = JSON.parse(localStorage.getItem("zk_viewer_profile"));
    expect(Object.keys(profile).sort()).toEqual(["name"]);
    expect(JSON.stringify(profile)).not.toContain(TOKEN);

    // What DOES travel onward is the opaque credential, and nothing else.
    expect(onRegistered.mock.calls[0][0]).toBe(TOKEN);
  });

  it("remembers the viewer only when the box is ticked", async () => {
    const user = userEvent.setup();
    renderGate();
    await fillForm(user);
    await register(user);

    await waitFor(() => expect(onRegistered).toHaveBeenCalled());
    expect(localStorage.getItem("zk_viewer_profile")).toBeNull();
  });

  it("does not report a registration that failed", async () => {
    vi.mocked(api.post).mockRejectedValueOnce({ response: { status: 500 } });
    const user = userEvent.setup();
    renderGate();
    await fillForm(user);
    await user.click(checkbox());
    await register(user);

    await waitFor(() => expect(api.post).toHaveBeenCalled());
    expect(onRegistered).not.toHaveBeenCalled();
    expect(localStorage.getItem(`zk_reg_${EVENT_ID}`)).toBeNull();
  });
});
