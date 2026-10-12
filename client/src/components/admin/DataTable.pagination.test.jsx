// Server-driven pagination, pinned at the size that broke it: Identity & Access had 2,173
// accounts at 50 a page, and DataTable drew all 44 page buttons in one row, past the card's
// right edge. These assert the row stays bounded however many pages there are, and that it
// still reaches every page.
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import DataTable from "./DataTable";

const columns = [{ key: "name", header: "Name" }];
const rows = [{ id: 1, name: "Row" }];

function renderPaged(page, pageCount, onPageChange = vi.fn()) {
  render(
    <DataTable
      columns={columns}
      rows={rows}
      rowKey={(r) => r.id}
      pageSize={50}
      serverPage={page}
      serverPageCount={pageCount}
      serverTotal={pageCount * 50}
      onPageChange={onPageChange}
    />
  );
  return onPageChange;
}

const pageNumbers = () =>
  screen.getAllByRole("button", { name: /^\d+$/ }).map((b) => Number(b.textContent));

describe("DataTable pagination", () => {
  it("draws a bounded window, not one button per page", () => {
    renderPaged(1, 44);
    expect(pageNumbers()).toEqual([1, 2, 44]);
  });

  it("keeps first, last and the current page's neighbours in the middle of a long range", () => {
    renderPaged(20, 44);
    expect(pageNumbers()).toEqual([1, 19, 20, 21, 44]);
  });

  it("shows every page when there are few, with no ellipsis", () => {
    const { container } = render(
      <DataTable columns={columns} rows={rows} rowKey={(r) => r.id} pageSize={50}
                 serverPage={2} serverPageCount={5} serverTotal={250} onPageChange={vi.fn()} />
    );
    expect(pageNumbers()).toEqual([1, 2, 3, 4, 5]);
    expect(container.textContent).not.toContain("…");
  });

  it("marks only the current page active", () => {
    renderPaged(20, 44);
    const current = screen.getAllByRole("button", { current: "page" });
    expect(current).toHaveLength(1);
    expect(current[0]).toHaveTextContent("20");
  });

  it("still navigates by number and by previous/next", async () => {
    const onPageChange = renderPaged(20, 44);
    await userEvent.click(screen.getByRole("button", { name: "44" }));
    await userEvent.click(screen.getByRole("button", { name: "Previous page" }));
    await userEvent.click(screen.getByRole("button", { name: "Next page" }));
    expect(onPageChange.mock.calls.map((c) => c[0])).toEqual([44, 19, 21]);
  });

  it("drops the current page's neighbours on a phone so the row cannot wrap", () => {
    const original = window.matchMedia;
    window.matchMedia = (query) => ({
      matches: query === "(max-width: 639.98px)", media: query,
      addEventListener() {}, removeEventListener() {},
    });
    try {
      renderPaged(20, 44);
      expect(pageNumbers()).toEqual([1, 20, 44]);
      expect(screen.getAllByRole("button", { current: "page" })[0]).toHaveTextContent("20");
    } finally {
      window.matchMedia = original;
    }
  });

  it("disables previous on the first page and next on the last", () => {
    renderPaged(44, 44);
    expect(screen.getByRole("button", { name: "Next page" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Previous page" })).toBeEnabled();
  });
});
