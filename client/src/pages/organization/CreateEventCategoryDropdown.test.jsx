// The Create Event dialog's Category field opens as part of the page, themed from its first
// frame (production report: a large plain white rectangle covered the dark dialog for about a
// second before the options appeared).
//
// The field was a native <select>. Its option list is drawn by the browser in a popup outside
// the page: no class, `dark:` variant or color-scheme of ours reaches its first frame, and it
// is not bounded by the dialog. It is now ui/Dropdown (variant "field"): a listbox rendered as
// page DOM, so what these tests read — its classes, its place in the DOM, its geometry — is
// what paints.
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

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

const CATEGORIES = [
  "Webinar", "Conference", "Corporate Event", "Product Launch", "Workshop / Training",
  "Entertainment", "Sports", "Wedding / Celebration", "Funeral / Memorial",
  "Community / Charity", "Other",
];

const realRect = Element.prototype.getBoundingClientRect;
const realHeight = window.innerHeight;

let onClose;
const renderModal = () => {
  onClose = vi.fn();
  render(
    <ThemeProvider>
      <CreateEventModal open onClose={onClose} onCreated={vi.fn()} />
    </ThemeProvider>
  );
};

const dialog = () => screen.getByRole("dialog", { name: "Create Event" });
const field = () => screen.getByRole("combobox", { name: "Category" });
const list = () => screen.getByRole("listbox", { name: "Category" });
const queryList = () => screen.queryByRole("listbox", { name: "Category" });
const option = (name) => within(list()).getByRole("option", { name });

beforeEach(() => {
  vi.clearAllMocks();
  // The console is dark: ThemeContext writes .dark on <html>, which the dialog follows.
  localStorage.setItem("theme", "dark");
  vi.mocked(api.get).mockResolvedValue({ data: { items: [] } });
  vi.mocked(api.post).mockResolvedValue({ data: { id: "e1" } });
  Element.prototype.scrollIntoView = vi.fn();
});

afterEach(() => {
  Element.prototype.getBoundingClientRect = realRect;
  window.innerHeight = realHeight;
  localStorage.removeItem("theme");
  document.documentElement.classList.remove("dark");
});

describe("opening", () => {
  it("is a listbox in the page, not a browser-drawn <select>", () => {
    renderModal();
    const selects = [...dialog().querySelectorAll("select")];
    expect(selects.filter((s) => s.textContent.includes("Webinar"))).toEqual([]);
    expect(field().tagName).toBe("BUTTON");
    expect(field()).toHaveTextContent("Webinar");
    expect(field()).toHaveAttribute("aria-haspopup", "listbox");
    expect(field()).toHaveAttribute("aria-expanded", "false");
  });

  it("shows every option on the click that opens it: nothing to wait for, no empty panel", () => {
    renderModal();
    const requestsBefore = vi.mocked(api.get).mock.calls.length;
    fireEvent.click(field());
    // Synchronously, with no waitFor: the options are local, so they render in the same
    // commit as the panel. There is no frame in which the panel exists without them.
    const names = within(list()).getAllByRole("option").map((o) => o.textContent);
    expect(names).toEqual(CATEGORIES);
    expect(vi.mocked(api.get).mock.calls.length).toBe(requestsBefore);
    expect(within(list()).queryByText(/loading/i)).toBeNull();
    expect(field()).toHaveAttribute("aria-expanded", "true");
    expect(field()).toHaveAttribute("aria-controls", list().id);
  });

  it("is painted on the dialog's own dark surface from its first frame", () => {
    renderModal();
    expect(document.documentElement).toHaveClass("dark");
    fireEvent.click(field());
    const panel = list();
    // A dark surface, border and text for the dark console, and the light ones for light mode
    // — never a bare white panel. zk-pop-in fades it in from transparent, never from white.
    for (const cls of ["dark:bg-slate-800", "dark:border-slate-700", "bg-white", "border-slate-200", "zk-pop-in"]) {
      expect(panel.className).toContain(cls);
    }
    expect(panel.style.backgroundColor).toBe("");
    // The options carry the theme too: the selected row (violet tint + check), the others.
    expect(option("Webinar").className).toContain("dark:bg-violet-500/15");
    expect(option("Webinar").className).toContain("dark:text-violet-100");
    expect(option("Conference").className).toContain("dark:text-slate-200");
  });

  it("wears exactly the field look of the inputs beside it", () => {
    renderModal();
    const title = screen.getByPlaceholderText("e.g. Q3 Product Launch");
    for (const cls of title.className.split(/\s+/)) expect(field().className).toContain(cls);
  });
});

describe("layering", () => {
  it("floats above the dialog instead of inside its scrolling body, so it is never clipped", () => {
    renderModal();
    fireEvent.click(field());
    const panel = list();
    expect(dialog().contains(panel)).toBe(false);
    expect(panel.parentElement).toBe(document.body);
    expect(panel.style.position).toBe("fixed");
    // The dialog's overlay is z-[60]; the menu is the layer above it, below toasts (z-[80]).
    expect(dialog().closest(".z-\\[60\\]")).not.toBeNull();
    expect(panel.className).toContain("z-[70]");
  });

  it("sits under the field at its width, and stays inside the dialog, not just the window", () => {
    window.innerHeight = 1000;
    Element.prototype.getBoundingClientRect = function rect() {
      if (this.getAttribute?.("role") === "dialog") return { top: 50, bottom: 400, left: 20, right: 660, width: 640, height: 350 };
      if (this.getAttribute?.("role") === "combobox") return { top: 162, bottom: 200, left: 40, right: 440, width: 400, height: 38 };
      return { top: 0, bottom: 0, left: 0, right: 0, width: 0, height: 0 };
    };
    renderModal();
    fireEvent.click(field());
    const s = list().style;
    expect(s.left).toBe("40px");
    expect(s.width).toBe("400px");
    expect(s.top).toBe("206px"); // 6px under the field
    // The window has room for the full 288px; the dialog ends at 400, so the menu stops 8px
    // short of the dialog's edge: 206 + 186 = 392.
    expect(s.maxHeight).toBe("186px");
  });
});

describe("choosing", () => {
  it("keeps the chosen category, closes, and leaves the dialog open with focus on the field", async () => {
    const u = userEvent.setup();
    renderModal();
    await u.click(field());
    await u.click(option("Workshop / Training"));

    expect(queryList()).toBeNull();
    expect(field()).toHaveTextContent("Workshop / Training");
    expect(field()).toHaveFocus();
    expect(dialog()).toBeInTheDocument();
    expect(onClose).not.toHaveBeenCalled();

    await u.click(field());
    expect(option("Workshop / Training")).toHaveAttribute("aria-selected", "true");
    expect(option("Webinar")).toHaveAttribute("aria-selected", "false");
  });

  it("sends the chosen category with the event", async () => {
    const u = userEvent.setup();
    renderModal();
    await u.click(field());
    await u.click(option("Conference"));
    await u.type(screen.getByPlaceholderText("e.g. Q3 Product Launch"), "Partner summit");
    await u.click(screen.getByRole("button", { name: "Save Draft" }));

    expect(api.post).toHaveBeenCalledTimes(1);
    expect(vi.mocked(api.post).mock.calls[0][1]).toMatchObject({ title: "Partner summit", category: "Conference" });
  });

  it("closes on a click elsewhere in the dialog without changing the value", async () => {
    const u = userEvent.setup();
    renderModal();
    await u.click(field());
    await u.click(screen.getByPlaceholderText("What is this event about?"));
    expect(queryList()).toBeNull();
    expect(field()).toHaveTextContent("Webinar");
    expect(dialog()).toBeInTheDocument();
  });
});

describe("keyboard", () => {
  it("opens, moves and commits like a native select, type-ahead included", async () => {
    const u = userEvent.setup();
    renderModal();
    field().focus();

    await u.keyboard("{ArrowDown}");
    const active = () => document.getElementById(list().getAttribute("aria-activedescendant"));
    expect(list()).toHaveFocus();
    expect(active()).toHaveTextContent("Webinar"); // opens on the current value

    await u.keyboard("{ArrowDown}");
    expect(active()).toHaveTextContent("Conference");
    await u.keyboard("{End}");
    expect(active()).toHaveTextContent("Other");
    await u.keyboard("{Home}");
    expect(active()).toHaveTextContent("Webinar");
    await u.keyboard("f");
    expect(active()).toHaveTextContent("Funeral / Memorial");

    await u.keyboard("{Enter}");
    expect(queryList()).toBeNull();
    expect(field()).toHaveTextContent("Funeral / Memorial");
    expect(field()).toHaveFocus();
  });

  it("type-ahead from the closed field opens on the matching option", async () => {
    const u = userEvent.setup();
    renderModal();
    field().focus();
    await u.keyboard("s");
    expect(document.getElementById(list().getAttribute("aria-activedescendant"))).toHaveTextContent("Sports");
  });

  it("Escape closes the menu only; the dialog and its form stay", async () => {
    const u = userEvent.setup();
    renderModal();
    await u.type(screen.getByPlaceholderText("e.g. Q3 Product Launch"), "Keep me");
    await u.click(field());

    await u.keyboard("{Escape}");
    expect(queryList()).toBeNull();
    expect(onClose).not.toHaveBeenCalled();
    expect(dialog()).toBeInTheDocument();
    expect(screen.getByPlaceholderText("e.g. Q3 Product Launch")).toHaveValue("Keep me");
    expect(field()).toHaveFocus();
    expect(field()).toHaveTextContent("Webinar");

    // With the menu gone, Escape is the dialog's again.
    await u.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("Tab closes the menu and hands focus back to the field, so Tab carries on through the form", async () => {
    const u = userEvent.setup();
    renderModal();
    await u.click(field());
    act(() => {
      fireEvent.keyDown(list(), { key: "Tab" });
    });
    expect(queryList()).toBeNull();
    expect(field()).toHaveFocus();
  });
});
