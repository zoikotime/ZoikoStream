// The Events table's row actions (ORG-02): Open Workspace, and the row's "⋯" overflow holding
// Copy viewer link and Delete — both controls visible at rest.
//
// They sit inside the shared DataTable's row-actions cell, which is `opacity-0` until the row
// is hovered (or something in it is focused) unless a table opts out — so on
// /organization/events the Actions column once looked empty. The Events table opts into
// always-visible actions (DataTable `alwaysShowRowActions`); hover and focus only add emphasis. Tailwind is not loaded in jsdom, so visibility is asserted on
// the classes that would hide the controls, in both themes.
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../api", () => ({
  default: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
  errMsg: (e) => e?.response?.data?.detail ?? e?.message ?? "Something went wrong.",
  API_BASE: "http://api.test",
  AUTH_EXPIRED_EVENT: "zoiko:auth-expired",
}));
vi.mock("../../ui/Toast", () => ({ notify: { error: vi.fn(), success: vi.fn(), alert: vi.fn() } }));
vi.mock("../../utils/viewerLink", () => ({ copyViewerLink: vi.fn(() => Promise.resolve(true)) }));
vi.mock("../../auth/AuthContext", () => ({
  useAuth: () => ({ user: { full_name: "Vihari Owner", email: "owner@example.com" }, logout: vi.fn() }),
}));

import api from "../../api";
import { notify } from "../../ui/Toast";
import DataTable from "../../components/admin/DataTable";
import { ThemeProvider } from "../../theme/ThemeContext";
import OrganizationEvents from "./Events";

const EVENTS = [
  { id: "e1", title: "Testt", slug: "testt", status: "scheduled", start_time: "2026-10-06T10:26:00Z",
    visibility: "public", registration_required: false, duration_minutes: 12 },
  { id: "e2", title: "new", slug: "new", status: "draft", start_time: "2026-10-01T09:33:00Z",
    visibility: "public", registration_required: false, duration_minutes: 12 },
];

const listCalls = () => vi.mocked(api.get).mock.calls.filter(([url]) => url === "/events");

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  vi.mocked(api.get).mockImplementation((url) => {
    if (url === "/events") return Promise.resolve({ data: { items: EVENTS, total: EVENTS.length } });
    if (url === "/events/status-counts") return Promise.resolve({ data: { counts: { scheduled: 1, draft: 1 } } });
    return Promise.resolve({ data: {} });
  });
});

afterEach(() => {
  vi.restoreAllMocks();
  document.documentElement.classList.remove("dark");
  localStorage.clear();
});

const renderPage = (theme = "light") => {
  localStorage.setItem("theme", theme);
  return render(
    <ThemeProvider>
      <MemoryRouter initialEntries={["/organization/events"]}>
        <OrganizationEvents />
      </MemoryRouter>
    </ThemeProvider>,
  );
};

// Anything between the control and its row that would hide it at rest, or reveal it only on
// hover: opacity-0 / invisible / transparent text, and any group-hover: utility.
const HIDING = /(^|:)(opacity-0|invisible|hidden|text-transparent)$|^group-hover:/;
function hidingClasses(el) {
  const found = [];
  for (let node = el; node && node.tagName !== "TR"; node = node.parentElement) {
    for (const cls of node.classList) if (HIDING.test(cls)) found.push(cls);
  }
  return found;
}

// The events table. Titles also appear in the side panels (Next on the schedule), so rows are
// always looked up inside the table.
const table = () => within(screen.getByRole("table"));
const view = (title) => table().getByRole("link", { name: `Open workspace for ${title}` });
const more = (title) => table().getByRole("button", { name: `More actions for ${title}` });
// Delete lives in the row's "⋯" menu (ZST-UX-NAV-001 §16.1), which is portalled to <body>.
async function chooseDelete(user, title) {
  await user.click(more(title));
  await user.click(await screen.findByRole("menuitem", { name: "Delete event" }));
}

describe("Open and More actions at rest", () => {
  it.each(["light", "dark"])("are visible without hovering anything (%s mode)", async (theme) => {
    renderPage(theme);
    await table().findByText("Testt");
    expect(document.documentElement.classList.contains("dark")).toBe(theme === "dark");
    for (const title of ["Testt", "new"]) {
      for (const control of [view(title), more(title)]) {
        expect(control).toBeVisible();
        expect(hidingClasses(control)).toEqual([]);
      }
    }
  });

  it.each(["light", "dark"])("draw their icons in a readable colour, never transparent (%s mode)", async (theme) => {
    renderPage(theme);
    await table().findByText("Testt");
    for (const control of [view("Testt"), more("Testt")]) {
      const classes = [...control.classList];
      // A real text colour for light mode and one for dark mode (the icon is currentColor).
      expect(classes.some((c) => /^text-(slate|neutral|rose|violet)-\d{3}$/.test(c))).toBe(true);
      expect(classes.some((c) => /^dark:text-(slate|neutral|rose|violet|white)/.test(c))).toBe(true);
      expect(classes).not.toContain("text-transparent");
    }
  });

  it("sit under the Actions heading in every row: open, then the row's overflow menu", async () => {
    renderPage();
    await table().findByText("Testt");
    const headers = screen.getAllByRole("columnheader").map((th) => th.textContent.trim());
    const actionsAt = headers.indexOf("Actions");
    const row = view("Testt").closest("tr");
    const cells = within(row).getAllByRole("cell");
    const controls = [...cells[actionsAt].querySelectorAll("a, button")].map((el) => el.getAttribute("aria-label"));
    expect(controls).toEqual(["Open workspace for Testt", "More actions for Testt"]);
    expect(more("Testt")).toHaveAttribute("aria-haspopup", "menu");
  });
});

describe("hover and focus", () => {
  it("hover does not decide visibility: the same classes before, during and after", async () => {
    const user = userEvent.setup();
    renderPage("dark");
    await table().findByText("Testt");
    const row = view("Testt").closest("tr");
    const before = hidingClasses(view("Testt"));
    await user.hover(row);
    expect(hidingClasses(view("Testt"))).toEqual(before);
    await user.unhover(row);
    expect(hidingClasses(view("Testt"))).toEqual([]);
    // Hover only adds emphasis on the controls themselves.
    expect([...view("Testt").classList].some((c) => c.startsWith("hover:text-"))).toBe(true);
    expect([...more("Testt").classList].some((c) => c.startsWith("hover:bg-"))).toBe(true);
  });

  it("keyboard focus reaches Open then More actions; the menu opens on Enter and Escape returns", async () => {
    const user = userEvent.setup();
    renderPage();
    await table().findByText("Testt");
    for (let i = 0; i < 40 && document.activeElement !== view("Testt"); i += 1) await user.tab();
    expect(view("Testt")).toHaveFocus();
    expect(view("Testt")).toHaveClass("focus-visible:ring-2");
    expect(hidingClasses(view("Testt"))).toEqual([]);
    await user.tab();
    expect(more("Testt")).toHaveFocus();
    expect(more("Testt")).toHaveClass("focus-visible:ring-2");
    await user.keyboard("{Enter}");
    expect(await screen.findByRole("menuitem", { name: "Copy viewer link" })).toHaveFocus();
    await user.keyboard("{ArrowDown}");
    expect(screen.getByRole("menuitem", { name: "Delete event" })).toHaveFocus();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    expect(more("Testt")).toHaveFocus();
  });

  // The menu is portalled to <body>. Tab must close it with focus back on its trigger, so the
  // browser's Tab carries on from the row — not from the end of the page, where it reached a
  // floating button in Chrome. (jsdom has no native Tab, so this pins the hand-back; the
  // onward step was checked in a real browser.)
  it("Tab closes the menu and hands focus back to its trigger first", async () => {
    renderPage();
    await table().findByText("Testt");
    // Synchronous from here to the focus check: nothing else can move focus in between.
    more("Testt").focus();
    fireEvent.keyDown(more("Testt"), { key: "Enter" });
    const first = screen.getByRole("menuitem", { name: "Copy viewer link" });
    expect(first).toHaveFocus();
    fireEvent.keyDown(first, { key: "Tab" });
    expect(more("Testt")).toHaveFocus();
    await waitFor(() => expect(screen.queryByRole("menu")).not.toBeInTheDocument());
    expect(more("Testt")).toHaveAttribute("aria-expanded", "false");
  });
});

describe("Delete keeps its rules", () => {
  // /organization/events is an org_admin route (App.jsx RoleRoute), and the server's
  // DELETE /events/{id} requires an organization admin (require_org_admin): everyone who can
  // open this page may delete, so the action shows for every row — and the server still decides.

  it("asks for confirmation, and does nothing when declined", async () => {
    const user = userEvent.setup();
    vi.spyOn(window, "confirm").mockReturnValue(false);
    renderPage();
    await table().findByText("Testt");
    await chooseDelete(user, "Testt");
    expect(window.confirm).toHaveBeenCalledWith('Delete "Testt"? This cannot be undone.');
    expect(api.delete).not.toHaveBeenCalled();
  });

  it("deletes once confirmed and refreshes the list", async () => {
    const user = userEvent.setup();
    vi.spyOn(window, "confirm").mockReturnValue(true);
    vi.mocked(api.delete).mockResolvedValue({});
    renderPage();
    await table().findByText("Testt");
    const before = listCalls().length;
    await chooseDelete(user, "Testt");
    await waitFor(() => expect(api.delete).toHaveBeenCalledWith("/events/e1"));
    expect(notify.success).toHaveBeenCalledWith("Event deleted");
    await waitFor(() => expect(listCalls().length).toBeGreaterThan(before));
  });

  it("shows the server's refusal and keeps the row when deletion is not permitted", async () => {
    const user = userEvent.setup();
    vi.spyOn(window, "confirm").mockReturnValue(true);
    vi.mocked(api.delete).mockRejectedValue({
      response: { status: 403, data: { detail: "Organization admin access required" } },
    });
    renderPage();
    await table().findByText("Testt");
    await chooseDelete(user, "Testt");
    await waitFor(() => expect(notify.error).toHaveBeenCalledWith("Organization admin access required"));
    expect(notify.success).not.toHaveBeenCalled();
    expect(table().getByText("Testt")).toBeInTheDocument();
  });
});

describe("the shared DataTable", () => {
  const columns = [{ key: "name", header: "Name" }];
  const rows = [{ id: 1, name: "One" }];
  const actions = () => <button type="button">Edit</button>;

  it("still reveals row actions on hover by default — other tables are unchanged", () => {
    render(<DataTable columns={columns} rows={rows} rowKey={(r) => r.id} rowActions={actions} />);
    const wrapper = screen.getByRole("button", { name: "Edit" }).closest("[data-row-actions]");
    expect(wrapper).toHaveClass("opacity-0", "group-hover:opacity-100", "focus-within:opacity-100");
  });

  it("shows them at rest when a table asks for it", () => {
    render(<DataTable columns={columns} rows={rows} rowKey={(r) => r.id} rowActions={actions} alwaysShowRowActions />);
    expect(hidingClasses(screen.getByRole("button", { name: "Edit" }))).toEqual([]);
  });
});
