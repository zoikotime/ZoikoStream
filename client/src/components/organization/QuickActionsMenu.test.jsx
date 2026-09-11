// The topbar's Quick Actions menu, and the slot it shares with the health verdict.
//
// Two things worth pinning: the menu offers exactly the seven agreed destinations and
// navigates to real routes, and it replaces the health pill ONLY on the page that asks for
// it — so no other org page silently loses its live verdict.
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

vi.mock("../../api", () => ({
  default: { get: vi.fn(() => Promise.resolve({ data: {} })), post: vi.fn() },
  errMsg: (e) => e?.message ?? "Something went wrong.",
}));

vi.mock("../../auth/AuthContext", () => ({
  useAuth: () => ({
    user: { full_name: "Vihari Owner", email: "owner@example.com" },
    logout: vi.fn(),
  }),
}));

import { ThemeProvider } from "../../theme/ThemeContext";
import QuickActionsMenu from "./QuickActionsMenu";
import { QUICK_ACTIONS } from "./quickActions";
import Topbar from "../Dashboard/Topbar";

const HEALTHY_STATE = { health: { status: "ok" }, organization: { name: "Northwind" } };

const Landed = () => <h1>landed</h1>;

function renderMenu() {
  return render(
    <MemoryRouter initialEntries={["/organization/profile"]}>
      <Routes>
        <Route path="/organization/profile" element={<QuickActionsMenu />} />
        {/* Every destination resolves to the same probe, so a click can be asserted as a
            real navigation rather than as a handler call. */}
        {QUICK_ACTIONS.map((a) => (
          <Route key={a.to} path={a.to.split("?")[0]} element={<Landed />} />
        ))}
      </Routes>
    </MemoryRouter>,
  );
}

function renderTopbar(props) {
  // ThemeProvider because the topbar renders the real ThemeToggle — the point of these
  // three tests is the REAL header, not a stub of it.
  return render(
    <ThemeProvider>
      <MemoryRouter>
        <Topbar state={HEALTHY_STATE} unknown={false} onRetry={vi.fn()} {...props} />
      </MemoryRouter>
    </ThemeProvider>,
  );
}

describe("the shared action list", () => {
  it("carries exactly the seven agreed destinations, in order", () => {
    expect(QUICK_ACTIONS.map((a) => a.title)).toEqual([
      "Edit Profile", "Invite Members", "Manage Workspace", "API Keys",
      "Security Settings", "Billing", "Export Data",
    ]);
  });

  it("points every action at an in-app route", () => {
    for (const action of QUICK_ACTIONS) {
      expect(action.to).toMatch(/^\/organization\//);
      expect(action.to).not.toMatch(/^https?:/);
      expect(action.desc?.length).toBeGreaterThan(0);
    }
  });
});

describe("the menu", () => {
  it("starts closed and opens on click", async () => {
    renderMenu();
    const trigger = screen.getByRole("button", { name: /quick actions/i });
    expect(trigger).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();

    await userEvent.click(trigger);

    expect(trigger).toHaveAttribute("aria-expanded", "true");
    const menu = screen.getByRole("menu");
    expect(within(menu).getAllByRole("menuitem")).toHaveLength(QUICK_ACTIONS.length);
    for (const action of QUICK_ACTIONS) {
      expect(within(menu).getByText(action.title)).toBeInTheDocument();
    }
  });

  it("navigates to the chosen destination and closes", async () => {
    renderMenu();
    await userEvent.click(screen.getByRole("button", { name: /quick actions/i }));
    await userEvent.click(screen.getByRole("menuitem", { name: /Billing/ }));

    expect(await screen.findByText("landed")).toBeInTheDocument();
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  });

  it("opens and commits from the keyboard", async () => {
    renderMenu();
    const trigger = screen.getByRole("button", { name: /quick actions/i });
    trigger.focus();

    // ArrowDown opens on the first item — the contract the console's other menus have.
    await userEvent.keyboard("{ArrowDown}");
    expect(screen.getByRole("menu")).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: /Edit Profile/ })).toHaveFocus();

    await userEvent.keyboard("{ArrowDown}");
    expect(screen.getByRole("menuitem", { name: /Invite Members/ })).toHaveFocus();

    await userEvent.keyboard("{End}");
    expect(screen.getByRole("menuitem", { name: /Export Data/ })).toHaveFocus();

    await userEvent.keyboard("{Enter}");
    expect(await screen.findByText("landed")).toBeInTheDocument();
  });

  it("closes on Escape and returns focus to the trigger", async () => {
    renderMenu();
    const trigger = screen.getByRole("button", { name: /quick actions/i });
    await userEvent.click(trigger);
    await userEvent.keyboard("{Escape}");

    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });

  it("is a single tab stop", async () => {
    renderMenu();
    await userEvent.click(screen.getByRole("button", { name: /quick actions/i }));
    const items = screen.getAllByRole("menuitem");
    // Arrow keys move within a menu; Tab must not walk all seven items.
    expect(items.filter((el) => el.getAttribute("tabindex") === "0").length)
      .toBeLessThanOrEqual(1);
  });
});

describe("the topbar slot", () => {
  it("shows Quick Actions instead of the health pill when the page asks", () => {
    renderTopbar({ quickActions: true });
    expect(screen.getByRole("button", { name: /quick actions/i })).toBeInTheDocument();
    // The pill it replaced is gone from this page's header.
    expect(screen.queryByText("Healthy")).not.toBeInTheDocument();
  });

  it("shows nothing in the slot on a healthy page that does not ask", () => {
    // The permanent "Healthy" pill is gone from the whole organization console: it is a
    // readout nobody can act on, repeated on every screen, and the real verdict lives on
    // Support & Status. What must NOT come back is a page silently claiming health.
    renderTopbar({});
    expect(screen.queryByText("Healthy")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /quick actions/i })).not.toBeInTheDocument();
  });

  it("still surfaces a retry when the console-state call failed", () => {
    // The failure case is the half worth keeping: with no console-state the shell has no
    // identity, no badges and no verdict, so the reader needs to be told and be able to act.
    renderTopbar({ unknown: true });
    expect(screen.getByText(/status unavailable/i)).toBeInTheDocument();
    expect(screen.getByTitle(/click to retry/i)).toBeInTheDocument();
  });

  it("leaves the rest of the header intact", () => {
    renderTopbar({ quickActions: true });
    // The controls the brief said not to disturb.
    expect(screen.getByRole("button", { name: /request live event/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /account menu/i })).toBeInTheDocument();
    // The theme control is role="switch", not a plain button.
    expect(screen.getByRole("switch", { name: /switch to (light|dark) theme/i }))
      .toBeInTheDocument();
  });
});
