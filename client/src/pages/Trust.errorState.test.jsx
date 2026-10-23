// Trust Center — what it says when GET /trust fails.
//
// Both forms (documentation request, security report) are built from that payload, so a failed
// load leaves neither on the page. The message used to say the report form "is below and will
// still submit" — a promise the page could not keep, made to the one reader with an urgent
// security issue. It now says the forms are unavailable and offers the one real next step:
// try the load again, here, rather than sending vulnerability details another way.
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../api", () => ({
  default: { get: vi.fn(), post: vi.fn() },
  errMsg: (e) => e?.message ?? "Something went wrong.",
}));

import api from "../api";
import Trust from "./Trust";

const PAYLOAD = { advisories: [], documents: [], purposes: [], scopes: [] };
const down = () => Promise.reject(Object.assign(new Error("Network Error"), { code: "ERR_NETWORK" }));
const trustGets = () => vi.mocked(api.get).mock.calls.filter(([url]) => url === "/trust").length;

let scrolled;
beforeEach(() => {
  vi.clearAllMocks();
  scrolled = [];
  Element.prototype.scrollIntoView = vi.fn(function scrollIntoView() { scrolled.push(this.id); });
});
afterEach(() => {
  delete Element.prototype.scrollIntoView;
});

const open = (path = "/trust") =>
  render(
    <MemoryRouter initialEntries={[path]}>
      <Routes><Route path="/trust" element={<Trust />} /></Routes>
    </MemoryRouter>,
  );
const failure = () => screen.findByRole("alert");
const FORMS = ["Request trust documentation", "Report a security issue"];

describe("when the Trust Center cannot load", () => {
  it("says the forms are unavailable, and promises no form that is not there", async () => {
    vi.mocked(api.get).mockImplementation(down);
    open();
    const alert = await failure();
    expect(alert).toHaveTextContent(/documentation request and security report forms are unavailable/i);
    expect(alert).not.toHaveTextContent(/below/i);
    expect(alert).not.toHaveTextContent(/will still submit/i);
    for (const form of FORMS) expect(screen.queryByRole("heading", { name: form })).toBeNull();
    expect(screen.queryByRole("textbox")).toBeNull();
    expect(api.post).not.toHaveBeenCalled();
  });

  it("names no other channel: no link, address or contact route to send details to", async () => {
    vi.mocked(api.get).mockImplementation(down);
    open();
    const alert = await failure();
    expect(alert.querySelector("a")).toBeNull();
    expect(alert).not.toHaveTextContent(/@|https?:|\/contact|mailto/i);
    expect(alert).toHaveTextContent(/try again here rather than sending vulnerability details another way/i);
  });

  it("Try again loads it again and brings both forms back", async () => {
    const user = userEvent.setup();
    vi.mocked(api.get).mockImplementation(down);
    open();
    await failure();
    vi.mocked(api.get).mockResolvedValue({ data: PAYLOAD });

    await user.click(screen.getByRole("button", { name: "Try again" }));

    for (const form of FORMS) expect(await screen.findByRole("heading", { name: form })).toBeInTheDocument();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(trustGets()).toBe(2);
  });

  it("a retry that fails again says so again, and can be retried again", async () => {
    const user = userEvent.setup();
    vi.mocked(api.get).mockImplementation(down);
    open();
    await failure();
    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(await failure()).toHaveTextContent(/forms are unavailable/i);
    expect(screen.getByRole("button", { name: "Try again" })).toBeEnabled();
    expect(trustGets()).toBe(2);
  });

  it("arriving for the report form during an outage still lands there once a retry succeeds", async () => {
    const user = userEvent.setup();
    vi.mocked(api.get).mockImplementation(down);
    open("/trust#report");
    await failure();
    expect(scrolled).toEqual([]);
    vi.mocked(api.get).mockResolvedValue({ data: PAYLOAD });
    await user.click(screen.getByRole("button", { name: "Try again" }));
    await screen.findByRole("heading", { name: "Report a security issue" });
    await waitFor(() => expect(document.activeElement).toBe(document.getElementById("report")));
    expect(scrolled).toEqual(["report"]);
  });
});

describe("when it loads", () => {
  it("shows both forms and no error", async () => {
    vi.mocked(api.get).mockResolvedValue({ data: PAYLOAD });
    open();
    for (const form of FORMS) expect(await screen.findByRole("heading", { name: form })).toBeInTheDocument();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.queryByRole("button", { name: "Try again" })).toBeNull();
  });
});

// ── the header's shortcuts to the two forms ──────────────────────────────────────────────
// They used to be plain #evidence / #report anchors at all times, so during a load or after a
// failure they jumped to sections that were not on the page.
const SHORTCUTS = [["Request documents", "#evidence", "evidence"], ["Report an issue", "#report", "report"]];
const shortcut = (name) => screen.getByRole("link", { name });
const expectUnavailable = () => {
  for (const [name, , section] of SHORTCUTS) {
    expect(shortcut(name)).toHaveAttribute("aria-disabled", "true");
    expect(shortcut(name)).not.toHaveAttribute("href");
    expect(shortcut(name).tabIndex).toBe(-1);                 // not in the tab order
    expect(document.getElementById(section)).toBeNull();      // and nothing to jump to
  }
};
const expectAvailable = () => {
  for (const [name, href, section] of SHORTCUTS) {
    expect(shortcut(name)).toHaveAttribute("href", href);
    expect(shortcut(name)).not.toHaveAttribute("aria-disabled");
    expect(document.getElementById(section)).not.toBeNull();  // the destination exists
  }
};

describe("the header shortcuts", () => {
  it("are unavailable while the Trust Center is loading", async () => {
    vi.mocked(api.get).mockReturnValue(new Promise(() => {}));
    open();
    expect(await screen.findByText("Loading…")).toBeInTheDocument();
    expectUnavailable();
  });

  it("stay unavailable after a failed load, beside the alert and its Try again", async () => {
    vi.mocked(api.get).mockImplementation(down);
    open();
    await failure();
    expectUnavailable();
    expect(screen.getByRole("button", { name: "Try again" })).toBeEnabled();
  });

  it("come back, pointing where they always did, once Try again succeeds", async () => {
    const user = userEvent.setup();
    vi.mocked(api.get).mockImplementation(down);
    open();
    await failure();
    vi.mocked(api.get).mockResolvedValue({ data: PAYLOAD });
    await user.click(screen.getByRole("button", { name: "Try again" }));
    await screen.findByRole("heading", { name: "Report a security issue" });
    expectAvailable();
  });

  it("are ordinary keyboard links once loaded, and following one submits nothing", async () => {
    const user = userEvent.setup();
    vi.mocked(api.get).mockResolvedValue({ data: PAYLOAD });
    open();
    await screen.findByRole("heading", { name: "Request trust documentation" });
    expectAvailable();
    // Tab order: home logo, then the two shortcuts.
    await user.tab();
    expect(screen.getByRole("link", { name: "ZoikoStream home" })).toHaveFocus();
    await user.tab();
    expect(shortcut("Request documents")).toHaveFocus();
    await user.tab();
    expect(shortcut("Report an issue")).toHaveFocus();
    await user.keyboard("{Enter}");
    expect(api.post).not.toHaveBeenCalled();
  });

  it("are skipped by the keyboard while unavailable", async () => {
    const user = userEvent.setup();
    vi.mocked(api.get).mockImplementation(down);
    open();
    await failure();
    await user.tab();
    expect(screen.getByRole("link", { name: "ZoikoStream home" })).toHaveFocus();
    await user.tab();
    expect(screen.getByRole("button", { name: "Try again" })).toHaveFocus();
  });
});
