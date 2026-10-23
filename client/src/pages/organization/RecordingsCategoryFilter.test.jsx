// The Recordings category filter offers the categories the listed recordings really have
// (their events' Event.category, from GET /organization/recordings) — each once — and is not
// shown at all when there is nothing to filter by. It used to render a dropdown whose only
// option was "All categories" for a library with no recordings.
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
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
import { ALL_CATEGORIES, UNCATEGORIZED, categoryOptions, matchesCategory } from "./recordingCategories";

const rec = (id, title, category, over = {}) => ({
  id, event_id: `e-${id}`, title, category, started_at: "2026-09-27T10:00:00Z",
  duration_seconds: 3600, size_bytes: 1024 ** 3, url: `https://signed.example/${id}.mp4`,
  state: "ready", legal_hold: false, validation_status: null, ...over,
});
const SUMMARY = { library_total: 0, in_progress: 0, failed_attempts: 0, latest_failure: null };

const serve = (list) =>
  vi.mocked(api.get).mockImplementation((url) => {
    if (url === "/organization/recordings") return Promise.resolve({ data: list });
    if (url === "/organization/recordings/summary") return Promise.resolve({ data: SUMMARY });
    return Promise.resolve({ data: [] });
  });

const show = () =>
  render(
    <ThemeProvider>
      <MemoryRouter>
        <Recordings />
      </MemoryRouter>
    </ThemeProvider>,
  );

const filter = () => screen.queryByRole("combobox", { name: "Filter by category" });
const optionLabels = () => within(filter()).getAllByRole("option").map((o) => o.textContent);
// The library table — the newest recordings also appear under Recently captured.
const lib = () => within(screen.getByRole("region", { name: "Recording library" }));
const titlesShown = (titles) => titles.filter((t) => lib().queryByText(t));

beforeEach(() => vi.clearAllMocks());

const LIBRARY = [
  rec("r1", "Town hall", "Company"),
  rec("r2", "Quarterly webinar", "Webinar"),
  rec("r3", "Product webinar", " webinar "),          // same category, other spelling
  rec("r4", "All-hands", "company"),
];
const TITLES = LIBRARY.map((r) => r.title);

describe("when there is nothing to filter by", () => {
  it("shows no category filter for a library with no recordings", async () => {
    serve([]);
    show();
    expect(await screen.findByText(/No recordings yet/)).toBeInTheDocument();
    expect(filter()).toBeNull();
    // The rest of the toolbar is untouched.
    expect(screen.getByRole("combobox", { name: "Sort recordings" })).toBeInTheDocument();
    expect(screen.getByPlaceholderText("Search recordings...")).toBeInTheDocument();
  });

  it("shows no category filter when no recording has a category", async () => {
    serve([rec("r1", "Untitled one", null), rec("r2", "Untitled two", "   ")]);
    show();
    await lib().findByText("Untitled one");
    expect(filter()).toBeNull();
  });
});

describe("the categories offered", () => {
  it("are the real ones, each once, after All categories", async () => {
    serve(LIBRARY);
    show();
    await lib().findByText("Town hall");
    expect(optionLabels()).toEqual(["All categories", "Company", "Webinar"]);
    expect(filter()).toHaveValue(ALL_CATEGORIES);
  });

  it("include Uncategorized only when some recordings have no category", async () => {
    serve([...LIBRARY, rec("r5", "Loose recording", null)]);
    show();
    await lib().findByText("Loose recording");
    expect(optionLabels()).toEqual(["All categories", "Company", "Webinar", "Uncategorized"]);
  });
});

describe("filtering", () => {
  it("shows only the chosen category, and All categories resets it", async () => {
    const user = userEvent.setup();
    serve(LIBRARY);
    show();
    await lib().findByText("Town hall");
    await user.selectOptions(filter(), "webinar");
    await waitFor(() => expect(titlesShown(TITLES)).toEqual(["Quarterly webinar", "Product webinar"]));
    await user.selectOptions(filter(), ALL_CATEGORIES);
    await waitFor(() => expect(titlesShown(TITLES)).toEqual(TITLES));
  });

  it("finds the uncategorized recordings", async () => {
    const user = userEvent.setup();
    serve([...LIBRARY, rec("r5", "Loose recording", null)]);
    show();
    await lib().findByText("Loose recording");
    await user.selectOptions(filter(), UNCATEGORIZED);
    await waitFor(() => expect(titlesShown([...TITLES, "Loose recording"])).toEqual(["Loose recording"]));
  });

  it("combines with search, and says so when nothing matches both", async () => {
    const user = userEvent.setup();
    serve(LIBRARY);
    show();
    await lib().findByText("Town hall");
    await user.selectOptions(filter(), "company");
    await user.type(screen.getByPlaceholderText("Search recordings..."), "all-hands");
    await waitFor(() => expect(titlesShown(TITLES)).toEqual(["All-hands"]));
    await user.clear(screen.getByPlaceholderText("Search recordings..."));
    await user.type(screen.getByPlaceholderText("Search recordings..."), "webinar");
    expect(await screen.findByText("No recordings match your filters.")).toBeInTheDocument();
  });
});

describe("the category helpers", () => {
  it("merge spellings, skip blanks and sort", () => {
    expect(categoryOptions([
      { category: "Webinar" }, { category: " webinar" }, { category: "Company" },
      { category: null }, { category: "" }, { category: "  " }, { category: 42 }, {},
    ])).toEqual([
      { value: "company", label: "Company" },
      { value: "webinar", label: "Webinar" },
      { value: UNCATEGORIZED, label: "Uncategorized" },
    ]);
    expect(categoryOptions([])).toEqual([]);
    expect(categoryOptions([{ category: null }])).toEqual([]);
  });

  it("match a recording to the selection", () => {
    expect(matchesCategory({ category: " WEBINAR " }, "webinar")).toBe(true);
    expect(matchesCategory({ category: "Company" }, "webinar")).toBe(false);
    expect(matchesCategory({ category: null }, UNCATEGORIZED)).toBe(true);
    expect(matchesCategory({ category: "Company" }, UNCATEGORIZED)).toBe(false);
    expect(matchesCategory({ category: "Company" }, ALL_CATEGORIES)).toBe(true);
  });
});
