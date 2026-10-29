import { fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";

vi.mock("../../api", async (importOriginal) => {
  const real = await importOriginal();
  return { ...real, default: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() } };
});
vi.mock("../../ui/Toast", () => ({
  notify: { error: vi.fn(), success: vi.fn(), alert: vi.fn(), warning: vi.fn(), info: vi.fn() },
}));
vi.mock("../../auth/AuthContext", () => ({
  useAuth: () => ({ user: { full_name: "Ada Admin", role: "org_admin" }, logout: vi.fn() }),
}));

import api from "../../api";
import { ThemeProvider } from "../../theme/ThemeContext";
import Recordings from "./Recordings";

const SUMMARY = { library_total: 0, in_progress: 0, failed_attempts: 0, latest_failure: null };

const serve = (list = []) =>
  vi.mocked(api.get).mockImplementation((url) => {
    if (url === "/organization/recordings") return Promise.resolve({ data: list });
    if (url === "/organization/recordings/summary") return Promise.resolve({ data: SUMMARY });
    return Promise.resolve({ data: [] });
  });

function renderPage(initialEntries = ["/organization/recordings"]) {
  return render(
    <ThemeProvider>
      <MemoryRouter initialEntries={initialEntries}>
        <Recordings />
      </MemoryRouter>
    </ThemeProvider>
  );
}

beforeEach(() => vi.clearAllMocks());

describe("Recording library dropdown contrast and accessibility", () => {
  it("renders state filter as native select with theme-aware option colors", async () => {
    serve([]);
    renderPage();

    const select = await screen.findByRole("combobox", { name: "Filter by state" });
    expect(select.tagName).toBe("SELECT");

    // The select control carries descendant option styling for resilient fallback
    expect(select.className).toContain("[&>option]:bg-white");
    expect(select.className).toContain("[&>option]:text-slate-800");
    expect(select.className).toContain("dark:[&>option]:bg-slate-900");
    expect(select.className).toContain("dark:[&>option]:text-slate-100");

    const options = within(select).getAllByRole("option");
    expect(options.map((o) => [o.getAttribute("value"), o.textContent.trim()])).toEqual([
      ["all", "All states"],
      ["ready", "Replay-ready"],
      ["processing", "Processing"],
      ["attention", "Needs attention"],
    ]);

    // Every option must explicitly carry background and foreground classes for both themes
    for (const opt of options) {
      expect(opt.className).toContain("bg-white");
      expect(opt.className).toContain("text-slate-800");
      expect(opt.className).toContain("dark:bg-slate-900");
      expect(opt.className).toContain("dark:text-slate-100");
    }
  });

  it("updates selected filter value and maintains readable state", async () => {
    serve([]);
    renderPage();

    const select = await screen.findByRole("combobox", { name: "Filter by state" });
    expect(select.value).toBe("all");

    fireEvent.change(select, { target: { value: "ready" } });
    expect(select.value).toBe("ready");

    fireEvent.change(select, { target: { value: "processing" } });
    expect(select.value).toBe("processing");

    fireEvent.change(select, { target: { value: "attention" } });
    expect(select.value).toBe("attention");
  });

  it("renders sort dropdown options with the same explicit contrast classes", async () => {
    serve([]);
    renderPage();

    const sortSelect = await screen.findByRole("combobox", { name: "Sort recordings" });
    expect(sortSelect.tagName).toBe("SELECT");

    const options = within(sortSelect).getAllByRole("option");
    for (const opt of options) {
      expect(opt.className).toContain("bg-white");
      expect(opt.className).toContain("text-slate-800");
      expect(opt.className).toContain("dark:bg-slate-900");
      expect(opt.className).toContain("dark:text-slate-100");
    }
  });

  it("renders category dropdown options with explicit contrast classes when categories exist", async () => {
    serve([
      {
        id: "r1",
        event_id: "e1",
        title: "All Hands",
        category: "Internal",
        started_at: "2026-10-01T10:00:00Z",
        duration_seconds: 3600,
        size_bytes: 1024,
        url: "https://example.com/r1.mp4",
        state: "ready",
      },
    ]);
    renderPage();

    const catSelect = await screen.findByRole("combobox", { name: "Filter by category" });
    expect(catSelect).toBeInTheDocument();

    const options = within(catSelect).getAllByRole("option");
    expect(options.length).toBeGreaterThan(1);
    for (const opt of options) {
      expect(opt.className).toContain("bg-white");
      expect(opt.className).toContain("text-slate-800");
      expect(opt.className).toContain("dark:bg-slate-900");
      expect(opt.className).toContain("dark:text-slate-100");
    }
  });
});

