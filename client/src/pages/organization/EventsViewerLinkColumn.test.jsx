// The Events table's viewer link has a column of its own.
//
// The chain icon copies the event's viewer URL, but it sat under the generic "Actions" heading
// beside View and Delete, so nothing said what it did. It is now its own "Viewer link" column
// right after Duration, with View/Delete under "Actions" — and the URL itself still comes from
// the unchanged utils/viewerLink (mocked here so the test can see what it was handed).
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

const HEADERS = ["Event", "Status", "Starts", "Visibility", "Registration", "Duration", "Viewer link", "Actions"];

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
const rowFor = (title) => screen.getByRole("button", { name: new RegExp(`^${title}`) }).closest("tr");
const cellsOf = (row) => within(row).getAllByRole("cell");

describe("the Events table columns", () => {
  it("reads Event, Status, Starts, Visibility, Registration, Duration, Viewer link, Actions", async () => {
    renderPage();
    await screen.findByText("Testt");
    expect(headerTexts()).toEqual(HEADERS);
  });

  it("keeps every row aligned with the header: one cell per column", async () => {
    renderPage();
    await screen.findByText("Testt");
    for (const title of ["Testt", "Family memorial"]) {
      expect(cellsOf(rowFor(title))).toHaveLength(HEADERS.length);
    }
    // The icon columns line up with their headings: same alignment above and below.
    const headers = screen.getAllByRole("columnheader");
    const cells = cellsOf(rowFor("Testt"));
    for (const name of ["Duration", "Viewer link", "Actions"]) {
      const i = HEADERS.indexOf(name);
      expect(headers[i]).toHaveClass("text-right");
      expect(cells[i]).toHaveClass("text-right");
    }
  });

  it("puts only the link icon under Viewer link, and View/Delete under Actions", async () => {
    renderPage();
    await screen.findByText("Testt");
    const cells = cellsOf(rowFor("Testt"));
    const linkCell = cells[HEADERS.indexOf("Viewer link")];
    const actionsCell = cells[HEADERS.indexOf("Actions")];

    expect(within(linkCell).getAllByRole("button")).toHaveLength(1);
    expect(within(linkCell).getByRole("button", { name: "Copy viewer link for Testt" })).toBeInTheDocument();

    expect(within(actionsCell).getByRole("button", { name: "View Testt" })).toBeInTheDocument();
    expect(within(actionsCell).getByRole("button", { name: "Delete Testt" })).toBeInTheDocument();
    expect(within(actionsCell).queryByRole("button", { name: /viewer link/i })).not.toBeInTheDocument();

    // Duration is unaffected and still sits right before the link.
    expect(cells[HEADERS.indexOf("Duration")]).toHaveTextContent("12m");
  });

  it("says what the icon does in a tooltip and an accessible name", async () => {
    renderPage();
    await screen.findByText("Testt");
    const button = screen.getByRole("button", { name: "Copy viewer link for Testt" });
    expect(button).toHaveAttribute("title", "Copy viewer link");
    expect(button).toHaveAttribute("type", "button");
  });

  it("is a viewer link for private events too, not only public ones", async () => {
    renderPage();
    await screen.findByText("Family memorial");
    const cells = cellsOf(rowFor("Family memorial"));
    expect(cells[HEADERS.indexOf("Visibility")]).toHaveTextContent(/private/i);
    expect(within(cells[HEADERS.indexOf("Viewer link")])
      .getByRole("button", { name: "Copy viewer link for Family memorial" })).toBeInTheDocument();
  });

  it("copies through the unchanged viewer-link logic, with the event it belongs to", async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByText("Family memorial");
    await user.click(screen.getByRole("button", { name: "Copy viewer link for Family memorial" }));
    await waitFor(() => expect(copyViewerLink).toHaveBeenCalledTimes(1));
    expect(copyViewerLink).toHaveBeenCalledWith(EVENTS[1]);
    expect(notify.success).toHaveBeenCalledWith("Viewer link copied");
  });

  it("reports a copy that failed", async () => {
    vi.mocked(copyViewerLink).mockResolvedValueOnce(false);
    const user = userEvent.setup();
    renderPage();
    await screen.findByText("Testt");
    await user.click(screen.getByRole("button", { name: "Copy viewer link for Testt" }));
    await waitFor(() => expect(notify.error).toHaveBeenCalledWith("Unable to copy viewer link."));
  });

  it("keeps sorting: Event, Status and Starts sort on the server; the new columns do not sort", async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByText("Testt");
    for (const name of ["Event", "Status", "Starts"]) {
      expect(within(screen.getByRole("columnheader", { name: new RegExp(`^${name}`) }))
        .getByRole("button")).toBeInTheDocument();
    }
    for (const name of ["Viewer link", "Actions"]) {
      expect(within(screen.getByRole("columnheader", { name })).queryByRole("button")).not.toBeInTheDocument();
    }
    await user.click(screen.getByRole("button", { name: /^Event/ }));
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
