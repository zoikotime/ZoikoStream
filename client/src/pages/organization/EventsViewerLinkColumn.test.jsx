// The Events table (ORG-02): Event · Date & time · Status · Readiness · Actions.
//
// Copy viewer link sits in the row's "⋯" menu under "Actions", beside Open Workspace, and says
// what it does in words (it once sat there as an unlabelled chain icon). The URL itself still comes from the unchanged utils/viewerLink (mocked here so
// the test can see what it was handed).
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../api", () => ({
  default: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
  errMsg: (e) => e?.message ?? "Something went wrong.",
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
import { copyViewerLink } from "../../utils/viewerLink";
import DataTable from "../../components/admin/DataTable";
import { ThemeProvider } from "../../theme/ThemeContext";
import OrganizationEvents from "./Events";

const EVENTS = [
  { id: "e1", title: "Testt", slug: "testt", status: "scheduled", start_time: "2026-10-06T10:26:00Z",
    visibility: "public", registration_required: false, duration_minutes: 12 },
  { id: "e2", title: "Family memorial", slug: "memorial", status: "scheduled", start_time: "2026-10-01T09:33:00Z",
    visibility: "private", registration_required: true, duration_minutes: 45,
    public_watch_url: "https://watch.example.org/events/e2/watch" },
];

const HEADERS = ["Event", "Date & time", "Status", "Readiness", "Actions"];

const listCalls = () => vi.mocked(api.get).mock.calls.filter(([url]) => url === "/events");

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.get).mockImplementation((url) => {
    if (url === "/events") return Promise.resolve({ data: { items: EVENTS, total: EVENTS.length } });
    if (url === "/events/status-counts") return Promise.resolve({ data: { counts: { scheduled: 2 } } });
    return Promise.resolve({ data: {} });
  });
});

const renderPage = () =>
  render(
    <ThemeProvider>
      <MemoryRouter initialEntries={["/organization/events"]}>
        <OrganizationEvents />
      </MemoryRouter>
    </ThemeProvider>,
  );

const headerTexts = () => screen.getAllByRole("columnheader").map((th) => th.textContent.trim());
// The events table. Titles also appear in the side panels (Next on the schedule), so rows are
// always looked up inside the table.
const table = () => within(screen.getByRole("table"));
const rowFor = (title) => table().getByRole("button", { name: new RegExp(`^${title}`) }).closest("tr");
const cellsOf = (row) => within(row).getAllByRole("cell");
// The menu is portalled to <body>, so its items are found on the screen once it is open.
async function chooseCopy(user, title) {
  await user.click(table().getByRole("button", { name: `More actions for ${title}` }));
  await user.click(await screen.findByRole("menuitem", { name: "Copy viewer link" }));
}

describe("the Events table columns", () => {
  it("reads Event, Date & time, Status, Readiness, Actions", async () => {
    renderPage();
    await table().findByText("Testt");
    expect(headerTexts()).toEqual(HEADERS);
  });

  it("keeps every row aligned with the header: one cell per column", async () => {
    renderPage();
    await table().findByText("Testt");
    for (const title of ["Testt", "Family memorial"]) {
      expect(cellsOf(rowFor(title))).toHaveLength(HEADERS.length);
    }
    // The actions line up with their heading: same alignment above and below.
    const headers = screen.getAllByRole("columnheader");
    const cells = cellsOf(rowFor("Testt"));
    const i = HEADERS.indexOf("Actions");
    expect(headers[i]).toHaveClass("text-right");
    expect(cells[i]).toHaveClass("text-right");
  });

  it("puts Open Workspace and the row's overflow menu under Actions, and the time under Date & time", async () => {
    const user = userEvent.setup();
    renderPage();
    await table().findByText("Testt");
    const cells = cellsOf(rowFor("Testt"));
    const actionsCell = cells[HEADERS.indexOf("Actions")];

    expect(within(actionsCell).getByRole("link", { name: "Open workspace for Testt" })).toHaveAttribute("href", "/organization/events/e1");
    expect(within(actionsCell).getAllByRole("button")).toHaveLength(1);
    await user.click(within(actionsCell).getByRole("button", { name: "More actions for Testt" }));
    const menu = await screen.findByRole("menu", { name: "More actions for Testt" });
    expect(within(menu).getAllByRole("menuitem").map((m) => m.textContent)).toEqual(["Copy viewer link", "Delete event"]);

    expect(cells[HEADERS.indexOf("Date & time")]).toHaveTextContent(/2026/);
  });

  it("says what the controls do in a tooltip and an accessible name", async () => {
    const user = userEvent.setup();
    renderPage();
    await table().findByText("Testt");
    const trigger = table().getByRole("button", { name: "More actions for Testt" });
    expect(trigger).toHaveAttribute("title", "More actions");
    expect(trigger).toHaveAttribute("type", "button");
    await user.click(trigger);
    expect(await screen.findByRole("menuitem", { name: "Copy viewer link" })).toHaveAttribute("type", "button");
  });

  it("is a viewer link for private events too, not only public ones", async () => {
    renderPage();
    await table().findByText("Family memorial");
    const user = userEvent.setup();
    const cells = cellsOf(rowFor("Family memorial"));
    expect(cells[HEADERS.indexOf("Event")]).toHaveTextContent(/private event/i);
    await user.click(within(cells[HEADERS.indexOf("Actions")]).getByRole("button", { name: "More actions for Family memorial" }));
    expect(await screen.findByRole("menuitem", { name: "Copy viewer link" })).toBeInTheDocument();
  });

  it("copies through the unchanged viewer-link logic, with the event it belongs to", async () => {
    const user = userEvent.setup();
    renderPage();
    await table().findByText("Family memorial");
    await chooseCopy(user, "Family memorial");
    await waitFor(() => expect(copyViewerLink).toHaveBeenCalledTimes(1));
    expect(copyViewerLink).toHaveBeenCalledWith(EVENTS[1]);
    expect(notify.success).toHaveBeenCalledWith("Viewer link copied");
  });

  it("reports a copy that failed", async () => {
    vi.mocked(copyViewerLink).mockResolvedValueOnce(false);
    const user = userEvent.setup();
    renderPage();
    await table().findByText("Testt");
    await chooseCopy(user, "Testt");
    await waitFor(() => expect(notify.error).toHaveBeenCalledWith("Unable to copy viewer link."));
  });

  it("keeps sorting: Event, Date & time and Status sort on the server; Readiness and Actions do not", async () => {
    const user = userEvent.setup();
    renderPage();
    await table().findByText("Testt");
    for (const name of ["Event", "Date & time", "Status"]) {
      expect(within(screen.getByRole("columnheader", { name: new RegExp(`^${name}`) }))
        .getByRole("button")).toBeInTheDocument();
    }
    for (const name of ["Readiness", "Actions"]) {
      expect(within(screen.getByRole("columnheader", { name })).queryByRole("button")).not.toBeInTheDocument();
    }
    await user.click(within(screen.getByRole("columnheader", { name: /^Event/ })).getByRole("button"));
    await waitFor(() => expect(listCalls().at(-1)[1].params).toMatchObject({ sort_by: "title" }));
  });
});

describe("DataTable's row-actions heading", () => {
  const columns = [{ key: "name", header: "Name" }];
  const rows = [{ id: 1, name: "One" }];
  const actions = () => <button type="button">Edit</button>;

  it("stays unlabelled when a table does not ask for one (every other table is unchanged)", () => {
    render(<DataTable columns={columns} rows={rows} rowKey={(r) => r.id} rowActions={actions} />);
    const headers = screen.getAllByRole("columnheader");
    expect(headers).toHaveLength(2);
    expect(headers[1]).toHaveTextContent("");
  });

  it("is labelled when the table names it", () => {
    render(<DataTable columns={columns} rows={rows} rowKey={(r) => r.id} rowActions={actions} rowActionsHeader="Actions" />);
    expect(screen.getAllByRole("columnheader").map((th) => th.textContent)).toEqual(["Name", "Actions"]);
  });
});
