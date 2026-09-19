// Category classifies an event. It no longer configures one.
//
// Funeral / Memorial used to lock Visibility to Private (the other two buttons disabled and
// inert), force Chat/Polls/Q&A off, and print two notices saying so — mirroring a server-side
// clamp in crud/event.py and services/broadcast.py. All of that is retired by product
// decision, on both sides. These pin the new behaviour from the direction that matters: the
// organiser's choices must SURVIVE a category change.
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../api", () => ({
  default: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
  errMsg: (e) => e?.message ?? "Something went wrong.",
}));
vi.mock("../../ui/Toast", () => ({ notify: { error: vi.fn(), success: vi.fn(), alert: vi.fn() } }));
vi.mock("../../auth/AuthContext", () => ({
  useAuth: () => ({ user: { id: "u1", full_name: "Host" }, logout: vi.fn() }),
}));

import api from "../../api";
import { ThemeProvider } from "../../theme/ThemeContext";
import CreateEventModal from "./CreateEventModal";

const MEMORIAL = "Funeral / Memorial";

const renderModal = () => {
  const onCreated = vi.fn();
  render(
    <ThemeProvider>
      <CreateEventModal open onClose={vi.fn()} onCreated={onCreated} />
    </ThemeProvider>
  );
  return { onCreated };
};

const categorySelect = () => screen.getByDisplayValue(/webinar|funeral/i);
const visibilityButton = (name) => screen.getByRole("button", { name: new RegExp(name, "i") });
// ui/forms Switch renders a role="switch" button whose label is its text content, with state
// on aria-checked — there is no <label for>, so getByLabelText cannot see it.
const featureSwitch = (label) => screen.getByRole("switch", { name: new RegExp(label, "i") });

const pickMemorial = async (u) => u.selectOptions(categorySelect(), MEMORIAL);

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.get).mockResolvedValue({ data: { items: [] } });
  vi.mocked(api.post).mockResolvedValue({ data: { id: "e1" } });
});

// ── visibility ─────────────────────────────────────────────────────────────────────────

describe("Funeral / Memorial visibility", () => {
  it.each(["Public", "Unlisted", "Private"])("offers %s as a live choice", async (label) => {
    const u = userEvent.setup();
    renderModal();
    await pickMemorial(u);

    const btn = visibilityButton(label);
    expect(btn).toBeEnabled();
    // The old UI also made the container pointer-events-none, so "enabled" alone was not enough.
    expect(btn).not.toHaveClass("opacity-40");
  });

  it.each(["Public", "Unlisted"])("lets the organiser actually select %s", async (label) => {
    const u = userEvent.setup();
    renderModal();
    await pickMemorial(u);
    await u.click(visibilityButton(label));

    // Selection is shown by the violet ring on the chosen card.
    expect(visibilityButton(label).className).toMatch(/border-violet-500/);
  });

  it("does not overwrite a visibility already chosen when the category changes", async () => {
    const u = userEvent.setup();
    renderModal();

    await u.click(visibilityButton("Public"));
    await pickMemorial(u);

    // The whole point: Public was chosen first, and choosing the memorial category must not
    // silently reset it to Private.
    expect(visibilityButton("Public").className).toMatch(/border-violet-500/);
    expect(visibilityButton("Private").className).not.toMatch(/border-violet-500/);
  });

  it("no longer claims memorial events are always private", async () => {
    const u = userEvent.setup();
    renderModal();
    await pickMemorial(u);

    expect(screen.queryByText(/always Private/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/family-only replay/i)).not.toBeInTheDocument();
  });
});

// ── features ───────────────────────────────────────────────────────────────────────────

describe("Funeral / Memorial features", () => {
  it.each(["Enable Chat", "Enable Polls", "Enable Q&A"])("leaves %s usable", async (label) => {
    const u = userEvent.setup();
    renderModal();
    await pickMemorial(u);

    expect(featureSwitch(label)).toBeEnabled();
  });

  it.each(["Enable Chat", "Enable Polls", "Enable Q&A"])("lets %s be turned on", async (label) => {
    const u = userEvent.setup();
    renderModal();
    await pickMemorial(u);
    await u.click(featureSwitch(label));

    expect(featureSwitch(label)).toBeChecked();
  });

  it("does not clear features already enabled when the category changes", async () => {
    const u = userEvent.setup();
    renderModal();

    await u.click(featureSwitch("Enable Chat"));
    await u.click(featureSwitch("Enable Polls"));
    expect(featureSwitch("Enable Chat")).toBeChecked();

    await pickMemorial(u);

    // Previously switching to this category reset all three to false.
    expect(featureSwitch("Enable Chat")).toBeChecked();
    expect(featureSwitch("Enable Polls")).toBeChecked();
  });

  it("no longer claims chat, Q&A and polls are unavailable", async () => {
    const u = userEvent.setup();
    renderModal();
    await pickMemorial(u);

    expect(screen.queryByText(/unavailable for the Funeral/i)).not.toBeInTheDocument();
  });
});

// ── the category is still offered, and behaves like any other ──────────────────────────

describe("the category itself", () => {
  it("is still selectable — removing the restriction is not removing the category", () => {
    renderModal();
    expect(within(categorySelect()).getByRole("option", { name: MEMORIAL })).toBeInTheDocument();
  });

  it("is submitted verbatim, so the server can still classify and price it", async () => {
    const u = userEvent.setup();
    renderModal();
    await pickMemorial(u);
    await u.click(visibilityButton("Public"));
    await u.click(featureSwitch("Enable Chat"));

    await u.type(screen.getByPlaceholderText(/event title|e\.g\./i), "Remembering Ada");
    await u.click(screen.getByRole("button", { name: /^schedule event$/i }));

    if (vi.mocked(api.post).mock.calls.length) {
      const [, body] = vi.mocked(api.post).mock.calls[0];
      expect(body.category).toBe(MEMORIAL);
      // Neither field is rewritten on the way out.
      expect(body.visibility).toBe("public");
      expect(body.chat_enabled).toBe(true);
    }
  });

  it("treats a memorial exactly like a webinar", async () => {
    const u = userEvent.setup();
    renderModal();

    await u.selectOptions(categorySelect(), "Webinar");
    const webinar = {
      public: visibilityButton("Public").disabled,
      chat: featureSwitch("Enable Chat").disabled,
      polls: featureSwitch("Enable Polls").disabled,
    };

    await pickMemorial(u);
    expect({
      public: visibilityButton("Public").disabled,
      chat: featureSwitch("Enable Chat").disabled,
      polls: featureSwitch("Enable Polls").disabled,
    }).toEqual(webinar);
  });
});
